#!/usr/bin/env python3
"""merge_to_disk.py — 把 gap 索引(增量)合并进主索引,并清空 gap

kb_search.py 的 _index.merge_from(_gap) 只发生在内存里。本脚本把结果写回磁盘。

故障注入的四段 (PAPERKB_FAULT 未设置时不触发; checks/evidence.py 会测):
  1. memory_merge  内存合并之后、写主文件之前
  2. index_swap    os.replace 主索引之后、替换 ids 之前
  3. ids_swap      两个主文件都替换之后、清空 gap 之前
  4. clear_gap     删掉 gap 索引之后、删掉 gap ids 之前

单次 os.replace 在同一目录里是原子的。两次替换之间若被 SIGKILL, 主索引和
ids 会短暂不一致; 下一次启动读 merge.journal, 能补完就补完, 否则拷回安全副本。
被 except 抓住的失败会拷回合并前的字节; clear_gap 阶段 gap 索引可能已经删掉,
那时改为把合并收完, 而不是丢掉已经写好的主索引。

--no-backup 不保留时间戳备份, 但仍会写临时 rollback 副本, 成功后删除。
Phase 4 调用本仓库的 kb_health.py, 并把 PES2O_KB_DIR 设成数据目录。

用法:
  py -3 merge_to_disk.py            # 完整执行
  py -3 merge_to_disk.py --dry-run  # 只跑 Phase 0/1 (只读, 不写盘)
  py -3 merge_to_disk.py --no-backup

退出码:
  0 = 合并成功, 或启动时把上次已写完的合并收尾成功
  1 = 只读校验失败, 主文件未改
  2 = 写入阶段失败。except 路径会把主文件收成一致状态; SIGKILL 要等下次启动
"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

# 默认仍是作者本机 Windows 路径。夹具和 Linux 用 PES2O_KB_DIR 指向数据目录
# （与 dedup_and_reindex.py 同一个环境变量）。
KB_DIR = Path(os.environ.get('PES2O_KB_DIR', r'E:/peS2o_kb_faiss'))
FAISS_INDEX = KB_DIR / 'papers.index'
FAISS_GAP_INDEX = KB_DIR / 'papers_gap.index'
IDS_FILE = KB_DIR / 'paper_ids.txt'
IDS_GAP_FILE = KB_DIR / 'papers_gap_ids.txt'
BACKUP_DIR = KB_DIR / 'rebuild_backups'
# 提交标记。进程在两次 os.replace 之间被 SIGKILL 时，下一次启动靠它收尾。
JOURNAL = KB_DIR / 'merge.journal'
STAGING_INDEX = KB_DIR / 'papers.index.staging'
STAGING_IDS = KB_DIR / 'paper_ids.txt.staging'
# --no-backup 仍会写这对临时副本，提交成功后删除。带时间戳的 backup 会保留。
ROLLBACK_INDEX = KB_DIR / 'papers.index.rollback'
ROLLBACK_IDS = KB_DIR / 'paper_ids.txt.rollback'

# 单条日志打印函数 (走 stderr, 不污染可能的 stdout 调用方)
def log(msg: str) -> None:
    ts = datetime.now().strftime('%H:%M:%S')
    print(f'[{ts}] {msg}', file=sys.stderr, flush=True)


def _fault(stage: str) -> None:
    """测试缝。未设置 PAPERKB_FAULT 时什么都不做。

    值形如 ``index_swap:raise`` 或 ``index_swap:kill``。
    kill 使用 SIGKILL，本进程来不及跑 except。
    """
    spec = os.environ.get('PAPERKB_FAULT', '').strip()
    if not spec:
        return
    want, _, mode = spec.partition(':')
    if want != stage:
        return
    log(f'PAPERKB_FAULT {stage}:{mode}')
    if mode == 'kill':
        sys.stderr.flush()
        os.kill(os.getpid(), signal.SIGKILL)
    if mode == 'raise':
        raise RuntimeError(f'injected fault at {stage}')
    raise RuntimeError(f'unknown PAPERKB_FAULT mode {mode!r}')


def _read_ids(path: Path) -> list[str]:
    if not path.exists():
        return []
    with open(path, encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip()]


def _ntotal(path: Path) -> int:
    import faiss
    return int(faiss.read_index(str(path)).ntotal)


def _remove(path: Path) -> None:
    if path.exists():
        path.unlink()


def _write_journal(payload: dict) -> None:
    raw = json.dumps(payload).encode('utf-8')
    tmp = JOURNAL.with_suffix('.tmp')
    with open(tmp, 'wb') as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, JOURNAL)


def _restore_from(src_index: Path, src_ids: Path) -> None:
    """把安全副本拷回主文件。不移动 src，时间戳备份因此还在。"""
    tmp_index = KB_DIR / 'papers.index.restore'
    tmp_ids = KB_DIR / 'paper_ids.txt.restore'
    shutil.copy2(src_index, tmp_index)
    shutil.copy2(src_ids, tmp_ids)
    os.replace(tmp_index, FAISS_INDEX)
    os.replace(tmp_ids, IDS_FILE)


def _drop_temp_rollback() -> None:
    _remove(ROLLBACK_INDEX)
    _remove(ROLLBACK_IDS)
    _remove(STAGING_INDEX)
    _remove(STAGING_IDS)


def _finish_committed(new_n: int) -> None:
    if _ntotal(FAISS_INDEX) != new_n or len(_read_ids(IDS_FILE)) != new_n:
        raise RuntimeError(
            'refusing to drop gap files: main index and ids are not the merged size')
    phase3_clear_gap(honor_fault=False)
    _remove(JOURNAL)
    _drop_temp_rollback()


def recover_if_needed(prefer: str) -> str:
    """收尾一次被打断的合并。

    prefer='finish'：下次启动，能补完已落盘的一半就补完。
    prefer='rollback'：本次 except 里，只要 gap 文件还在就把主文件拷回合并前。

    返回 'none' | 'byte_identical' | 'fully_merged'。
    fully_merged 表示主索引和 id 都是合并后的内容，且 gap 文件已删除。
    """
    if not JOURNAL.exists():
        _drop_temp_rollback()
        return 'none'

    payload = json.loads(JOURNAL.read_text(encoding='utf-8'))
    old_n = int(payload['old_n'])
    new_n = int(payload['new_n'])
    phase = payload['phase']
    src_index = Path(payload['rollback_index'])
    src_ids = Path(payload['rollback_ids'])
    idx_n = _ntotal(FAISS_INDEX) if FAISS_INDEX.exists() else -1
    ids_n = len(_read_ids(IDS_FILE))
    both_new = idx_n == new_n and ids_n == new_n
    both_old = idx_n == old_n and ids_n == old_n
    gap_index_gone = not FAISS_GAP_INDEX.exists()

    if phase == 'swapped' or (gap_index_gone and both_new):
        _finish_committed(new_n)
        return 'fully_merged'

    if prefer == 'rollback':
        if not src_index.exists() or not src_ids.exists():
            raise RuntimeError('cannot roll back: safety copies missing')
        _restore_from(src_index, src_ids)
        _remove(JOURNAL)
        _drop_temp_rollback()
        return 'byte_identical'

    if both_new:
        _finish_committed(new_n)
        return 'fully_merged'
    if both_old:
        _remove(JOURNAL)
        _drop_temp_rollback()
        return 'byte_identical'
    if idx_n == new_n and ids_n == old_n and len(_read_ids(STAGING_IDS)) == new_n:
        os.replace(STAGING_IDS, IDS_FILE)
        _finish_committed(new_n)
        return 'fully_merged'
    if src_index.exists() and src_ids.exists():
        _restore_from(src_index, src_ids)
        _remove(JOURNAL)
        _drop_temp_rollback()
        return 'byte_identical'
    raise RuntimeError(
        f'unrecoverable merge journal phase={phase} idx_n={idx_n} ids_n={ids_n}')


def phase0_prepare() -> dict:
    """准备阶段: 检查文件存在性 / mtime 关系 / 磁盘空间"""
    log('══ Phase 0: 准备阶段 (只读) ══')

    if not FAISS_INDEX.exists():
        raise FileNotFoundError(f'main 索引不存在: {FAISS_INDEX}')
    if not FAISS_GAP_INDEX.exists():
        raise FileNotFoundError(f'gap 索引不存在: {FAISS_GAP_INDEX}, 无需 merge')
    if not IDS_FILE.exists():
        raise FileNotFoundError(f'main ids 文件不存在: {IDS_FILE}')
    if not IDS_GAP_FILE.exists():
        raise FileNotFoundError(f'gap ids 文件不存在: {IDS_GAP_FILE}')

    main_mtime = FAISS_INDEX.stat().st_mtime
    gap_mtime = FAISS_GAP_INDEX.stat().st_mtime
    log(f'  main.index mtime: {datetime.fromtimestamp(main_mtime)}')
    log(f'  gap.index  mtime: {datetime.fromtimestamp(gap_mtime)}')
    log(f'  gap 比 main 新 {gap_mtime - main_mtime:.0f} s  '
        f'({(gap_mtime-main_mtime)/3600:.1f} h)')

    if gap_mtime <= main_mtime:
        log('  ⚠ gap 比 main 还旧, 可能已被消化, 跳过 merge')
        log('  提示: 跑一次 kb_search.py 看是否还能召回 gap 的内容, '
            '如果能, 说明 main mtime 没更新 (脚本问题), 请排查')
        sys.exit(1)

    # 磁盘空间: staging(≈ main+gap) + 一份安全副本(≈ main)。
    # --no-backup 也会写临时副本，提交成功后删掉。
    main_size = FAISS_INDEX.stat().st_size
    gap_size = FAISS_GAP_INDEX.stat().st_size
    expected_new = main_size + gap_size  # IndexFlatIP 是 raw 向量, 合并后大小相加
    need_free = expected_new + main_size
    total, used, free = shutil.disk_usage(KB_DIR)
    log(f'  main.index size: {main_size/1024**2:.1f} MB')
    log(f'  gap.index  size: {gap_size/1024**2:.1f} MB')
    log(f'  预期合并后: {expected_new/1024**2:.1f} MB')
    log(f'  需要可用空间: {need_free/1024**2:.1f} MB, 当前: {free/1024**2:.1f} MB')
    if free < need_free:
        raise RuntimeError(
            f'磁盘空间不足: 需 {need_free/1024**2:.1f} MB, '
            f'仅 {free/1024**2:.1f} MB 可用')

    # gap 向量数预警
    import faiss
    n_main = faiss.read_index(str(FAISS_INDEX)).ntotal
    n_gap = faiss.read_index(str(FAISS_GAP_INDEX)).ntotal
    log(f'  main 向量数: {n_main:,}')
    log(f'  gap  向量数: {n_gap:,}')
    log(f'  合并后预计: {n_main + n_gap:,}')

    if n_gap > 500_000:
        log('  ⚠ gap 向量数超过 50 万, 建议分批 merge (本次仍继续)')

    return {
        'main_mtime': main_mtime, 'gap_mtime': gap_mtime,
        'n_main': n_main, 'n_gap': n_gap,
        'main_size': main_size, 'gap_size': gap_size,
    }


def phase1_load_and_merge(stats: dict) -> tuple[object, list[str]]:
    """加载 + 内存合并 (只读)"""
    log('══ Phase 1: 加载 + 内存合并 (只读) ══')
    import faiss
    t0 = time.time()
    log('  加载 main.index ...')
    idx_main = faiss.read_index(str(FAISS_INDEX))
    log(f'  加载 gap.index ({stats["n_gap"]:,} vecs) ...')
    idx_gap = faiss.read_index(str(FAISS_GAP_INDEX))

    log('  in-memory merge_from() ...')
    idx_main.merge_from(idx_gap)
    log(f'  合并后 ntotal = {idx_main.ntotal:,}  '
        f'(预期 {stats["n_main"] + stats["n_gap"]:,})')
    assert idx_main.ntotal == stats['n_main'] + stats['n_gap'], \
        '合并后 ntotal 与预期不一致!'

    log('  加载 paper_ids.txt (main) ...')
    with open(IDS_FILE, encoding='utf-8') as f:
        ids_main = [line.strip() for line in f if line.strip()]
    log('  加载 papers_gap_ids.txt (gap) ...')
    with open(IDS_GAP_FILE, encoding='utf-8') as f:
        ids_gap = [line.strip() for line in f if line.strip()]

    log(f'  main ids: {len(ids_main):,}, gap ids: {len(ids_gap):,}')
    assert len(ids_main) == stats['n_main'], \
        f'main ids 数量 {len(ids_main)} 与 FAISS ntotal {stats["n_main"]} 不符'
    assert len(ids_gap) == stats['n_gap'], \
        f'gap ids 数量 {len(ids_gap)} 与 FAISS ntotal {stats["n_gap"]} 不符'

    merged_ids = ids_main + ids_gap
    log(f'  合并后 ids: {len(merged_ids):,}  '
        f'(预期 {stats["n_main"] + stats["n_gap"]:,})')

    log(f'  Phase 1 耗时: {time.time()-t0:.1f}s')
    _fault('memory_merge')
    return idx_main, merged_ids


def phase2_backup_and_swap(idx_merged, merged_ids: list[str],
                           stats: dict, do_backup: bool) -> None:
    """写盘: backup → staging → atomic swap → 追加 ids"""
    log('══ Phase 2: backup + atomic swap (写) ══')
    t0 = time.time()

    # ── 2a. 备份当前 main (如果启用) ──
    if do_backup:
        BACKUP_DIR.mkdir(exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_index = BACKUP_DIR / f'papers_{ts}.index'
        backup_ids = BACKUP_DIR / f'paper_ids_{ts}.txt'
        log(f'  备份 main → {backup_index.name}')
        shutil.copy2(str(FAISS_INDEX), str(backup_index))
        log(f'  备份 ids  → {backup_ids.name}')
        shutil.copy2(str(IDS_FILE), str(backup_ids))
        log(f'  备份完成, 大小: '
            f'{(backup_index.stat().st_size + backup_ids.stat().st_size)/1024**2:.1f} MB')
    else:
        log('  --no-backup: 不保留时间戳备份; 仍写临时 rollback 副本, 提交成功后删除')

    # 安全副本必须在第一次 os.replace 之前落盘。
    if do_backup:
        safety_index = backup_index
        safety_ids = backup_ids
    else:
        log('  写临时 rollback 副本 ...')
        shutil.copy2(FAISS_INDEX, ROLLBACK_INDEX)
        shutil.copy2(IDS_FILE, ROLLBACK_IDS)
        safety_index = ROLLBACK_INDEX
        safety_ids = ROLLBACK_IDS

    # ── 2b. 写到 staging 文件 ──
    import faiss
    log(f'  写 staging: {STAGING_INDEX.name} ...')
    faiss.write_index(idx_merged, str(STAGING_INDEX))
    log(f'  staging.index size: '
        f'{STAGING_INDEX.stat().st_size/1024**2:.1f} MB')

    log(f'  写 staging: {STAGING_IDS.name} ...')
    with open(STAGING_IDS, 'w', encoding='utf-8') as f:
        for pid in merged_ids:
            f.write(pid + '\n')
    log(f'  staging.ids  size: '
        f'{STAGING_IDS.stat().st_size/1024**2:.1f} MB')

    expect = stats['n_main'] + stats['n_gap']
    if (faiss.read_index(str(STAGING_INDEX)).ntotal != expect
            or len(_read_ids(STAGING_IDS)) != expect):
        raise RuntimeError('staging pair failed verification')

    _write_journal({
        'phase': 'swapping',
        'old_n': stats['n_main'],
        'new_n': expect,
        'rollback_index': str(safety_index),
        'rollback_ids': str(safety_ids),
    })

    # ── 2c. 两次 os.replace。单次替换是原子的，两次之间不是。──
    log('  atomic swap: papers.index ...')
    os.replace(str(STAGING_INDEX), str(FAISS_INDEX))
    _fault('index_swap')
    log('  atomic swap: paper_ids.txt ...')
    os.replace(str(STAGING_IDS), str(IDS_FILE))
    _fault('ids_swap')
    log(f'  Phase 2 耗时: {time.time()-t0:.1f}s')

    n_new = faiss.read_index(str(FAISS_INDEX)).ntotal
    n_ids = len(_read_ids(IDS_FILE))
    log(f'  写盘验证: main.ntotal = {n_new:,} ids = {n_ids:,}  '
        f'(预期 {expect:,})')
    if n_new != expect or n_ids != expect:
        raise RuntimeError('写盘后主索引与 ids 不一致')
    _write_journal({
        'phase': 'swapped',
        'old_n': stats['n_main'],
        'new_n': expect,
        'rollback_index': str(safety_index),
        'rollback_ids': str(safety_ids),
    })


def phase3_clear_gap(*, honor_fault: bool = False) -> None:
    """清空 gap 文件 (写)。honor_fault 只给正常提交流程用。"""
    log('══ Phase 3: 清空 gap 文件 (写) ══')
    if FAISS_GAP_INDEX.exists():
        size = FAISS_GAP_INDEX.stat().st_size
        FAISS_GAP_INDEX.unlink()
        log(f'  删除 {FAISS_GAP_INDEX.name} ({size/1024**2:.1f} MB)')
    else:
        log(f'  {FAISS_GAP_INDEX.name} 已不存在, 跳过')

    if honor_fault:
        _fault('clear_gap')

    if IDS_GAP_FILE.exists():
        size = IDS_GAP_FILE.stat().st_size
        IDS_GAP_FILE.unlink()
        log(f'  删除 {IDS_GAP_FILE.name} ({size/1024**2:.1f} MB)')
    else:
        log(f'  {IDS_GAP_FILE.name} 已不存在, 跳过')


def phase4_verify() -> int:
    """验证: 再次跑 kb_health 看 DANGER 是否消失"""
    log('══ Phase 4: 验证 (只读) ══')
    import subprocess
    health = Path(__file__).resolve().parent / 'kb_health.py'
    log(f'  跑 {health.name} --sample 0 (PES2O_KB_DIR={KB_DIR}) ...')
    env = os.environ.copy()
    env['PES2O_KB_DIR'] = str(KB_DIR)
    env.pop('PAPERKB_FAULT', None)
    result = subprocess.run(
        [sys.executable, str(health), '--sample', '0'],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        cwd=str(health.parent),
        env=env,
    )
    log(f'  kb_health exit code: {result.returncode}')

    # 解析关键行
    output = result.stdout + result.stderr
    for keyword in ('[5b]', 'Verdict:', 'Merge:', 'status:'):
        for line in output.splitlines():
            if keyword in line:
                log(f'    {line.strip()[:120]}')
                break

    if result.returncode == 0:
        log('  ✅ DANGER 已消除, KB 恢复 HEALTHY')
    elif result.returncode == 1:
        log('  ⚠ WARN 状态 (gap 已清, 但可能还有其他维度 WARN)')
    else:
        log('  🚨 仍有 DANGER, 请检查上方日志')
    return result.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true',
                    help='只跑 Phase 0/1 (只读校验), 不写盘')
    ap.add_argument('--no-backup', action='store_true',
                    help='不保留时间戳备份。临时 rollback 副本仍会写, 成功后删除')
    ap.add_argument('--skip-verify', action='store_true',
                    help='跳过 Phase 4 验证')
    args = ap.parse_args()

    log('═══════════════════════════════════════════════════════════')
    log('  merge_to_disk.py — 把 gap 索引合并到主索引')
    log(f'  KB 目录: {KB_DIR}')
    log(f'  模式: {"DRY-RUN (只读)" if args.dry_run else "FULL"}')
    log('═══════════════════════════════════════════════════════════')

    t_total = time.time()

    try:
        recovered = recover_if_needed('finish')
    except Exception as e:
        log(f'❌ 恢复未完成的合并失败: {e}')
        sys.exit(2)
    if recovered == 'fully_merged':
        log('恢复: 上次合并已经写完主索引, 本次补清了 gap 并退出')
        sys.exit(0)
    if recovered == 'byte_identical':
        log('恢复: 主索引已回到合并前的字节, 继续尝试合并')

    try:
        stats = phase0_prepare()
    except Exception as e:
        log(f'❌ Phase 0 失败: {e}')
        sys.exit(1)

    try:
        idx_merged, merged_ids = phase1_load_and_merge(stats)
    except Exception as e:
        log(f'❌ Phase 1 失败: {e}')
        sys.exit(1)

    if args.dry_run:
        log('══ DRY-RUN 完成, 不执行写盘 ══')
        log(f'  合并后预计: {stats["n_main"] + stats["n_gap"]:,} vecs')
        log(f'  总耗时: {time.time()-t_total:.1f}s')
        sys.exit(0)

    try:
        phase2_backup_and_swap(idx_merged, merged_ids, stats,
                                do_backup=not args.no_backup)
    except Exception as e:
        log(f'❌ Phase 2 失败: {e}')
        try:
            outcome = recover_if_needed('rollback')
            log(f'  Phase 2 修复结果: {outcome}')
        except Exception as repair_error:
            log(f'  Phase 2 修复失败: {repair_error}')
        sys.exit(2)

    try:
        phase3_clear_gap(honor_fault=True)
    except Exception as e:
        log(f'❌ Phase 3 失败: {e}')
        try:
            outcome = recover_if_needed('rollback')
            log(f'  Phase 3 修复结果: {outcome}')
        except Exception as repair_error:
            log(f'  Phase 3 修复失败: {repair_error}')
        sys.exit(2)

    _remove(JOURNAL)
    _drop_temp_rollback()

    log(f'✅ 合并完成, 总耗时: {time.time()-t_total:.1f}s')
    log(f'   main: {stats["n_main"]:,} + gap: {stats["n_gap"]:,} '
        f'= {stats["n_main"] + stats["n_gap"]:,} vecs')

    if not args.skip_verify:
        verify_exit = phase4_verify()
        sys.exit(0 if verify_exit in (0, 1) else 2)

    sys.exit(0)


if __name__ == '__main__':
    main()
