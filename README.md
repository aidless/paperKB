# paperKB — peS2o 论文知识库搜索工具包

> **English TL;DR**: A five-script toolkit for semantic search over a peS2o paper knowledge base, with timeout-safe search, a fail-closed health check, atomic gap-index merge, deduplication/reindex, and bias-aware reranking. Its 19 mock-based unit tests were re-run locally in this pass; the 580,285-vector index figures come from a historical measurement on an external disk that is not part of this repository and were **not** recomputable here.

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/aidless/paperKB/actions/workflows/ci.yml/badge.svg)](https://github.com/aidless/paperKB/actions/workflows/ci.yml)

> **这不是 Obsidian 笔记应用**。这是一个 FAISS + SQLite 工具包，用于在大型论文
> 知识库（peS2o，54 万+ 篇）上做语义检索、健康检查、去重重建与增量合并。名字
> 取自作者本地工具链，与 Obsidian 编辑器无关。

---

## 这个项目解决什么问题

在 54 万+ 篇的论文语料上做语义检索时，会碰到四个工程问题：

1. **搜索慢且超时**：向量检索 + 模型加载容易拖到几十秒甚至卡死。
2. **索引会漂移**：`daily_grow` 增量补丁（gap 索引）与主索引不一致，长期不合并
   会导致健康检查从 HEALTHY 掉到 WARN/DANGER。
3. **数据有重复与缺失**：重复 paper_id、缺失元数据让检索结果失真。
4. **结果撞「模态天花板」**：在特定领域（如机械工程）语料密集时，纯语义检索
   会被无关论文占据，需要重排序与查询扩展。

本工具包用 5 个脚本解决：`kb_search`（检索 + 超时优雅降级）、`kb_health`
（只读健康检查）、`dedup_and_reindex`（去重重建）、`merge_to_disk`（增量原子
合并）、`smart_rerank`（重排序 + 查询扩展）。

## 为什么重要

- **超时保护内置**：`search()` 有 `total_timeout` / `model_timeout` /
  `sqlite_busy_timeout` 三层预算，超时后优雅降级返回已 fetch 的部分结果 + 提示，
  **不抛异常**（`kb_search.py:380`）。
- **索引健康可量化**：`kb_health` 输出退出码 0=健康 / 1=警告 / 2=危险，并检测
  增量未合并时长（24h/72h 阈值）。
- **增量合并是原子的**：`merge_to_disk` 做 in-memory merge + atomic swap 写回
  主索引 + 清空 gap，任一阶段失败即 stop，不污染状态（4 阶段流水线）。
- **重排序对抗语料偏差**：`smart_rerank` 对 2024-2026 论文加权、`cs.LG/CL/AI` 等
  类别加分、可选引用数加分，解决 "modal ceiling" 撞机械工程论文的问题。

## 我用了什么方法

- **向量检索**：FAISS（主索引 `papers.index` + gap 索引 `papers_gap.index`）。
- **元数据**：SQLite（`papers.db`）+ 明文 ID 文件（`paper_ids.txt` /
  `papers_gap_ids.txt`）。
- **嵌入**：sentence-transformers（默认 `all-MiniLM-L6-v2`，`_st_model` 懒加载）。
- **重排序**：多因子线性加分（年份 + 类别 + 引用），见 `smart_rerank.py:133-155`
  的 `YEAR_BONUS` / `LLM_CATEGORY_BONUS`。

## 结果如何

**测试套件**：`tests/test_kb_search_unit.py` 19 项通过（mock KB，100 papers +
内存 sqlite + 假 sentence-transformers，~8.5 秒），CI 在 ubuntu-latest 跑。
集成测试 `tests/test_kb_search.py` 需要真实 KB（8GB，54 万 papers），本地跑，
CI 跳过。

历史修复记录（`CHANGELOG_2026-07-09.md` / `CICD_DEPLOYMENT_CHECKLIST.md`）：
- 去重重建：SQLite 257 种重复、FAISS 33K 重复、补齐 14,593 条缺失
  `peS2o_numeric` paper_id，`pct_all` 从非零降到 ≈0%，`kb_health` 从 WARN 升到
  HEALTHY。

## Fail-closed 声明

本工具包**默认 fail-closed**（对「假装成功」零容忍）：

- **增量合并失败即停**：`merge_to_disk` 的 4 阶段（in-memory merge → atomic swap 写回主索引 → 清 gap）任一阶段失败即 `stop`，**不污染**主索引状态，绝不在部分写入后报告「成功」。
- **超时是优雅降级不是静默错**：`search()` 超时后返回**已 fetch 的部分结果 + 提示**，不抛异常但**也不伪造未检索的结果**——用户能明确看到「检索不完整」。
- **健康检查量化不装好**：`kb_health` 用退出码 0=健康 / 1=警告 / 2=危险 如实反映主/gap 索引与 SQLite 的差距，`WARN`/`DANGER` 不会被当成 `HEALTHY`。
- **去重重建先备份再写**：`dedup_and_reindex` 写盘前要求备份，`--dry-run` 只读先行，失败不覆盖。

> 立场：本工具宁可不给结果、不给「看起来健康」的假绿，也不在部分状态上断言成功。fail-closed 意味着「无法验证就报告无法验证」，而不是猜一个数字。

## 边界（证明什么 / 不证明什么）

**证明什么：**
- 工具包能在 mock 数据上跑通**全部 5 个脚本的单元路径**（19 项单测佐证），且
  检索有超时保护、不抛异常。
- 健康检查能**量化**主/gap 索引与 SQLite 的差距，并给出退出码语义。

**不证明什么：**
- 不证明真实 54 万语料上的**端到端性能**：单测用 mock（100 papers）；真实 8GB
  KB 的检索延迟/召回只有本地集成测试 `test_kb_search.py` 能给出，而它依赖
  `E:/peS2o_kb_faiss`（Windows 路径），在 Linux 环境不可复现。
- 不证明 `smart_rerank` 的**类别/年份权重是最优的**：`YEAR_BONUS` 与
  `LLM_CATEGORY_BONUS` 是人工设定的常数，未做参数扫描，不代表最优解。
- 不证明**检索无漏**：纯向量检索 + 固定嵌入模型会有语义边界外的漏检，工具包
  只做检索不做覆盖度证明。
- 工具包是**本地工具链**，不是分布式服务；并发写入无锁，单进程使用。
- `KB_DIR`（`kb_search.py:53`）与磁盘检查盘符硬编码；换机需 sed/手改路径（上面的
  实跑示例给出一条完整 Linux 通道）。
- 健康检查的 freshness/disk 维度绑定了 daily_grow 与宿主盘符：`kb_health` 在真实
  KB 上 2026-11-10 实跑给出 DANGER（exit 2）——freshness 1901h 未增量抓取 + 宿主盘
  余量 <5%；一致性维度（FAISS vs SQLite +0.0%、抽样 2000/2000）全过。DANGER 是
  如实报警，不是工具故障。

## 索引规模与数据来源（2026-11-10 实测，/data1/peS2o_kb_faiss）

| 项 | 值 | 测法 |
|---|---|---|
| FAISS 主索引 | 580,285 条向量（d=384），850.03 MB | `faiss.read_index().ntotal` |
| SQLite 元数据 | papers 表 580,285 行，papers_fts 580,285 行，papers.db 4.79 GB | sqlite3 `SELECT count(*)` |
| ID 文件 | paper_ids.txt 580,285 行 | `wc -l` |
| 来源构成 | peS2o 数字 ID 332,028（57.2%）· arXiv 248,169（42.8%，daily_grow 增量抓取）· peS2o SHA1 88 | 按 ID 格式正则分类 |
| 一致性 | 主 FAISS vs SQLite 差 +0（0.0%）；抽样 2000/2000 全命中 | `kb_health` |
| gap 索引 | 不存在（已合并，status=ok） | `kb_health` |

（来源构成与一致性数字取自 `kb_health.py` 实跑输出；总数与向量数用 faiss/sqlite/wc 独立复算一致。）

## kb_search 实跑示例（Linux，真实查询）

`KB_DIR` 与模型路径硬编码（见下），Linux 上用一条 sed 生成临时副本指向本地 KB：

```bash
sed -e 's|E:/peS2o_kb_faiss|/data1/peS2o_kb_faiss|' \
    -e "s|MODEL_NAME = 'all-MiniLM-L6-v2'|MODEL_NAME = '/data1/peS2o_kb_faiss/models/all-MiniLM-L6-v2'|" \
    kb_search.py > /tmp/kb_search_linux.py
python3 /tmp/kb_search_linux.py "KV cache eviction policy" -n 5
```

真实输出（Top-5，0.010s，2026-11-10）：

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

## 快速开始

```bash
pip install -r requirements.txt

# 健康检查（只读）
python kb_health.py

# 搜索
python kb_search.py "你的查询"

# 增量合并（原子，写盘）
python merge_to_disk.py --dry-run   # 先干跑只读
python merge_to_disk.py             # 真合并

# 去重重建（写盘，先备份）
python dedup_and_reindex.py --dry-run
```

> `KB_DIR` 硬编码在 `kb_search.py:53`（`E:/peS2o_kb_faiss`，Windows 路径）。
> 在非 Windows 环境需修改该路径或挂载对应盘符。

## 测试与复现

```bash
# 单元测试（mock KB，CI 跑，~8.5s）
python -X utf8 -m unittest tests.test_kb_search_unit -v

# 集成测试（需真实 KB，本地跑）
python -X utf8 -m unittest tests.test_kb_search -v
```

- 19 项单测来自 `tests/test_kb_search_unit.py`（mock 数据生成器在
  `tests/_mocks.py`）。
- CI（`.github/workflows/ci.yml`）跑单元测试 + 3 个脚本 import 烟雾测试 +
  全树语法检查，跳过需真实 KB 的集成测试。

## 目录结构

```
kb_search.py            # 语义检索（超时保护 / 优雅降级 / BibTeX 输出）
kb_health.py            # 只读健康检查（退出码 0/1/2）
dedup_and_reindex.py    # 去重 + 补齐缺失（4+1 阶段流水线，dry-run 支持）
merge_to_disk.py        # 增量索引原子合并到主索引（4 阶段，失败即停）
smart_rerank.py         # 重排序 + 查询扩展（expand_query / rerank_score）
tests/
  test_kb_search_unit.py  # 19 项 mock 单测（CI）
  test_kb_search.py       # 集成测试（需真实 KB）
  _mocks.py               # mock 数据生成器
  simulate_ci_timeout.py  # 超时模拟（CI 验证）
```

## 参考

- `CICD_DEPLOYMENT_CHECKLIST.md`：部署与 CI/CD 检查清单（279 行）
- `CHANGELOG_2026-07-09.md`：去重重建修复记录
- `SECURITY.md`：安全声明

## U1 results table (recomputed 2026-11-11)

| Live check | Value | Source |
|---|---|---|
| Mock unit tests, executed for this pass | **Ran 19 tests in 15.103s / OK** | `python3 -X utf8 -m unittest tests.test_kb_search_unit` (exit 0) |
| Unit-test target file | `tests/test_kb_search_unit.py` | `README.md:50`, `README.md:169` |
| Mock KB size used by tests | 100 papers | `README.md:50` |
| Historical CI unit-test runs | `Ran 19 tests in 17.152s, OK` / `Ran 19 tests in 13.079s, OK` | `CICD_DEPLOYMENT_CHECKLIST.md:57`, `CICD_DEPLOYMENT_CHECKLIST.md:61` |
| Dedup / rebuild record | SQLite 257 duplicate kinds, FAISS 33K duplicates, 14,593 missing IDs backfilled, `pct_all` → ≈0% | `README.md:56`, `CICD_DEPLOYMENT_CHECKLIST.md:4` |
| Consistency spot-check (historical) | FAISS vs SQLite +0 (0.0%), 2000/2000 sample hits | `README.md:102`, `README.md:91` |

**Quickstart command actually executed for this pass**:

```bash
python3 -X utf8 -m unittest tests.test_kb_search_unit
# Ran 19 tests in 15.103s
# OK   (exit 0)
```

**NOT YET MEASURED / not recomputable in this pass**: the real-index numbers
in the existing section above (580,285 FAISS vectors, 580,285 SQLite rows,
850.03 MB index, 4.79 GB database, Top-5 in 0.010s — `README.md:98-100`,
`README.md:118-123`). They were measured on `/data1/peS2o_kb_faiss`, which is
not in this repository; `KB_DIR` is still a Windows path
(`kb_search.py:53`, see `README.md:156`). Reproduce only on a host with the
real KB:

```bash
python3 -X utf8 -m unittest tests.test_kb_search_unit -v   # offline mock path
python3 kb_health.py                                       # requires real KB_DIR
python3 -X utf8 -m unittest tests.test_kb_search -v        # requires real 8GB KB
```

<!-- figure: pending real measurement -->

## 验收题库与复算

`eval/cases.jsonl` 是 20 条可执行验收题，覆盖十二个口径（重排序年份加权与缺失
year、类别加权只计一次与 hint 命中、查询扩展规则与 AI 上下文判定、BibTeX 键与
arXiv/peS2o 两种字段导出、健康检查字节与行数格式、缺文件时的不抛异常降级、
重建嵌入维度常量、单测声明条数现数，以及 README / PREREG / CI 三条文档口径）。
验收判据：`python3 eval/run_cases.py` 必须 `20/20 cases passed` 且退出码 0，
其中 **≥ 12 条**为 `expr`/`raises` 且**真调本仓库 API**（`smart_rerank` /
`kb_search` / `kb_health` / `dedup_and_reindex`）或真读仓库内文件，期望值不抄
文档、一律现算。

```bash
python3 eval/run_cases.py            # 20/20 cases passed
python3 eval/run_cases.py --save     # 顺带把结果写进 eval/run-report.json
python3 eval/run_cases.py --selftest # 篡改一条期望值，必须变红（19/20，证明这套题会失败）
```

**数字如何复算**：`eval/run-report.json` 里每条 case 都带 `id` / `axis` /
`detail`，把 `detail` 与 `cases.jsonl` 的 `verify` 代码一起看，就能从源码复现该
数字；`--save` 重跑一次即可重建整份报告，不需要手工抄数。`expr` 题的 `verify`
末尾一律是 `result = ...`，可直接贴进 `python3 -c` 复算。

**失败归因**：`run_cases.py` 不只报通过率，失败时按 `axis` 汇总（例如
`rerank/year: 1 条 —— RY-01`），直接指出是哪个模块的哪个口径坏了。
