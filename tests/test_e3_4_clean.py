"""
Tests for E3.4 — fixes B1 (legal_name false-positive) and B5/B6 (DRE
block bleeding into the assumptions section beneath it).

Three concerns are pinned down here:

* ``extract_legal_name`` rejects degenerate matches arising from
  decorative-font slides where letter-tracking spreads "CASA" into
  "CA S A" and the trailing "S A" is misread as the legal-suffix
  pattern. We require ≥2 substantive body tokens (≥3 letters each)
  before the suffix.

* The XLSX parser stops reading line items at the first "Premissas"
  / "Notas" / "Drivers" section header. Without this terminator,
  rows from the assumption-driver section beneath the DRE — like
  "Salário Médio ($)" or "Comissão por novo contrato ($)" — leak
  into the dossier as DRE line items.

* The expanded ratio-token list catches additional Brazilian
  variations like "Alíquota efetiva", "% do EBT", "Taxa de
  Crescimento YoY%".
"""
from __future__ import annotations

import pytest

openpyxl = pytest.importorskip("openpyxl")

from tests.test_xlsx_parser import _new_workbook, _add_cover, _add_dre_sheet, _basic_dre_rows


# ── B1: legal_name false-positive ────────────────────────────────────────
def test_legal_name_rejects_decorative_font_fragment():
    """Bug B1 from Regenera v002+v003: slide 19 of Bioma renders 'CASA'
    in a decorative font with extreme letter-tracking, and pdfplumber
    extracts it as 'CA S A L A CES - S P'. The legal-name regex matched
    'CA S A' as a candidate because 'S A' looks like a Brazilian S.A.
    legal suffix and 'CA' was a single body token. The fix requires at
    least 2 body tokens of ≥3 alphabetic chars each.
    """
    from src.parsers.profile_parser import extract_legal_name
    assert extract_legal_name("CA S A L A CES - S P") is None


def test_legal_name_keeps_real_brazilian_companies():
    """Sanity check: the tightened filter doesn't reject real names.

    Frank's disclaimer is the load-bearing positive case — if this
    assertion fails, the Frank regression suite would also fail (which
    runs `extract_legal_name` indirectly via the rules extractor).
    """
    from src.parsers.profile_parser import extract_legal_name
    cases = [
        # Frank: 5 body tokens
        ("MERCADÃO DOS ÓCULOS SOL E GRAU FRANCHISING LTDA",
         "MERCADÃO DOS ÓCULOS SOL E GRAU FRANCHISING LTDA"),
        # Two body tokens — minimum acceptable
        ("ACME COMERCIAL LTDA", "ACME COMERCIAL LTDA"),
        # S.A. variant
        ("FOO BAR S.A.", "FOO BAR S.A"),  # regex strips trailing dot
        # Very long real name
        ("MEGA INDÚSTRIA E COMÉRCIO LTDA", "MEGA INDÚSTRIA E COMÉRCIO LTDA"),
    ]
    for text, expected in cases:
        result = extract_legal_name(text)
        assert result == expected, f"on {text!r}: got {result!r}, expected {expected!r}"


def test_legal_name_rejects_other_degenerate_fragments():
    """Other variants of the same trap — short or single-letter body tokens
    that happen to end in something the suffix regex accepts."""
    from src.parsers.profile_parser import extract_legal_name
    # Single body token before LTDA — could be a noisy match
    assert extract_legal_name("ACME LTDA") is None
    # All single-letter body tokens — degenerate
    assert extract_legal_name("X Y S A") is None
    assert extract_legal_name("AS S A") is None


def test_legal_name_still_skips_advisor_self_references():
    """The advisor filter must still kick in (regression check on the
    pre-existing behavior, just to make sure the new body-token filter
    didn't accidentally let advisor names through)."""
    from src.parsers.profile_parser import extract_legal_name
    assert extract_legal_name("VALUE CAPITAL ADVISORS LTDA") is None
    assert extract_legal_name("ASSESSORIA FINANCEIRA TAL LTDA") is None


# ── B5/B6: DRE block terminator ──────────────────────────────────────────
def test_dre_parser_stops_at_premissas_section(tmp_path):
    """When a 'Premissas' divider row appears in the label column, the
    parser should stop reading further rows — anything below is
    assumption metadata, not DRE.
    """
    from src.parsers.xlsx_financial_parser import parse_xlsx_financials

    # Build a DRE that has 4 real lines, then a Premissas section with
    # rows that look line-item-ish but shouldn't be picked up.
    rows = [
        ("Receita Bruta",   1_000_000, 1_100_000, 1_200_000),
        ("CMV",             -400_000,  -440_000,  -480_000),
        ("EBITDA",          200_000,   220_000,   240_000),
        ("Lucro Líquido",   100_000,   110_000,   120_000),
        # Section divider: just the word "Premissas"
        ("Premissas",       None,       None,       None),
        # These should NOT show up in the parsed output
        ("Salário Médio ($)",       -180,    -190,    -200),
        ("Número de funcionários (#)", 50,    55,      60),
        ("Comissão por novo contrato ($)", -3, -3,    -3),
    ]
    wb = _new_workbook()
    _add_cover(wb, last_update_year=2025)
    _add_dre_sheet(wb, "DRE Foo", years=("2024", "2025", "2026"), rows=rows)
    path = tmp_path / "test.xlsx"
    wb.save(path)

    result = parse_xlsx_financials(path)
    foo = next(e for e in result.chapter.entities if e.name == "Foo")
    labels = [l.label for l in foo.dre.lines]
    # The four real lines should be there
    assert "Receita Bruta" in labels
    assert "CMV" in labels
    assert "EBITDA" in labels
    assert "Lucro Líquido" in labels
    # And nothing from below the divider
    assert not any("Salário" in l for l in labels)
    assert not any("Número de funcionários" in l for l in labels)
    assert not any("Comissão por novo contrato" in l for l in labels)
    # And the divider itself isn't kept either
    assert "Premissas" not in labels


def test_dre_parser_stops_at_drivers_or_notas_too(tmp_path):
    """Other terminator markers besides 'Premissas' should also work —
    Brazilian decks vary on this label."""
    from src.parsers.xlsx_financial_parser import parse_xlsx_financials

    for terminator in ("Notas", "Drivers", "Assumptions"):
        wb = _new_workbook()
        _add_cover(wb, last_update_year=2025)
        rows = [
            ("Receita Bruta", 1_000_000, 1_100_000, 1_200_000),
            ("EBITDA",        200_000,   220_000,   240_000),
            (terminator,      None, None, None),
            ("Bogus row",     999_999,   999_999,   999_999),
        ]
        _add_dre_sheet(wb, "DRE Foo", years=("2024", "2025", "2026"), rows=rows)
        path = tmp_path / f"test_{terminator}.xlsx"
        wb.save(path)

        result = parse_xlsx_financials(path)
        foo = next(e for e in result.chapter.entities if e.name == "Foo")
        labels = [l.label for l in foo.dre.lines]
        assert "Bogus row" not in labels, f"didn't stop at terminator '{terminator}'"


def test_dre_parser_does_not_stop_on_compound_premissas_label(tmp_path):
    """A line item that *contains* 'premissas' as part of a larger label
    (e.g. 'Premissas — CMV') should NOT be treated as a terminator; it's
    a legitimate sub-row. The terminator is exact-after-normalize only.
    """
    from src.parsers.xlsx_financial_parser import parse_xlsx_financials

    wb = _new_workbook()
    _add_cover(wb, last_update_year=2025)
    rows = [
        ("Receita Bruta",        1_000_000, 1_100_000, 1_200_000),
        ("Premissas — CMV",      -400_000,  -440_000,  -480_000),  # not bare 'Premissas'
        ("EBITDA",               200_000,   220_000,   240_000),
    ]
    _add_dre_sheet(wb, "DRE Foo", years=("2024", "2025", "2026"), rows=rows)
    path = tmp_path / "test.xlsx"
    wb.save(path)

    result = parse_xlsx_financials(path)
    foo = next(e for e in result.chapter.entities if e.name == "Foo")
    labels = [l.label for l in foo.dre.lines]
    # All three should be present — "Premissas — CMV" is a real line, EBITDA below
    assert "Premissas — CMV" in labels
    assert "EBITDA" in labels


# ── Expanded ratio tokens ──────────────────────────────────────────────
@pytest.mark.parametrize("ratio_label", [
    "% do EBT",
    "Alíquota de imposto",
    "Taxa de Crescimento YoY%",
    "margem EBIT % RL",
    "margem líquida % da RL",
])
def test_expanded_ratio_tokens_filter_more_variations(tmp_path, ratio_label):
    """The expanded ratio-token list catches Brazilian variations that
    the v003 parser was leaking through (sometimes appearing in the
    output as bizarre rows like 'margem EBIT % RL = 0.45')."""
    from src.parsers.xlsx_financial_parser import parse_xlsx_financials

    wb = _new_workbook()
    _add_cover(wb, last_update_year=2025)
    rows = [
        ("Receita Bruta",     1_000_000, 1_100_000, 1_200_000),
        ("EBITDA",            200_000,   220_000,   240_000),
        (ratio_label,         0.20,      0.20,      0.20),
    ]
    _add_dre_sheet(wb, "DRE Foo", years=("2024", "2025", "2026"), rows=rows)
    path = tmp_path / "test.xlsx"
    wb.save(path)

    result = parse_xlsx_financials(path)
    foo = next(e for e in result.chapter.entities if e.name == "Foo")
    labels = [l.label for l in foo.dre.lines]
    assert ratio_label not in labels, f"ratio {ratio_label!r} leaked through"
