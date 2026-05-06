"""
Tests for E3.1 — PDF-parser relaxation + sector-agnostic classifier.

Two layers are exercised:

1. ``pdf_parser._classify_page`` no longer aggressively promotes any short
   page to ``title``. The new contract: only single-line, very-short
   pages get the ``title`` type; everything substantive (≥2 lines or
   ≥30 chars) is ``content``.

2. ``classifier.classify_pages`` keeps the keyword rules but adds a
   *structural fallback*: if no keyword matches, infer the chapter
   from page-level shape signals (location tags, year-rich text,
   product vocabulary, clusters of person names). The fallback only
   fires when keyword scoring failed, so keyword-classified pages
   keep their original chapter (and confidence ≥ 0.3 boost only
   shows up on structurally-classified ones).

Both layers are tested in isolation with synthetic ``ContentBlock``
inputs — no real PDF needed, sub-second runtime.
"""
from __future__ import annotations

import pytest

from src.parsers.pdf_parser import ContentBlock, _classify_page
from src.pipeline.classifier import classify_pages


# ── Helpers ─────────────────────────────────────────────────────────────
def _make_block(
    page_number: int,
    clean_text: str,
    *,
    tables_found: int = 0,
    page_type: str = "content",
) -> ContentBlock:
    """Mint a ContentBlock with realistic counts derived from clean_text."""
    lines = [l for l in clean_text.split("\n") if l.strip()]
    return ContentBlock(
        page_number=page_number,
        raw_text=clean_text,
        clean_text=clean_text,
        tables_found=tables_found,
        char_count=len(clean_text),
        line_count=len(lines),
        page_type=page_type,
        first_heading=lines[0][:120] if lines else "",
        source_file="test.pdf",
    )


# ── _classify_page ──────────────────────────────────────────────────────
def test_classify_page_empty_is_separator():
    assert _classify_page("", tables_found=0, line_count=0) == "separator"


def test_classify_page_numbered_section_is_separator():
    """A '2. Mercado' divider page stays a separator."""
    assert _classify_page("2. Mercado", tables_found=0, line_count=1) == "separator"


def test_classify_page_obrigado_is_separator():
    """Closing 'Obrigado!' page is a separator (single token closing slide)."""
    assert _classify_page("Obrigado!\n@brand @social", tables_found=0, line_count=2) == "separator"


def test_classify_page_short_single_line_is_title():
    """One-line page under 30 chars is still a title (genuine title cards)."""
    assert _classify_page("Bel Vedere - BH", tables_found=0, line_count=1) == "title"


def test_classify_page_two_line_short_is_now_content_not_title():
    """Critical regression-trap: previously two-line short pages became
    'title' and were skipped by the classifier. This is what discarded
    Bioma's product slides. They must now be 'content'."""
    text = "TONALIZANTE LIVRE DE PPD\nE 80% NATURAL"
    assert _classify_page(text, tables_found=0, line_count=2) == "content"


def test_classify_page_five_line_short_is_now_content_not_title():
    """Same trap, with 5 lines (the old upper bound)."""
    text = "SPRAY, MOUSSE PASTA E EM PÓ;\nFINALIZAÇÃO NATURAL,\nSEM DANOS,\nALTA PERFORMANCE.\nLACES STYLING"
    assert _classify_page(text, tables_found=0, line_count=5) == "content"


def test_classify_page_financial_table_still_detected():
    """Financial table detection is unchanged — still triggered by DRE/BP
    headings combined with year columns."""
    text = "Demonstração de Resultados – Foo\n2021 2022 2023 2024 2025\nReceita 100 110 120 130 140"
    assert _classify_page(text, tables_found=1, line_count=3) == "financial_table"


# ── classifier.classify_pages — structural fallback ─────────────────────
def test_keyword_match_still_dominant():
    """When a keyword matches, structural fallback should NOT override it.

    A page mentioning 'Demonstração de Resultados' should land in
    financials/dre with method='rules', not get re-routed to e.g.
    company.products via structural inference.
    """
    blocks = [
        _make_block(2, "Demonstração de Resultados – Companhia\nReceita Bruta 100 200 300"),
    ]
    [p1] = classify_pages(blocks)
    assert p1.chapter == "financials"
    assert p1.sub_chapter == "dre"
    assert p1.method == "rules"


def test_structural_fallback_location_tag_to_operations():
    """A short page whose only signal is 'Foo - SP' or 'Foo - S P' (the
    pdfplumber-mangled form) is inferred as company.operations.

    We pad with sentinel cover/closing blocks so the actual test pages
    sit in the middle of the deck and aren't subject to the boundary-
    page heuristics.
    """
    blocks = [
        _make_block(1, "Cover"),                    # sentinel
        _make_block(2, "JARDINS - SP"),
        _make_block(3, "MOEMA - S P"),
        _make_block(4, "Bel Vedere - BH"),
        _make_block(99, "Closing"),                 # sentinel
    ]
    classified = classify_pages(blocks)
    # Skip the sentinels, only check pages 2-4
    inner = [p for p in classified if p.block.page_number in (2, 3, 4)]
    for p in inner:
        assert p.chapter == "company", f"page {p.block.page_number} got {p.chapter}"
        assert p.sub_chapter == "operations"
        assert p.method == "structural"
        assert p.confidence == pytest.approx(0.3)


def test_structural_fallback_three_years_to_timeline():
    """A page with ≥3 distinct years and short prose is inferred as
    company.timeline — regardless of whether 'linha do tempo' header
    survived OCR."""
    text = "1920 João Domingos\n1987 Inauguração Morumbi\n2014 Expansão regional\n2023 Bioma Salon"
    blocks = [
        _make_block(1, "Cover"),
        _make_block(2, text),
        _make_block(99, "Closing"),
    ]
    classified = classify_pages(blocks)
    p = next(p for p in classified if p.block.page_number == 2)
    assert p.chapter == "company"
    assert p.sub_chapter == "timeline"


def test_structural_fallback_product_vocabulary_to_products():
    """Generic product vocabulary like 'spray', 'mousse', 'tonalizante'
    routes to company.products even without naming a specific brand."""
    blocks = [
        _make_block(1, "Cover"),
        _make_block(2, "TONALIZANTE LIVRE DE PPD E 80% NATURAL"),
        _make_block(3, "SPRAY MOUSSE PASTA STYLING"),
        _make_block(99, "Closing"),
    ]
    classified = classify_pages(blocks)
    inner = [p for p in classified if p.block.page_number in (2, 3)]
    for p in inner:
        assert p.chapter == "company"
        assert p.sub_chapter == "products"


def test_structural_fallback_two_person_names_to_team():
    """Short page with 2+ Title-Case person names ⇒ company.team."""
    text = "Itamar Cechetto e Cris Dios são fundadores"
    blocks = [
        _make_block(1, "Cover"),
        _make_block(2, text),
        _make_block(99, "Closing"),
    ]
    classified = classify_pages(blocks)
    p = next(p for p in classified if p.block.page_number == 2)
    assert p.chapter == "company"
    assert p.sub_chapter == "team"


def test_structural_fallback_no_signal_stays_unknown():
    """Page with no keyword and no structural signal stays unknown —
    fallback is conservative."""
    blocks = [
        _make_block(1, "Cover"),
        _make_block(2, "lorem ipsum dolor sit amet"),
        _make_block(99, "Closing"),
    ]
    classified = classify_pages(blocks)
    p = next(p for p in classified if p.block.page_number == 2)
    assert p.chapter == "unknown"


def test_structural_fallback_does_not_override_meta():
    """Cover (page 1) and closing (last page) are still meta — the
    structural fallback never gets a chance because cover/closing
    short-circuits earlier in classify_pages."""
    blocks = [
        _make_block(1, "Empresa X | Confidential"),
        _make_block(2, "Some content"),
        _make_block(3, "Obrigado! @social"),
    ]
    classified = classify_pages(blocks)
    assert classified[0].chapter == "meta"
    assert classified[0].sub_chapter == "cover"
    # Closing page comes from page_type='separator' set by the parser when
    # 'Obrigado' is detected; classifier short-circuits and tags 'meta/closing'
    # because page_number == len(blocks). We verify it's NOT classified
    # as a chapter via fallback.
    assert classified[2].chapter in ("meta", "skip")


# ── Frank-keyword retrocompat (no regression on Frank-flavored pages) ───
def test_frank_competitor_keywords_still_route_to_market_competitors():
    """The Frank-specific 'Óticas Carol' keyword is preserved as a bonus
    on market.competitors, so Frank's competitor pages keep classifying
    correctly."""
    blocks = [_make_block(2, "Top 5 brasileiros: Óticas Carol e Chilli Beans lideram")]
    [p] = classify_pages(blocks)
    assert p.chapter == "market"
    assert p.sub_chapter == "competitors"


def test_frank_essilor_keyword_still_routes_to_global_players():
    blocks = [_make_block(2, "EssilorLuxottica e Warby Parker são players globais relevantes")]
    [p] = classify_pages(blocks)
    assert p.chapter == "market"
    assert p.sub_chapter == "global_players"
