# paperKB — peS2o 论文知识库搜索工具包

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

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
