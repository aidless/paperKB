# FAILURE_MODES

小夹具证明的是进程被杀死或抛错时主文件会不会写成一半。它不证明外部磁盘上那份大索引的延迟或检索质量。

## What this run actually is

The offline fixture scale is 8 main vectors + 4 gap vectors, dimension 8. That is the FAISS index `make check` builds in a temporary directory. It is not the external-disk index. The 580,285-vector figures are historical measurements from a disk that is not in this repository and are not recomputed by make check.

faiss-cpu is not pinned in `requirements.txt`. make check prints faiss_cpu_version and does not compare it to a frozen number. A newer wheel can change on-disk index bytes; the check compares vectors with `reconstruct`, and it compares pre-merge bytes only when the file was copied back unchanged.

## Merge

`merge_to_disk.py` replaces `papers.index` and `paper_ids.txt` with two `os.replace` calls. One replace on the same directory is atomic. The pair is not.

A SIGKILL between the two os.replace calls leaves a torn main pair until the next merge_to_disk.py run, which reads `merge.journal` and finishes the commit. The same is true if the process is killed after both files are replaced but before both gap files are deleted: the main pair is already the merged data, and the next run deletes the leftover gap file. `kill_memory_merge` happens before the journal exists, so the main files stay byte-identical.

A raised exception does not leave a torn main pair. `index_swap` and `ids_swap` copy the safety files back over the main pair. `clear_gap` finishes the commit instead, because the gap index may already be gone and rolling the main index back would drop those vectors.

This is a process-kill test. It does not power off the disk. The journal file is fsynced; the parent directory is not. A crash that loses the journal rename can still leave a torn pair with nothing to recover from.

`--no-backup` does not keep a timestamped copy. It still writes `papers.index.rollback` and `paper_ids.txt.rollback` for the swap window and deletes them after a consistent commit. `--dry-run` does not write. Phase 4 runs this repository's `kb_health.py` with `PES2O_KB_DIR` pointing at the data directory. It does not execute a second copy that happens to live inside that directory.

`kb_search.py` still merges a gap index in memory on load. A merged main index with the gap files left in place would append the gap twice. The check treats that state as torn until the gap files are gone.

## Health exit codes

Gap age uses the file mtimes. Under 24 hours is not a failure. The warn and danger exits in the README were measured by setting those mtimes, with SQLite row counts aligned to main+gap so the count gap is zero.

A daily_grow timestamp 48 hours old still exits 0 when the index counts match and there is no stale gap. The source labels that band warn, and the label does not change the exit code. Stale is a later threshold and does force exit 2. A missing daily_grow_state.json is reported as unknown and does not fail a consistent index.

An ID sample that misses every SQLite row does not change the health exit code. The sample rate is printed. The exit code uses counts, gap age, and whether daily_grow is stale.

## Search timeouts

`model_timeout` is checked after `SentenceTransformer` returns. A load that never returns is not interrupted. When the constructor is slower than the budget, `search` returns an empty list and does not raise.

`total_timeout` is cooperative. The loop looks at the stopwatch between rows. The measured partial result is one row, then a stop. It is not a signal that aborts a running call.

`sqlite_busy_timeout` is SQLite's lock wait. It does not cancel a long-running query. On this machine a second connection held `BEGIN EXCLUSIVE`, the wait expired, and `search` returned no rows without raising. A second injected `database is locked` after one successful read returned that one row. Both are degradation under a lock error, not a proof about slow queries.

## Rerank

`YEAR_BONUS` and `LLM_CATEGORY_BONUS` are hand-set constants. Only the first matching category in dict order is added. The measured flip uses axis-aligned vectors with base inner products 0.50 and 0.25, then the year and category constants. That ordering is not a neural embedding and not a retrieval-quality result. Hashing embeddings are not used. No peS2o text is committed; the two rows are names A and B with years and categories only.

## What is still not shown

The external disk, the 0.010s query transcript, and any claim about recall or adoption are outside this check. The mock unit tests in `tests/test_kb_search_unit.py` do not build this fixture. Concurrent writers are not locked. `PAPERKB_FAULT` is a test seam and does nothing when unset.
