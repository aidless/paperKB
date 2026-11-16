# paperKB — 五个本地脚本，不是 Obsidian

> **English TL;DR**: Five local FAISS + SQLite scripts for a peS2o paper KB (search, health, dedup, merge, rerank). They are not the Obsidian app and not a service. A fresh clone can recompute only the small-fixture fault results below. External-disk index counts, latencies, and retrieval quality are historical and were not recomputed here.

外部磁盘上的论文库会把向量检索拖死，增量 gap 也会和主索引分叉。
paperKB 是五个本地脚本（搜索、健康检查、去重、合并、重排序），不是 Obsidian，也不是服务。
这次能复算的只有小夹具上的故障结果；外部磁盘上的规模、延迟和检索质量都没有重算。

仓库名是 [aidless/paperKB](https://github.com/aidless/paperKB)。`aidless/obsidian` 会重定向到这里。下面的 CI 徽章指向 paperKB。

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/aidless/paperKB/actions/workflows/ci.yml/badge.svg)](https://github.com/aidless/paperKB/actions/workflows/ci.yml)

## 这次复算了什么

`make check` 离线运行 `checks/evidence.py`，对照下面的表。表和重算结果不一致就失败。不下载嵌入模型。这条路径只需要 `numpy` 和 `faiss-cpu`。`faiss-cpu` 不锁版本，检查会把装到的版本打出来，不写进这张表。

<!-- measured-numbers:start -->

```text
cite_gap_danger: 227
cite_gap_warn: 230
cite_kb_dir: 53
cite_search_def: 380
cite_year_bonus: 133
dry_run_exit: 0
dry_run_main: byte_identical
fault_cases: 8
fault_cases_final_consistent: 8
fault_stages: 4
fixture_dim: 8
fixture_gap_vectors: 4
fixture_main_vectors: 8
freshness_ok_below_hours: 36
freshness_stale_hours: 72
gap_danger_hours: 72
gap_warn_hours: 24
happy_path_exit: 0
happy_path_final: fully_merged
health_exit_danger: 2
health_exit_freshness_48h: 0
health_exit_healthy: 0
health_exit_missing_state: 0
health_exit_sample_miss: 0
health_exit_stale: 2
health_exit_warn: 1
kill_clear_gap: torn_then_fully_merged
kill_ids_swap: torn_then_fully_merged
kill_index_swap: torn_then_fully_merged
kill_memory_merge: byte_identical
model_timeout_count: 0
model_timeout_raised: 0
model_timeout_said_empty: 1
raise_clear_gap: fully_merged
raise_ids_swap: byte_identical
raise_index_swap: byte_identical
raise_memory_merge: byte_identical
rerank_base_A: 0.50
rerank_base_B: 0.25
rerank_final_A: -0.10
rerank_final_B: 0.67
rerank_is_retrieval_quality: 0
rerank_order_after: B,A
rerank_order_before: A,B
sqlite_injected_busy_count: 1
sqlite_injected_busy_raised: 0
sqlite_lock_acquired: 1
sqlite_lock_blocked: 1
sqlite_lock_count: 0
sqlite_lock_raised: 0
total_timeout_partial_count: 1
total_timeout_raised: 0
unit_test_methods: 19
```

<!-- measured-numbers:end -->

表里的 `cite_*` 是源码行号，检查会打开那一行核对关键字。`byte_identical` 表示主索引和 id 文件与合并前的字节相同，gap 还在。`fully_merged` 表示主索引向量和 id 都已包含 gap，并且 gap 文件已删除。`torn_then_fully_merged` 表示 SIGKILL 当时主文件不一致或 gap 只删了一半，下一次运行 `merge_to_disk.py` 把它收成 `fully_merged`。被异常抓住的失败不会停在 torn。

健康检查的退出码来自临时目录，不是外部磁盘。表里 sample miss 那一行是 id 与 SQLite 对不上、退出码却仍为健康。freshness 那一行是 daily_grow 落在源码的 warn 带、退出码仍为健康。missing state 那一行是没有状态文件。重排序两行是手写向量上的加分，检索质量那一行标成否。边界写在 `FAILURE_MODES.md`。

```bash
pip install numpy faiss-cpu
make check
```

`PES2O_KB_DIR` 指向数据目录（默认仍是作者机器上的 Windows 路径）。不设置时，搜索和健康检查找不到那块盘。

## 历史数字（外部磁盘，这里不能重算）

<!-- historical-unverified:start -->

These figures are historical measurements from an external disk and were not recomputed in this repository. Do not treat them as output of `make check`.

| 项 | 值 | 当时的测法 |
|---|---|---|
| FAISS 主索引 | 580,285 FAISS vectors（d=384），850.03 MB | `faiss.read_index().ntotal` on `/data1/peS2o_kb_faiss` |
| SQLite 元数据 | papers 表 580,285 行，papers_fts 580,285 行，papers.db 4.79 GB | sqlite3 `SELECT count(*)` |
| ID 文件 | paper_ids.txt 580,285 行 | `wc -l` |
| 来源构成 | peS2o 数字 ID 332,028（57.2%）· arXiv 248,169（42.8%）· peS2o SHA1 88 | 按 ID 格式分类 |
| 一致性 | 主 FAISS vs SQLite 差 +0（0.0%）；抽样 2000/2000 | 当时的 `kb_health` 输出 |

下面是一份当时的查询记录，不是这次复算的结果：

```text
  Query: "KV cache eviction policy"
  KB: 580,285 FAISS vectors / 580,285 unique IDs
  Top-5 results in 0.010s

  # 1 | score=1.1322  Risk-Controlled KV-Cache Eviction: From Memory Budgets to Risk Targets
        [arXiv] arxiv:2609.27981 | 2026 | cs.CL,cs.LG
  # 2 | score=1.0964  A Probabilistic Interpretation of KV Cache Eviction
        [arXiv] arxiv:2608.28293 | 2026 | cs.CL,cs.AI
  # 3 | score=1.0593  Error Certificates for KV-Cache Eviction via Randomized Design
        [arXiv] arxiv:2607.21475 | 2026 | cs.LG,cs.AI,cs.CL
  # 4 | score=1.0092  KVpop -- Key-Value Cache Compression with Predictive Online Pruning
        [arXiv] arxiv:2607.05061 | 2026 | cs.CL,cs.AI
  # 5 | score=1.0040  Leyline: KV Cache Directives for Agentic Inference
        [arXiv] arxiv:2606.01065 | 2026 | cs.DC cs.AI cs.LG
```

更早的去重记录（SQLite 257 种重复、FAISS 33K 重复、补齐 14,593 条 id）在 `CHANGELOG_2026-07-09.md`，同样不能在本仓库重算。

<!-- historical-unverified:end -->

## 脚本

| 脚本 | 作用 |
|---|---|
| `kb_search.py` | 语义检索。`def search` 的三个超时参数在上表 `cite_search_def`。慢模型返回空列表；总预算在行与行之间停下并返回已拿到的行；SQLite 锁等待失败同样返回已拿到的行。 |
| `kb_health.py` | 只读检查。退出码含义见上表，阈值行是 `cite_gap_warn` 和 `cite_gap_danger`。 |
| `merge_to_disk.py` | 把 gap 写入主索引。四段故障点的名字在上表的 `raise_*` / `kill_*` 行。 |
| `dedup_and_reindex.py` | 去重重建。写盘前可以先 `--dry-run`。 |
| `smart_rerank.py` | 年份和类别加分。`YEAR_BONUS` 在 `cite_year_bonus`。 |

```bash
python kb_health.py
python kb_search.py "你的查询"
python merge_to_disk.py --dry-run
python merge_to_disk.py
python dedup_and_reindex.py --dry-run
```

已有的 mock 单测（条数是上表的 `unit_test_methods`）和验收题：

```bash
python3 -X utf8 -m unittest tests.test_kb_search_unit -v
python3 eval/run_cases.py
```

集成测试 `tests/test_kb_search.py` 需要外部知识库，CI 不跑。
