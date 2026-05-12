"""
Shared helpers for regression tests.

Kept generic so adding a second CIM fixture later only requires new fixture
files, not new helper code.
"""
from __future__ import annotations

import re
from typing import Any


def deep_diff(
    actual: Any,
    expected: Any,
    *,
    float_tol: float = 0.01,
    path: str = "",
) -> list[str]:
    """Recursively compare two JSON-like structures.

    Returns a list of human-readable diff strings. Empty list means identical.

    Args:
        actual: The value produced by the code under test.
        expected: The golden baseline value.
        float_tol: Absolute tolerance for float comparisons (default 0.01).
        path: Internal — cumulative JSON path to the current node.
    """
    out: list[str] = []

    if type(actual) is not type(expected):
        out.append(
            f"{path}: TYPE {type(actual).__name__} vs {type(expected).__name__}"
        )
        return out

    if isinstance(actual, dict):
        keys = set(actual.keys()) | set(expected.keys())
        for k in sorted(keys):
            sub_path = f"{path}.{k}" if path else k
            if k not in actual:
                out.append(f"{sub_path}: MISSING in actual")
            elif k not in expected:
                out.append(f"{sub_path}: MISSING in expected")
            else:
                out.extend(
                    deep_diff(actual[k], expected[k], float_tol=float_tol, path=sub_path)
                )
        return out

    if isinstance(actual, list):
        if len(actual) != len(expected):
            out.append(f"{path}: LEN {len(actual)} vs {len(expected)}")
        for i, (a, e) in enumerate(zip(actual, expected)):
            out.extend(
                deep_diff(a, e, float_tol=float_tol, path=f"{path}[{i}]")
            )
        return out

    if isinstance(actual, float) and isinstance(expected, float):
        if abs(actual - expected) > float_tol:
            out.append(f"{path}: {actual} vs {expected} (|Δ|={abs(actual-expected):.4f})")
        return out

    if actual != expected:
        out.append(f"{path}: {actual!r} vs {expected!r}")
    return out


def extract_financial_md_section(md_text: str) -> str:
    """Extract only '## 2. Informações financeiras ... (until ## 3. Mercado)'.

    The financial section is what the refactor actually touches; the rest of
    the markdown (company profile, market narrative, transaction context) has
    LLM variability and can't be compared byte-for-byte across runs.
    """
    m = re.search(
        r"(## 2\. Informações financeiras.*?)(?=## 3\. Mercado)",
        md_text,
        re.DOTALL,
    )
    if not m:
        raise AssertionError("Markdown missing '## 2. Informações financeiras' section")
    return m.group(1)


def read_sheet(workbook, sheet_name: str) -> list[list]:
    """Dump a worksheet into a list-of-lists of raw cell values.

    Cell styles/fonts are intentionally not compared — only values matter
    for regression purposes.
    """
    ws = workbook[sheet_name]
    return [
        [ws.cell(row=r, column=c).value for c in range(1, ws.max_column + 1)]
        for r in range(1, ws.max_row + 1)
    ]


def first_cell_diff(actual: list[list], expected: list[list]) -> str | None:
    """Locate the first differing cell, for useful pytest failure messages."""
    if len(actual) != len(expected):
        return f"row count {len(actual)} vs {len(expected)}"
    for r, (row_a, row_e) in enumerate(zip(actual, expected), start=1):
        if len(row_a) != len(row_e):
            return f"row {r}: col count {len(row_a)} vs {len(row_e)}"
        for c, (a, e) in enumerate(zip(row_a, row_e), start=1):
            if a != e:
                return f"row {r} col {c}: {a!r} vs {e!r}"
    return None
