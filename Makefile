# --- acceptance_backfill (EVAL_ACCEPTANCE_STANDARD.md P4) ---
# 证据来源: tests/ + .github/workflows/ci.yml
# 命令逐字取自该来源，未增删任何参数。
# 修入口（本机实测）：原 `pytest tests -q` 收集了两个套件，其中 tests/test_kb_search.py
# 是集成套件，需真实 KB（E:/peS2o_kb_faiss，8GB FAISS）+ 已下载模型；本机无 KB、HF 不可达
# → 19 passed / 19 errors（逐条 OSError: couldn't connect to hf-mirror.com），rc=1。
# 仓自己的 CI 门只跑 mock 单元套件（.github/workflows/ci.yml:82），本机可离线真跑通，故 eval 用该命令。
# 集成套件单独保留为 integration:（本机预期红：需要真实 KB 与模型）。
.PHONY: eval integration

eval:
	python3 -X utf8 -m unittest tests.test_kb_search_unit -v

integration:
	python3 -m pytest tests/test_kb_search.py -q
