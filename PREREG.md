# paperKB（obsidian 仓库）预注册（PREREG）

> 本文档是研究规格（registered-report 风格），不是结果报告。它声明本仓库**计划检验的假设、评测方案、成功标准与明确边界**，全部基于 `README.md`、`kb_search.py`、`kb_health.py`、`dedup_and_reindex.py`、`merge_to_disk.py`、`smart_rerank.py`、`tests/test_kb_search_unit.py` 中已落盘的真实内容。本文档不虚构任何未执行的实验。

## 1. 研究问题

一个在 54 万+ 篇论文语料（peS2o）上做语义检索的 FAISS+SQLite 工具包能否做到：

- **RQ1**：检索在超时/慢模型下是否**优雅降级**而不是抛异常或卡死？
- **RQ2**：主索引与增量 gap 索引的健康差距能否被**量化**为退出码语义（0/1/2）？
- **RQ3**：去重重建与增量合并是否**原子**（失败即停，不污染既有状态）？

## 2. 预注册假设

| ID | 假设 | 检验方式 | 成功标准 |
|---|---|---|---|
| H1 | 检索有超时保护且**不抛异常** | `kb_search.py:380` 的三层预算（`total_timeout`/`model_timeout`/`sqlite_busy_timeout`）+ `tests/simulate_ci_timeout.py` | 超时后优雅降级返回已 fetch 的部分结果 + 提示，不抛异常 |
| H2 | 健康检查**量化**索引漂移 | `kb_health.py` 检测主/gap 索引与 SQLite 差距 + 增量未合并时长（24h/72h 阈值） | 退出码 0=健康 / 1=警告 / 2=危险，语义明确 |
| H3 | 增量合并是**原子的** | `merge_to_disk.py` 4 阶段（in-memory merge → atomic swap → 清 gap） | 任一阶段失败即 stop，不污染主索引状态 |
| H4 | 单元路径真实可跑 | `tests/test_kb_search_unit.py`（mock KB：100 papers + 内存 sqlite + 假 sentence-transformers） | 19 项单测通过（~8.5 秒），CI 在 ubuntu-latest 跑 |

## 3. 评测方案

- **单元测试**：`python -X utf8 -m unittest tests.test_kb_search_unit -v`（19 项，mock 100 papers + 内存 sqlite + 假 `sentence-transformers`，~8.5s，CI 跑）。
- **集成测试**：`python -X utf8 -m unittest tests.test_kb_search -v`——需真实 8GB KB（54 万 papers，`E:/peS2o_kb_faiss` Windows 路径），本地跑，CI 跳过（README 明确）。
- **健康检查**：`python kb_health.py`（只读，退出码 0/1/2）。
- **增量合并**：`python merge_to_disk.py --dry-run`（先干跑只读）→ `merge_to_disk.py`（真合并）。

## 4. 已声明边界（证明什么 / 不证明什么）

**证明什么（已实测）**：
- 5 个脚本的**单元路径**全部跑通（19 项单测佐证），检索有超时保护、不抛异常。
- 健康检查能**量化**主/gap 索引与 SQLite 的差距并给出退出码语义。
- 历史修复：去重重建 SQLite 257 种重复、FAISS 33K 重复、补齐 14,593 条缺失 `peS2o_numeric` paper_id，`pct_all` 从非零降到 ≈0%，`kb_health` 从 WARN 升到 HEALTHY（`CHANGELOG_2026-07-09.md`）。

**不证明什么（诚实边界）**：
- 不证明真实 54 万语料上的**端到端性能**：单测用 mock（100 papers）；真实 8GB KB 的检索延迟/召回只有本地集成测试 `test_kb_search.py` 能给出，而它依赖 `E:/peS2o_kb_faiss`（Windows 路径），在 Linux 环境不可复现。
- 不证明 `smart_rerank` 的**类别/年份权重是最优的**：`YEAR_BONUS` 与 `LLM_CATEGORY_BONUS`（`smart_rerank.py:133-155`）是人工设定常数，未做参数扫描，不代表最优解。
- 不证明**检索无漏**：纯向量检索 + 固定嵌入模型（`all-MiniLM-L6-v2`）会有语义边界外的漏检，工具包只做检索不做覆盖度证明。
- 工具包是**本地工具链**，不是分布式服务；并发写入无锁，单进程使用。

## 5. 成功标准（预先声明）

H1 与 H2 的**联合**成功 = 检索超时优雅降级（不抛异常）+ 健康检查量化退出码语义。H3 成功 = 增量合并原子（失败即停不污染）。本文档声明的成功**不**包括「真实 54 万语料的端到端性能」——那明确排除在成功之外（真实 KB 依赖 Windows 路径，Linux 不可复现）。

## 6. 预注册时间戳与状态

- 本文档为**回顾性预注册**：反映仓库既有已落盘内容（README、`kb_search.py`、`kb_health.py`、`merge_to_disk.py`、`dedup_and_reindex.py`、`smart_rerank.py`、`tests/`），不做前瞻性承诺。
- 目的：把「假设 + 评测方案 + 成功标准 + 边界」写清楚，供评审判断本仓库到底证明/不证明什么。
