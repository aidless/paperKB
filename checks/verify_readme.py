#!/usr/bin/env python3
"""Recompute the README measured block and fail if it drifts.

Offline: imports checks.evidence, which builds a tiny FAISS fixture and does
not download a model. Prints the installed faiss-cpu version without requiring
the README to freeze it.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from checks.evidence import collect  # noqa: E402

README = REPO / 'README.md'
FAILURES = REPO / 'FAILURE_MODES.md'
MEASURED_START = '<!-- measured-numbers:start -->'
MEASURED_END = '<!-- measured-numbers:end -->'
HISTORICAL_START = '<!-- historical-unverified:start -->'
HISTORICAL_END = '<!-- historical-unverified:end -->'
# Printed, not frozen. requirements.txt does not pin faiss-cpu.
SKIP_KEYS = frozenset({'faiss_cpu_version', 'historical_index_vectors'})
LINE_NEEDLES = {
    'cite_search_def': ('kb_search.py', 'def search('),
    'cite_kb_dir': ('kb_search.py', 'PES2O_KB_DIR'),
    'cite_year_bonus': ('smart_rerank.py', 'YEAR_BONUS'),
    'cite_gap_warn': ('kb_health.py', 'unmerged_hours'),
    'cite_gap_danger': ('kb_health.py', 'unmerged_hours'),
}


def _between(text: str, start: str, end: str) -> tuple[str, str]:
    if start not in text or end not in text:
        raise SystemExit(f'missing markers {start} / {end}')
    if text.index(start) > text.index(end):
        raise SystemExit(f'{start} is after {end}')
    before, rest = text.split(start, 1)
    body, after = rest.split(end, 1)
    return body, before + after


def parse_measured(body: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or line.startswith('`') or ':' not in line:
            continue
        if line.startswith('<!--'):
            continue
        key, value = line.split(':', 1)
        key, value = key.strip(), value.strip()
        if key in found:
            raise SystemExit(f'duplicate measured key {key}')
        found[key] = value
    return found


def compare(evidence: dict, claimed: dict) -> list[str]:
    errors = []
    expected = {key: str(value) for key, value in evidence.items() if key not in SKIP_KEYS}
    for key, value in sorted(expected.items()):
        if key not in claimed:
            errors.append(f'missing measured key {key}')
        elif claimed[key] != value:
            errors.append(f'{key}: README has {claimed[key]!r}, recomputed {value!r}')
    for key in sorted(claimed):
        if key not in expected:
            errors.append(f'README key {key} is not produced by checks/evidence.py')
    return errors


def _has_measurement_digit(line: str) -> bool:
    """Digits that are not inside an identifier (peS2o, python3, PES2O)."""
    return re.search(r'(?<![A-Za-z])\d', line) is not None


def _selftest() -> None:
    if compare({'keep': 1, 'faiss_cpu_version': '9.9.9'}, {'keep': '1'}) != []:
        raise SystemExit('drift detector failed to accept a matching block')
    drifted = compare({'keep': 1}, {'keep': '2'})
    if not drifted:
        raise SystemExit('drift detector accepted a wrong value')
    if not _has_measurement_digit('leak 7'):
        raise SystemExit('digit scan is blind')
    if _has_measurement_digit('peS2o and python3 and PES2O_KB_DIR'):
        raise SystemExit('digit scan flagged corpus and tool names')


def _require(text: str, needle: str, where: str) -> None:
    if needle not in text:
        raise SystemExit(f'{where} is missing {needle!r}')


def main() -> int:
    _selftest()
    evidence = collect()
    readme = README.read_text(encoding='utf-8')
    measured_body, without_measured = _between(readme, MEASURED_START, MEASURED_END)
    historical, outside = _between(without_measured, HISTORICAL_START, HISTORICAL_END)
    claimed = parse_measured(measured_body)
    errors = compare(evidence, claimed)
    if '580,285' in measured_body or '580285' in measured_body:
        errors.append('measured block contains the historical 580,285 figure')
    digit_lines = [
        line.strip() for line in outside.splitlines() if _has_measurement_digit(line)
    ]
    if digit_lines:
        errors.append('README numbers outside the measured and historical blocks:')
        errors.extend(f'  {line}' for line in digit_lines)
    if re.search(r'\.py:\d+', without_measured):
        errors.append('README line-number citation outside the measured block')
    historical_l = historical.lower()
    if '580,285 faiss vectors' not in historical_l and '580,285 FAISS vectors' not in historical:
        errors.append('historical section lost the 580,285 FAISS vectors label')
    if 'historical' not in historical_l and '历史' not in historical:
        errors.append('historical section is not labeled historical')
    if 'not recomputed' not in historical_l and '没有重算' not in historical and '未重算' not in historical:
        errors.append('historical section does not say the figures were not recomputed')

    for key, (rel, needle) in LINE_NEEDLES.items():
        lineno = int(evidence[key])
        line = (REPO / rel).read_text(encoding='utf-8').splitlines()[lineno - 1]
        if needle not in line:
            errors.append(f'{key} line {lineno} of {rel} does not contain {needle!r}: {line!r}')

    modes = FAILURES.read_text(encoding='utf-8')
    phrases = [
        (
            f"fixture scale is {evidence['fixture_main_vectors']} main vectors + "
            f"{evidence['fixture_gap_vectors']} gap vectors, dimension {evidence['fixture_dim']}"
        ),
        '580,285-vector figures are historical',
        'not recomputed by make check',
        'faiss-cpu is not pinned',
        'make check prints faiss_cpu_version',
        'SIGKILL between the two os.replace calls',
        'next merge_to_disk.py run',
        'raised exception does not leave a torn main pair',
        'parent directory is not',
        f"base inner products {evidence['rerank_base_A']} and {evidence['rerank_base_B']}",
        'not a neural embedding',
        'not a retrieval-quality',
        'does not change the health exit code',
        'missing daily_grow_state.json',
        'does not cancel a long-running query',
        'PES2O_KB_DIR',
        'daily_grow timestamp 48 hours old still exits 0',
        'Hashing embeddings are not used',
    ]
    for phrase in phrases:
        if phrase not in modes:
            errors.append(f'FAILURE_MODES.md missing {phrase!r}')
    evidence_src = (REPO / 'checks' / 'evidence.py').read_text(encoding='utf-8')
    if 'daily_grow_hours=48' not in evidence_src:
        errors.append('freshness-48h fixture is not daily_grow_hours=48')

    if errors:
        print('make check failed:', file=sys.stderr)
        for item in errors:
            print(f'- {item}', file=sys.stderr)
        return 1
    print('make check: ok')
    print(f"faiss_cpu_version: {evidence['faiss_cpu_version']}")
    print(
        'fixture: '
        f"{evidence['fixture_main_vectors']}+{evidence['fixture_gap_vectors']} "
        f"dim {evidence['fixture_dim']}; "
        f"faults {evidence['fault_cases_final_consistent']}/{evidence['fault_cases']} consistent"
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
