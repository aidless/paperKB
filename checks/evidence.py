#!/usr/bin/env python3
"""Offline evidence for merge faults, health exit codes, and search degradation.

Run from the repo root:

    python3 checks/evidence.py

Prints one JSON object. Exits 1 if a contract assertion fails.
``--probe-timeout`` is an internal child process; ``PES2O_KB_DIR`` must already
point at a fixture.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import faiss
import numpy as np

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

DIM = 8
N_MAIN = 8
N_GAP = 4
STAGES = ('memory_merge', 'index_swap', 'ids_swap', 'clear_gap')
SIDECARS = (
    'merge.journal',
    'merge.tmp',
    'papers.index.staging',
    'paper_ids.txt.staging',
    'papers.index.rollback',
    'paper_ids.txt.rollback',
    'papers.index.restore',
    'paper_ids.txt.restore',
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _vectors(n: int, dim: int) -> np.ndarray:
    out = np.zeros((n, dim), dtype=np.float32)
    for i in range(n):
        out[i, i % dim] = np.float32(1.0)
    return out


def _write_index(path: Path, vecs: np.ndarray) -> None:
    index = faiss.IndexFlatIP(vecs.shape[1])
    index.add(vecs)
    faiss.write_index(index, str(path))


def _write_ids(path: Path, ids: list[str]) -> None:
    path.write_text(''.join(f'{item}\n' for item in ids), encoding='utf-8')


def _write_sqlite(path: Path, ids: list[str]) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        '''CREATE TABLE papers (
            paper_id TEXT PRIMARY KEY,
            title TEXT,
            fields TEXT,
            text_prefix TEXT,
            abstract TEXT,
            created TEXT,
            year TEXT,
            categories TEXT,
            authors TEXT,
            source TEXT,
            version TEXT
        )'''
    )
    rows = [
        (pid, f'title {pid}', '', f'abstract {pid}', f'abstract {pid}',
         '2024-01-01', '2024', 'cs.LG', '[]', 'fixture', '')
        for pid in ids
    ]
    conn.executemany(
        '''INSERT INTO papers
           (paper_id, title, fields, text_prefix, abstract, created, year,
            categories, authors, source, version)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        rows,
    )
    conn.commit()
    conn.close()


def _age(path: Path, hours: float | None) -> None:
    if hours is None or not path.exists():
        return
    stamp = time.time() - hours * 3600.0
    os.utime(path, (stamp, stamp))


def build_kb(
    root: Path,
    *,
    n_main: int = N_MAIN,
    n_gap: int = N_GAP,
    main_age_h: float | None = 1.0,
    gap_age_h: float | None = 0.0,
    daily_grow_hours: float | None = 1.0,
    sqlite_mode: str = 'aligned',
) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    main_ids = [f'm{i}' for i in range(n_main)]
    gap_ids = [f'g{i}' for i in range(n_gap)]
    main_vecs = _vectors(n_main, DIM)
    gap_vecs = _vectors(n_gap, DIM)
    _write_index(root / 'papers.index', main_vecs)
    _write_ids(root / 'paper_ids.txt', main_ids)
    if sqlite_mode == 'aligned':
        sql_ids = main_ids + gap_ids
    elif sqlite_mode == 'mismatch':
        sql_ids = [f's{i}' for i in range(n_main + n_gap)]
    else:
        raise ValueError(sqlite_mode)
    _write_sqlite(root / 'papers.db', sql_ids)
    if n_gap:
        _write_index(root / 'papers_gap.index', gap_vecs)
        _write_ids(root / 'papers_gap_ids.txt', gap_ids)
        _age(root / 'papers_gap.index', gap_age_h)
        _age(root / 'papers_gap_ids.txt', gap_age_h)
    _age(root / 'papers.index', main_age_h)
    _age(root / 'paper_ids.txt', main_age_h)
    if daily_grow_hours is not None:
        stamp = datetime.now(timezone.utc) - timedelta(hours=daily_grow_hours)
        payload = {'last_run': stamp.replace(microsecond=0).isoformat()}
        (root / 'daily_grow_state.json').write_text(
            json.dumps(payload), encoding='utf-8')
    snap = {
        'n_main': n_main,
        'n_gap': n_gap,
        'main_ids': main_ids,
        'gap_ids': gap_ids,
        'main_vecs': main_vecs,
        'gap_vecs': gap_vecs,
        'index_sha': _sha(root / 'papers.index'),
        'ids_sha': _sha(root / 'paper_ids.txt'),
        'sqlite_sha': _sha(root / 'papers.db'),
    }
    if n_gap:
        snap['gap_index_sha'] = _sha(root / 'papers_gap.index')
        snap['gap_ids_sha'] = _sha(root / 'papers_gap_ids.txt')
    return snap


def _read_ids(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def _vectors_match(path: Path, expected: np.ndarray) -> bool:
    if not path.exists():
        return False
    index = faiss.read_index(str(path))
    if index.ntotal != len(expected):
        return False
    for i, vec in enumerate(expected):
        got = np.asarray(index.reconstruct(int(i)), dtype=np.float32)
        if not np.allclose(got, vec, atol=1e-6):
            return False
    return True


def classify(root: Path, snap: dict) -> str:
    index_path = root / 'papers.index'
    ids_path = root / 'paper_ids.txt'
    gap_index = root / 'papers_gap.index'
    gap_ids = root / 'papers_gap_ids.txt'
    sqlite_same = _sha(root / 'papers.db') == snap['sqlite_sha']
    if not sqlite_same:
        return 'torn'
    byte_same = (
        index_path.exists()
        and ids_path.exists()
        and _sha(index_path) == snap['index_sha']
        and _sha(ids_path) == snap['ids_sha']
    )
    if snap['n_gap']:
        gap_same = (
            gap_index.exists()
            and gap_ids.exists()
            and _sha(gap_index) == snap['gap_index_sha']
            and _sha(gap_ids) == snap['gap_ids_sha']
        )
    else:
        gap_same = not gap_index.exists() and not gap_ids.exists()
    if byte_same and gap_same:
        return 'byte_identical'
    merged_vecs = np.vstack([snap['main_vecs'], snap['gap_vecs']]) if snap['n_gap'] else snap['main_vecs']
    ids_ok = _read_ids(ids_path) == snap['main_ids'] + snap['gap_ids']
    gap_gone = not gap_index.exists() and not gap_ids.exists()
    if ids_ok and gap_gone and _vectors_match(index_path, merged_vecs):
        return 'fully_merged'
    return 'torn'


def _sidecars(root: Path) -> list[str]:
    return [name for name in SIDECARS if (root / name).exists()]


def _run(cmd: list[str], root: Path, fault: str | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env['PES2O_KB_DIR'] = str(root)
    env['HF_HUB_OFFLINE'] = '1'
    env['TRANSFORMERS_OFFLINE'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    env.pop('PAPERKB_FAULT', None)
    if fault:
        env['PAPERKB_FAULT'] = fault
    return subprocess.run(
        cmd,
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _merge(root: Path, fault: str | None = None, *, skip_verify: bool) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, str(REPO / 'merge_to_disk.py'), '--no-backup']
    if skip_verify:
        cmd.append('--skip-verify')
    return _run(cmd, root, fault)


def _health(root: Path, sample: int = 0) -> int:
    proc = _run(
        [sys.executable, str(REPO / 'kb_health.py'), '--sample', str(sample)],
        root,
    )
    if proc.returncode not in (0, 1, 2):
        raise RuntimeError(
            f'kb_health crashed rc={proc.returncode}\n{proc.stdout}\n{proc.stderr}')
    return proc.returncode


def _int_field(rel: str, pattern: str) -> int:
    match = re.search(pattern, (REPO / rel).read_text(encoding='utf-8'))
    if not match:
        raise RuntimeError(f'{rel} missing {pattern}')
    return int(match.group(1))


def _line(rel: str, needle: str) -> int:
    hits = [
        i for i, line in enumerate((REPO / rel).read_text(encoding='utf-8').splitlines(), 1)
        if needle in line
    ]
    if len(hits) != 1:
        raise RuntimeError(f'{rel} needle {needle!r} hits {hits}')
    return hits[0]


def _fault_cases(tmpdir: Path) -> dict[str, str]:
    observed: dict[str, str] = {}
    for stage in STAGES:
        for mode in ('raise', 'kill'):
            root = Path(tmpdir) / f'{mode}_{stage}'
            snap = build_kb(root)
            proc = _merge(root, f'{stage}:{mode}', skip_verify=True)
            immediate = classify(root, snap)
            if mode == 'kill':
                if proc.returncode >= 0:
                    raise RuntimeError(
                        f'{stage}:kill rc={proc.returncode} stderr={proc.stderr[-500:]}')
            elif proc.returncode <= 0:
                raise RuntimeError(
                    f'{stage}:raise rc={proc.returncode} stderr={proc.stderr[-500:]}')
            if mode == 'raise' and immediate == 'torn':
                raise RuntimeError(f'{stage}:raise left a torn main pair\n{proc.stderr[-800:]}')
            if immediate == 'torn':
                if mode != 'kill' or stage == 'memory_merge':
                    raise RuntimeError(f'unexpected torn state at {stage}:{mode}')
                if not (root / 'merge.journal').exists():
                    raise RuntimeError(f'{stage}:kill is torn but left no journal')
                repair = _merge(root, None, skip_verify=True)
                if repair.returncode != 0:
                    raise RuntimeError(
                        f'{stage}:kill repair rc={repair.returncode}\n{repair.stderr[-800:]}')
                final = classify(root, snap)
                if final != 'fully_merged':
                    raise RuntimeError(f'{stage}:kill repaired to {final}')
                label = 'torn_then_fully_merged'
            else:
                if immediate not in ('byte_identical', 'fully_merged'):
                    raise RuntimeError(f'{stage}:{mode} -> {immediate}')
                final = immediate
                label = immediate
            if _sidecars(root):
                raise RuntimeError(f'{stage}:{mode} left sidecars {_sidecars(root)}')
            if final not in ('byte_identical', 'fully_merged'):
                raise RuntimeError(final)
            observed[f'{mode}_{stage}'] = label
    return observed


def _happy_and_dry(tmpdir: Path) -> dict[str, str | int]:
    happy = Path(tmpdir) / 'happy'
    snap = build_kb(happy)
    proc = _merge(happy, None, skip_verify=False)
    if proc.returncode != 0:
        raise RuntimeError(f'happy rc={proc.returncode}\n{proc.stderr[-800:]}\n{proc.stdout[-400:]}')
    if classify(happy, snap) != 'fully_merged':
        raise RuntimeError(f'happy classified {classify(happy, snap)}')
    if 'kb_health exit code: 0' not in proc.stderr:
        raise RuntimeError(f'phase 4 did not report health exit 0:\n{proc.stderr[-800:]}')
    if _sidecars(happy):
        raise RuntimeError(f'happy left sidecars {_sidecars(happy)}')

    dry = Path(tmpdir) / 'dry'
    dry_snap = build_kb(dry)
    dry_proc = _run([sys.executable, str(REPO / 'merge_to_disk.py'), '--dry-run'], dry)
    if dry_proc.returncode != 0:
        raise RuntimeError(f'dry-run rc={dry_proc.returncode}\n{dry_proc.stderr[-800:]}')
    if classify(dry, dry_snap) != 'byte_identical':
        raise RuntimeError('dry-run changed the fixture')
    if _sidecars(dry):
        raise RuntimeError(f'dry-run left sidecars {_sidecars(dry)}')
    return {
        'happy_path_exit': 0,
        'happy_path_final': 'fully_merged',
        'dry_run_exit': 0,
        'dry_run_main': 'byte_identical',
    }


def _health_cases(tmpdir: Path) -> dict[str, int]:
    build_kb(Path(tmpdir) / 'healthy', n_gap=0)
    build_kb(Path(tmpdir) / 'warn', main_age_h=48, gap_age_h=36)
    build_kb(Path(tmpdir) / 'danger', main_age_h=100, gap_age_h=80)
    build_kb(Path(tmpdir) / 'stale', n_gap=0, daily_grow_hours=80)
    build_kb(Path(tmpdir) / 'nostate', n_gap=0, daily_grow_hours=None)
    build_kb(Path(tmpdir) / 'fresh48', n_gap=0, daily_grow_hours=48)
    build_kb(Path(tmpdir) / 'sample_miss', n_gap=0, sqlite_mode='mismatch')
    found = {
        'health_exit_healthy': _health(Path(tmpdir) / 'healthy'),
        'health_exit_warn': _health(Path(tmpdir) / 'warn'),
        'health_exit_danger': _health(Path(tmpdir) / 'danger'),
        'health_exit_stale': _health(Path(tmpdir) / 'stale'),
        'health_exit_missing_state': _health(Path(tmpdir) / 'nostate'),
        'health_exit_freshness_48h': _health(Path(tmpdir) / 'fresh48'),
        'health_exit_sample_miss': _health(Path(tmpdir) / 'sample_miss', sample=8),
    }
    expected = {
        'health_exit_healthy': 0,
        'health_exit_warn': 1,
        'health_exit_danger': 2,
        'health_exit_stale': 2,
        'health_exit_missing_state': 0,
        'health_exit_freshness_48h': 0,
        'health_exit_sample_miss': 0,
    }
    for key, want in expected.items():
        if found[key] != want:
            raise RuntimeError(f'{key}: got {found[key]} want {want}')
    return found


def probe_timeout() -> dict[str, int]:
    """Child entry. PES2O_KB_DIR is a fixture with a real FAISS index and sqlite."""
    import io
    from contextlib import redirect_stderr, redirect_stdout

    os.environ.setdefault('HF_HUB_OFFLINE', '1')
    os.environ.setdefault('TRANSFORMERS_OFFLINE', '1')
    kb_dir = Path(os.environ['PES2O_KB_DIR'])
    index = faiss.read_index(str(kb_dir / 'papers.index'))
    dim = int(index.d)

    import types
    module = types.ModuleType('sentence_transformers')

    class SentenceTransformer:
        sleep_s = 0.0

        def __init__(self, name: str):
            if SentenceTransformer.sleep_s:
                time.sleep(SentenceTransformer.sleep_s)

        def encode(self, sentences, **kwargs):
            if isinstance(sentences, str):
                sentences = [sentences]
            out = np.zeros((len(sentences), dim), dtype=np.float32)
            out[:, 0] = np.float32(1.0)
            return out

    module.SentenceTransformer = SentenceTransformer
    sys.modules['sentence_transformers'] = module

    import kb_search

    def reset() -> None:
        kb_search._model = None
        kb_search._index = None
        kb_search._paper_ids = None
        kb_search._paper_ids_raw = None
        kb_search._id_source = None
        kb_search._conn = None

    def call_search(**kwargs):
        stdout, stderr = io.StringIO(), io.StringIO()
        raised = 0
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                results = kb_search.search('probe', **kwargs)
            except Exception:
                raised = 1
                results = None
        return results, raised, stdout.getvalue() + stderr.getvalue()

    out: dict[str, int] = {}
    reset()
    SentenceTransformer.sleep_s = 0.3
    results, raised, err = call_search(n=3, smart=False, model_timeout=0.05, total_timeout=5)
    out['model_timeout_count'] = -1 if results is None else len(results)
    out['model_timeout_raised'] = raised
    out['model_timeout_said_empty'] = int('空结果' in err or '退化为' in err)
    if raised or results != []:
        raise RuntimeError(f'model timeout did not degrade: raised={raised} results={results} err={err}')

    reset()
    SentenceTransformer.sleep_s = 0.0
    warmed, raised, err = call_search(n=2, smart=False, total_timeout=5, model_timeout=5)
    if raised or not warmed:
        raise RuntimeError(f'warmup search failed raised={raised} n={None if warmed is None else len(warmed)} err={err}')

    original_remaining = kb_search._Stopwatch.remaining
    calls = {'n': 0}

    def remaining(self):
        calls['n'] += 1
        return 10.0 if calls['n'] == 1 else 0.0

    kb_search._Stopwatch.remaining = remaining
    try:
        partial, raised, err = call_search(n=5, smart=False, total_timeout=30, model_timeout=5)
    finally:
        kb_search._Stopwatch.remaining = original_remaining
    out['total_timeout_partial_count'] = -1 if partial is None else len(partial)
    out['total_timeout_raised'] = raised
    if raised or len(partial) != 1 or 'Timeout' not in err:
        raise RuntimeError(
            f'total timeout partial failed n={None if partial is None else len(partial)} '
            f'raised={raised} err={err!r}')

    kb_search._set_sqlite_busy_timeout(kb_search._conn, 0.2)
    ready = __import__('threading').Event()
    release = __import__('threading').Event()

    def locker() -> None:
        conn = sqlite3.connect(kb_dir / 'papers.db')
        conn.execute('BEGIN EXCLUSIVE')
        ready.set()
        release.wait(10)
        conn.commit()
        conn.close()

    thread = __import__('threading').Thread(target=locker, daemon=True)
    thread.start()
    acquired = ready.wait(3)
    out['sqlite_lock_acquired'] = int(acquired)
    if acquired:
        locked, raised, err = call_search(
            n=3, smart=False, total_timeout=5, model_timeout=5, sqlite_busy_timeout=0.2)
        out['sqlite_lock_count'] = -1 if locked is None else len(locked)
        out['sqlite_lock_raised'] = raised
        out['sqlite_lock_blocked'] = int(raised == 0 and locked == [])
        if raised:
            raise RuntimeError(f'real sqlite lock raised out of search: {err}')
    else:
        out['sqlite_lock_count'] = -1
        out['sqlite_lock_raised'] = 0
        out['sqlite_lock_blocked'] = 0
    release.set()
    thread.join(timeout=3)

    state = {'n': 0}
    real_conn = kb_search._conn

    class _CursorProxy:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, sql, *args, **kwargs):
            state['n'] += 1
            if state['n'] >= 2:
                raise sqlite3.OperationalError('database is locked')
            return self._inner.execute(sql, *args, **kwargs)

        def fetchone(self):
            return self._inner.fetchone()

    class _ConnProxy:
        def cursor(self):
            return _CursorProxy(real_conn.cursor())

    kb_search._conn = _ConnProxy()
    try:
        injected, raised, err = call_search(n=5, smart=False, total_timeout=5, model_timeout=5)
    finally:
        kb_search._conn = real_conn
    out['sqlite_injected_busy_count'] = -1 if injected is None else len(injected)
    out['sqlite_injected_busy_raised'] = raised
    if raised or len(injected) != 1 or 'SQLite busy' not in err:
        raise RuntimeError(
            f'injected busy failed n={None if injected is None else len(injected)} '
            f'raised={raised} err={err!r}')
    return out


def _timeout_case(root: Path) -> dict[str, int]:
    build_kb(root, n_gap=0, daily_grow_hours=1)
    env = os.environ.copy()
    env['PES2O_KB_DIR'] = str(root)
    env['HF_HUB_OFFLINE'] = '1'
    env['TRANSFORMERS_OFFLINE'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), '--probe-timeout'],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f'timeout probe rc={proc.returncode}\n{proc.stdout}\n{proc.stderr}')
    return {key: int(value) for key, value in json.loads(proc.stdout).items()}


def _rerank() -> dict[str, str | int]:
    import smart_rerank

    vecs = np.zeros((2, DIM), dtype=np.float32)
    vecs[0, 0] = np.float32(0.5)   # A: higher base score
    vecs[1, 0] = np.float32(0.25)  # B: lower base score
    query = np.zeros((1, DIM), dtype=np.float32)
    query[0, 0] = np.float32(1.0)
    index = faiss.IndexFlatIP(DIM)
    index.add(vecs)
    scores, ids = index.search(query, 2)
    metas = [
        {'name': 'A', 'year': '2020', 'categories': 'cs.ME'},
        {'name': 'B', 'year': '2026', 'categories': 'cs.CL'},
    ]
    before = [metas[int(i)]['name'] for i in ids[0]]
    ranked = []
    bases = {}
    finals = {}
    for score, idx in zip(scores[0], ids[0]):
        meta = metas[int(idx)]
        final, _debug = smart_rerank.rerank_score(meta, float(score))
        bases[meta['name']] = f'{round(float(score), 10):.2f}'
        finals[meta['name']] = f'{round(float(final), 10):.2f}'
        ranked.append((final, meta['name']))
    ranked.sort(key=lambda item: -item[0])
    after = [name for _score, name in ranked]
    if before != ['A', 'B'] or after != ['B', 'A']:
        raise RuntimeError(f'rerank did not flip order before={before} after={after}')
    return {
        'rerank_order_before': ','.join(before),
        'rerank_order_after': ','.join(after),
        'rerank_base_A': bases['A'],
        'rerank_base_B': bases['B'],
        'rerank_final_A': finals['A'],
        'rerank_final_B': finals['B'],
        'rerank_is_retrieval_quality': 0,
    }


def _unit_test_methods() -> int:
    import ast
    tree = ast.parse((REPO / 'tests' / 'test_kb_search_unit.py').read_text(encoding='utf-8'))
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        for fn in node.body
        if isinstance(fn, ast.FunctionDef) and fn.name.startswith('test_')
    )


def collect() -> dict:
    import importlib.metadata as metadata

    with tempfile.TemporaryDirectory(prefix='paperkb-evidence-') as tmp:
        root = Path(tmp)
        faults = _fault_cases(root)
        paths = _happy_and_dry(root)
        health = _health_cases(root)
        timeout = _timeout_case(root / 'timeout')
    rerank = _rerank()
    payload: dict = {
        'fixture_main_vectors': N_MAIN,
        'fixture_gap_vectors': N_GAP,
        'fixture_dim': DIM,
        'fault_stages': len(STAGES),
        'fault_cases': len(STAGES) * 2,
        'fault_cases_final_consistent': len(STAGES) * 2,
        **faults,
        **paths,
        **health,
        **timeout,
        **rerank,
        'cite_search_def': _line('kb_search.py', 'def search('),
        'cite_kb_dir': _line('kb_search.py', "KB_DIR = Path(os.environ.get('PES2O_KB_DIR'"),
        'cite_year_bonus': _line('smart_rerank.py', 'YEAR_BONUS = {'),
        'cite_gap_warn': _line('kb_health.py', 'elif unmerged_hours >= 24'),
        'cite_gap_danger': _line('kb_health.py', 'if unmerged_hours >= 72'),
        'gap_warn_hours': _int_field('kb_health.py', r'elif unmerged_hours >= (\d+)'),
        'gap_danger_hours': _int_field('kb_health.py', r'if unmerged_hours >= (\d+)'),
        'freshness_ok_below_hours': _int_field(
            'kb_health.py', r"freshness_status = 'ok' if age_h < (\d+)"),
        'freshness_stale_hours': _int_field(
            'kb_health.py', r"warn' if age_h < (\d+) else 'stale'"),
        'unit_test_methods': _unit_test_methods(),
        'faiss_cpu_version': metadata.version('faiss-cpu'),
        'historical_index_vectors': '580,285',
    }
    if payload['unit_test_methods'] != 19:
        raise RuntimeError(f"unit test count changed: {payload['unit_test_methods']}")
    return payload


def main() -> int:
    if '--probe-timeout' in sys.argv:
        print(json.dumps(probe_timeout()))
        return 0
    try:
        print(json.dumps(collect(), indent=2, sort_keys=True))
    except Exception as exc:
        print(f'EVIDENCE FAILED: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
