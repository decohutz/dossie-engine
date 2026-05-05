"""
Unit tests for `parsers.xlsx_financial_parser`.

These tests build small synthetic workbooks with openpyxl and feed them
through the parser. They cover the contract that downstream code (the
orchestrator, the valuation engine, the exporters) relies on:

* Sheet-name routing: only sheets matching ``DRE <name>`` / ``BP <name>``
  patterns are parsed; section dividers, support tabs, macro tabs are
  ignored.
* Year detection: the heading row is found, "2030E"-style suffixes are
  stripped, vertical-analysis duplicate runs are cut.
* Number coercion: BRL absolute values are converted to BRL k.
* Label classification: ratio rows ("margem bruta % RL", "Crescimento
  YoY") are dropped; "Suporte"/"Ref" cells are noise; rows whose values
  all sit in [-1, 1] are heuristically dropped as ratios.
* Entity routing: consolidated sheets land in ``chapter.dre_consolidated``;
  per-BU sheets land in ``chapter.entities``; CSC-style sheets are
  flagged ``non_operating``.
* ``is_projected`` is decided per-year using the detected (or supplied)
  cutoff.

The tests do NOT depend on the real Regenera workbook (that file is under
NDA and lives in the user's local repo, not in version control).
"""
from __future__ import annotations

import pytest

openpyxl = pytest.importorskip("openpyxl")


# ── Helpers ──────────────────────────────────────────────────────────────
def _new_workbook():
    """Fresh openpyxl workbook with the default sheet removed."""
    wb = openpyxl.Workbook()
    # Remove the default 'Sheet'
    default = wb.active
    wb.remove(default)
    return wb


def _add_cover(wb, last_update_year: int = 2024):
    """Add a Cover sheet with a 'Last Update' marker.

    last_update_year controls the year of the date dropped in the cover —
    the parser will treat (last_update_year - 1) as the last actual year.
    """
    from datetime import datetime
    ws = wb.create_sheet("Cover")
    ws["A1"] = "Confidential"
    ws["A2"] = "Test workbook"
    ws["A3"] = "Last Update"
    ws["B3"] = datetime(last_update_year, 12, 1)


def _add_dre_sheet(
    wb,
    name: str,
    *,
    in_sheet_label: str | None = None,
    years: tuple[str, ...] = ("2024", "2025", "2026", "2027", "2028"),
    rows: list[tuple] = None,
    label_col: int = 4,        # col D
    first_year_col: int = 6,   # col F (Regenera-style)
    heading_row: int = 4,
    add_vertical_analysis: bool = False,
):
    """Build a DRE-style sheet.

    Each row in `rows` is a tuple of ``(label, *values)`` where the values
    are aligned with `years`. None values produce blank cells.
    """
    ws = wb.create_sheet(name)
    # Row 2: project tag (optional metadata, parser ignores)
    ws.cell(row=2, column=label_col, value=f"Projeto Test | Projeções {name}")

    # Heading row
    in_label = in_sheet_label if in_sheet_label is not None else name
    ws.cell(row=heading_row, column=label_col, value=in_label)
    ws.cell(row=heading_row, column=label_col + 1, value="Suporte")  # noise col
    for i, y in enumerate(years):
        # Years stored as int unless they have an "E" suffix
        cell_val = int(y) if y.isdigit() else y
        ws.cell(row=heading_row, column=first_year_col + i, value=cell_val)

    # Optional duplicate run (vertical analysis) with a gap
    if add_vertical_analysis:
        gap_col = first_year_col + len(years) + 2  # leave a 2-col gap
        ws.cell(row=heading_row, column=gap_col - 1, value="Ref")
        for i, y in enumerate(years):
            cell_val = int(y) if y.isdigit() else y
            ws.cell(row=heading_row, column=gap_col + i, value=cell_val)

    # Data rows
    for r_offset, row_def in enumerate(rows or [], start=2):
        label, *vals = row_def
        ws.cell(row=heading_row + r_offset, column=label_col, value=label)
        for i, v in enumerate(vals):
            if v is not None:
                ws.cell(row=heading_row + r_offset, column=first_year_col + i, value=v)
        # Mirror values into vertical-analysis block as percentages so the
        # parser is forced to ignore them. Use fractions in [0, 1].
        if add_vertical_analysis:
            gap_col = first_year_col + len(years) + 2
            for i in range(len(vals)):
                ws.cell(row=heading_row + r_offset, column=gap_col + i, value=0.42)
    return ws


def _basic_dre_rows():
    """A minimal but realistic DRE row set."""
    return [
        ("Receita Bruta",            10_000_000, 11_000_000, 12_500_000, 14_000_000, 16_000_000),
        ("Crescimento YoY (%)",      None,       0.10,       0.14,       0.12,       0.14),
        ("Impostos e Deduções",      -1_500_000, -1_650_000, -1_875_000, -2_100_000, -2_400_000),
        ("Receita Liquida",           8_500_000,  9_350_000, 10_625_000, 11_900_000, 13_600_000),
        ("CMV",                      -3_400_000, -3_740_000, -4_250_000, -4_760_000, -5_440_000),
        ("Lucro Bruto",               5_100_000,  5_610_000,  6_375_000,  7_140_000,  8_160_000),
        ("margem bruta % RL",         0.60,       0.60,       0.60,       0.60,       0.60),
        ("SG&A",                     -3_000_000, -3_300_000, -3_750_000, -4_200_000, -4_800_000),
        ("EBITDA",                    2_100_000,  2_310_000,  2_625_000,  2_940_000,  3_360_000),
        ("margem EBITDA % RL",        0.247,      0.247,      0.247,      0.247,      0.247),
    ]


# ── Tests ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("input_val,expected", [
    # Native types
    (1234, 1234.0),
    (1234.5, 1234.5),
    (None, None),
    (True, None),                  # booleans treated as no-data
    # pt-BR strings — thousand separator with comma decimal
    ("1.234.567,89", 1234567.89),
    ("1.234", 1234.0),             # 3-digit tail → thousands
    ("400.000", 400000.0),         # the bug we caught: "(400.000)" was -0.4
    # US strings — comma thousand, dot decimal
    ("1,234,567.89", 1234567.89),
    ("1,234", 1234.0),
    # Decimal-only with non-3 tail (US-style decimal, no thousands)
    ("0.42", 0.42),
    ("1.5", 1.5),
    ("12.34", 12.34),
    # Comma-only is decimal in pt-BR
    ("0,42", 0.42),
    # Negatives in parens
    ("(1.234)", -1234.0),
    ("(400.000)", -400000.0),
    ("(1.234,56)", -1234.56),
    # Cleanups
    ("R$ 1.234,56", 1234.56),
    (" 100 ", 100.0),
    # Sentinels
    ("--", None),
    ("n.a.", None),
    ("", None),
])
def test_coerce_number_handles_various_formats(input_val, expected):
    """Direct unit tests for the number-coercion helper.

    These pin down the contract that everything else in the parser relies on:
    Brazilian thousand-dot ('400.000' → 400000), Brazilian decimal-comma,
    US format with comma thousands, parenthesized negatives, currency
    prefixes, and sentinel strings ('--', 'n.a.').
    """
    from src.parsers.xlsx_financial_parser import _coerce_number
    result = _coerce_number(input_val)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_basic_single_entity(tmp_path):
    """A single 'DRE Foo' sheet produces one entity with the expected lines."""
    from src.parsers.xlsx_financial_parser import parse_xlsx_financials

    wb = _new_workbook()
    _add_cover(wb, last_update_year=2025)  # → cutoff 2024
    _add_dre_sheet(wb, "DRE Foo", rows=_basic_dre_rows())
    path = tmp_path / "test.xlsx"
    wb.save(path)

    result = parse_xlsx_financials(path)

    assert result.last_actual_year == 2024
    assert len(result.chapter.entities) == 1
    foo = result.chapter.entities[0]
    assert foo.name == "Foo"
    assert foo.dre is not None
    assert set(foo.dre.years) == {"2024", "2025", "2026", "2027", "2028"}
    assert foo.non_operating is False


def test_brl_to_k_conversion(tmp_path):
    """Source values in BRL absolute become BRL k (divided by 1000)."""
    from src.parsers.xlsx_financial_parser import parse_xlsx_financials

    wb = _new_workbook()
    _add_cover(wb, last_update_year=2025)
    _add_dre_sheet(wb, "DRE Foo", rows=_basic_dre_rows())
    path = tmp_path / "test.xlsx"
    wb.save(path)

    result = parse_xlsx_financials(path)
    foo = result.chapter.entities[0]
    rb = next(l for l in foo.dre.lines if "Receita Bruta" in l.label)
    assert rb.values["2024"] == pytest.approx(10_000.0)        # 10M → 10k
    assert rb.values["2028"] == pytest.approx(16_000.0)
    assert rb.unit == "BRL k"


def test_ratio_lines_are_filtered(tmp_path):
    """Lines whose label matches a ratio token are excluded from `lines`."""
    from src.parsers.xlsx_financial_parser import parse_xlsx_financials

    wb = _new_workbook()
    _add_cover(wb, last_update_year=2025)
    _add_dre_sheet(wb, "DRE Foo", rows=_basic_dre_rows())
    path = tmp_path / "test.xlsx"
    wb.save(path)

    result = parse_xlsx_financials(path)
    labels = [l.label for l in result.chapter.entities[0].dre.lines]
    # Explicitly check the ratio rows we put in are NOT in the output
    assert not any("margem bruta" in l.lower() for l in labels)
    assert not any("crescimento yoy" in l.lower() for l in labels)
    assert not any("margem ebitda" in l.lower() for l in labels)
    # And that real lines ARE present
    assert any(l == "Receita Bruta" for l in labels)
    assert any(l == "EBITDA" for l in labels)


def test_csc_is_flagged_non_operating(tmp_path):
    """A 'DRE CSC' sheet produces an entity with non_operating=True."""
    from src.parsers.xlsx_financial_parser import parse_xlsx_financials

    wb = _new_workbook()
    _add_cover(wb, last_update_year=2025)
    _add_dre_sheet(wb, "DRE Laces", rows=_basic_dre_rows())
    _add_dre_sheet(wb, "DRE CSC", rows=_basic_dre_rows())
    path = tmp_path / "test.xlsx"
    wb.save(path)

    result = parse_xlsx_financials(path)
    by_name = {e.name: e for e in result.chapter.entities}
    assert by_name["Laces"].non_operating is False
    assert by_name["CSC"].non_operating is True
    # And the parser surfaced an info-level issue about it
    assert any("non-operating" in i.message and i.severity == "info" for i in result.issues)


def test_consolidated_routes_to_dedicated_slot(tmp_path):
    """A 'DRE Consolidado' sheet lands in chapter.dre_consolidated, not in entities."""
    from src.parsers.xlsx_financial_parser import parse_xlsx_financials

    wb = _new_workbook()
    _add_cover(wb, last_update_year=2025)
    _add_dre_sheet(wb, "DRE Foo", rows=_basic_dre_rows())
    # Consolidated sheet — note the in-sheet label says "DRE Grupo" while
    # the sheet name says "DRE Consolidado". This mirrors Regenera.
    _add_dre_sheet(
        wb, "DRE Consolidado",
        in_sheet_label="DRE Grupo",
        rows=_basic_dre_rows(),
    )
    path = tmp_path / "test.xlsx"
    wb.save(path)

    result = parse_xlsx_financials(path)
    assert result.chapter.dre_consolidated is not None
    assert len(result.chapter.entities) == 1   # only "Foo", not Consolidated
    assert result.chapter.entities[0].name == "Foo"


def test_section_dividers_are_skipped(tmp_path):
    """Section-divider sheets like 'DRE por BU >' don't produce entities."""
    from src.parsers.xlsx_financial_parser import parse_xlsx_financials

    wb = _new_workbook()
    _add_cover(wb, last_update_year=2025)
    # Just create empty sheets with these names
    wb.create_sheet("Detalhamento >")
    wb.create_sheet(" DRE por BU >")
    wb.create_sheet("Macro")
    wb.create_sheet("Support")
    _add_dre_sheet(wb, "DRE Foo", rows=_basic_dre_rows())
    path = tmp_path / "test.xlsx"
    wb.save(path)

    result = parse_xlsx_financials(path)
    assert len(result.chapter.entities) == 1
    assert result.chapter.entities[0].name == "Foo"


def test_year_with_e_suffix_is_projected_and_stripped(tmp_path):
    """A '2030E' header column is parsed as projected and labeled '2030'."""
    from src.parsers.xlsx_financial_parser import parse_xlsx_financials

    wb = _new_workbook()
    _add_cover(wb, last_update_year=2025)
    _add_dre_sheet(
        wb, "DRE Foo",
        years=("2024", "2025", "2030E"),
        rows=[("Receita Bruta", 1_000_000, 1_100_000, 1_500_000)],
    )
    path = tmp_path / "test.xlsx"
    wb.save(path)

    result = parse_xlsx_financials(path)
    foo = result.chapter.entities[0]
    assert foo.dre.years == ["2024", "2025", "2030"]
    rb = foo.dre.lines[0]
    assert rb.is_projected == {"2024": False, "2025": True, "2030": True}


def test_vertical_analysis_block_is_ignored(tmp_path):
    """A duplicated year run (Análise Vertical) is cut at the gap."""
    from src.parsers.xlsx_financial_parser import parse_xlsx_financials

    wb = _new_workbook()
    _add_cover(wb, last_update_year=2025)
    _add_dre_sheet(
        wb, "DRE Foo",
        rows=[("Receita Bruta", 1_000_000, 1_100_000, 1_200_000, 1_300_000, 1_400_000)],
        add_vertical_analysis=True,
    )
    path = tmp_path / "test.xlsx"
    wb.save(path)

    result = parse_xlsx_financials(path)
    foo = result.chapter.entities[0]
    # Should still see exactly 5 years — not 10
    assert len(foo.dre.years) == 5


def test_negative_in_parens_is_handled(tmp_path):
    """Strings like '(1.234)' are parsed as -1234."""
    from src.parsers.xlsx_financial_parser import parse_xlsx_financials

    wb = _new_workbook()
    _add_cover(wb, last_update_year=2025)
    # Mix of native negatives and string-formatted negatives.
    # We need at least 3 years for the heading row to be recognized.
    _add_dre_sheet(
        wb, "DRE Foo",
        years=("2024", "2025", "2026"),
        rows=[
            ("Receita Bruta", 1_000_000, 1_100_000, 1_200_000),
            ("CMV", "(400.000)", -440_000, -480_000),  # string-negative, native-negative, native-negative
        ],
    )
    path = tmp_path / "test.xlsx"
    wb.save(path)

    result = parse_xlsx_financials(path)
    cmv = next(l for l in result.chapter.entities[0].dre.lines if l.label == "CMV")
    assert cmv.values["2024"] == pytest.approx(-400.0)   # -400k from "(400.000)"
    assert cmv.values["2025"] == pytest.approx(-440.0)
    assert cmv.values["2026"] == pytest.approx(-480.0)


def test_empty_workbook_yields_error_issue(tmp_path):
    """Workbook with no DRE sheets returns an empty chapter and an error issue."""
    from src.parsers.xlsx_financial_parser import parse_xlsx_financials

    wb = _new_workbook()
    _add_cover(wb)
    wb.create_sheet("Macro")
    path = tmp_path / "empty.xlsx"
    wb.save(path)

    result = parse_xlsx_financials(path)
    assert len(result.chapter.entities) == 0
    assert result.chapter.dre_consolidated is None
    assert any(i.severity == "error" for i in result.issues)


def test_heading_requires_at_least_three_years(tmp_path):
    """A sheet with only 2 year columns is not recognized as a financial table.

    Three years is the parser's minimum: anything less is treated as a
    label/metadata row, not a year header. This keeps random rows that
    happen to contain a couple of integers in the 2000-2099 range from
    being misinterpreted as headings.
    """
    from src.parsers.xlsx_financial_parser import parse_xlsx_financials

    wb = _new_workbook()
    _add_cover(wb, last_update_year=2025)
    _add_dre_sheet(
        wb, "DRE Foo",
        years=("2024", "2025"),   # only 2 years
        rows=[("Receita Bruta", 1_000_000, 1_100_000)],
    )
    path = tmp_path / "test.xlsx"
    wb.save(path)

    result = parse_xlsx_financials(path)
    assert len(result.chapter.entities) == 0
    # And the parser surfaced a warning for the unrecognized sheet
    assert any("could not locate heading row" in i.message for i in result.issues)


def test_last_actual_year_override(tmp_path):
    """Caller can override the cutoff regardless of Cover detection."""
    from src.parsers.xlsx_financial_parser import parse_xlsx_financials

    wb = _new_workbook()
    _add_cover(wb, last_update_year=2025)  # cover would say cutoff=2024
    _add_dre_sheet(
        wb, "DRE Foo",
        years=("2024", "2025", "2026"),
        rows=[("Receita Bruta", 1_000_000, 1_100_000, 1_200_000)],
    )
    path = tmp_path / "test.xlsx"
    wb.save(path)

    # Override: pretend 2025 was already actual
    result = parse_xlsx_financials(path, last_actual_year=2025)
    assert result.last_actual_year == 2025
    rb = result.chapter.entities[0].dre.lines[0]
    assert rb.is_projected == {"2024": False, "2025": False, "2026": True}
