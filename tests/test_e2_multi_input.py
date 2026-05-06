"""
E2 multi-input pipeline tests.

These tests cover the contract of `run_pipeline` after E2:

* The legacy ``pdf_path=`` keyword still works (used by conftest.py and
  test_full_dossier.py — preserving it is what guarantees the Frank
  regression tests don't break).
* The new ``inputs=[...]`` keyword routes by file extension.
* PDF + XLSX merge follows the agreed policy: XLSX wins on financial data,
  PDF still drives soft chapters (profile, executives, market, transaction).
* Argument validation: missing files, mutual exclusion of `inputs` and
  `pdf_path`, unsupported extensions, multiple PDFs.

Heavy LLM/PDF work is sidestepped by patching the relevant pipeline stages
where possible — these tests should stay fast (sub-second) and shouldn't
require Ollama or the Frank PDF.
"""
from __future__ import annotations

import pytest
from datetime import datetime

openpyxl = pytest.importorskip("openpyxl")


# Reuse the helpers from the XLSX parser tests for building synthetic books.
from tests.test_xlsx_parser import _new_workbook, _add_cover, _add_dre_sheet, _basic_dre_rows


# ── Argument validation ─────────────────────────────────────────────────
def test_run_pipeline_requires_some_input():
    from src.pipeline.orchestrator import run_pipeline
    with pytest.raises(ValueError, match="requires either"):
        run_pipeline()


def test_run_pipeline_rejects_both_inputs_and_pdf_path(tmp_path):
    from src.pipeline.orchestrator import run_pipeline
    fake_pdf = tmp_path / "a.pdf"
    fake_pdf.write_bytes(b"")
    with pytest.raises(ValueError, match="not both"):
        run_pipeline(pdf_path=str(fake_pdf), inputs=[str(fake_pdf)])


def test_run_pipeline_rejects_unknown_extension(tmp_path):
    from src.pipeline.orchestrator import run_pipeline
    bad = tmp_path / "weird.csv"
    bad.write_text("a,b\n1,2\n")
    with pytest.raises(ValueError, match="unrecognized input file extension"):
        run_pipeline(inputs=[str(bad)])


def test_run_pipeline_rejects_multiple_pdfs(tmp_path):
    from src.pipeline.orchestrator import run_pipeline
    p1 = tmp_path / "a.pdf"
    p2 = tmp_path / "b.pdf"
    p1.write_bytes(b"")
    p2.write_bytes(b"")
    with pytest.raises(ValueError, match="multiple PDF inputs"):
        run_pipeline(inputs=[str(p1), str(p2)])


# ── XLSX-only path (no PDF) ─────────────────────────────────────────────
def test_xlsx_only_input_produces_dossier_with_financials_only(tmp_path):
    """A run with just XLSX produces a dossier whose financials come from
    the XLSX and whose soft chapters are empty (and therefore surface as
    gaps later)."""
    from src.pipeline.orchestrator import run_pipeline

    wb = _new_workbook()
    _add_cover(wb, last_update_year=2025)
    _add_dre_sheet(wb, "DRE Foo", rows=_basic_dre_rows())
    _add_dre_sheet(wb, "DRE Bar", rows=_basic_dre_rows())
    xlsx_path = tmp_path / "financials.xlsx"
    wb.save(xlsx_path)

    dossier = run_pipeline(inputs=[str(xlsx_path)], use_llm=False)

    # Financials populated from XLSX
    names = sorted(e.name for e in dossier.financials.entities)
    assert names == ["Bar", "Foo"]

    # Soft chapters empty
    assert dossier.company.profile.legal_name.value in (None, "")
    assert dossier.metadata.target_company == "Unknown"

    # Source files reflect the actual input
    assert dossier.metadata.source_files == ["financials.xlsx"]


def test_xlsx_only_default_project_name_from_filename(tmp_path):
    from src.pipeline.orchestrator import run_pipeline
    wb = _new_workbook()
    _add_cover(wb, last_update_year=2025)
    _add_dre_sheet(wb, "DRE Foo", rows=_basic_dre_rows())
    xlsx_path = tmp_path / "Projeto_Test_Financials.xlsx"
    wb.save(xlsx_path)

    dossier = run_pipeline(inputs=[str(xlsx_path)], use_llm=False)
    # Underscores → spaces, extension dropped
    assert dossier.metadata.project_name == "Projeto Test Financials"


# ── Merge helper, in isolation ──────────────────────────────────────────
def _make_chapter_with_entity(name: str, *, dre_label: str, value_2024: float,
                              non_op: bool = False):
    """Build a tiny FinancialChapter with one entity and one DRE line."""
    from src.models.financials import (
        FinancialChapter, FinancialEntity, FinancialStatement, FinancialLine,
    )
    line = FinancialLine(
        label=dre_label,
        values={"2024": value_2024},
        is_projected={"2024": False},
        unit="BRL k",
    )
    stmt = FinancialStatement(
        entity_name=name, statement_type="dre",
        lines=[line], years=["2024"],
    )
    ch = FinancialChapter()
    ch.entities.append(FinancialEntity(name=name, dre=stmt, non_operating=non_op))
    return ch


def test_merge_xlsx_replaces_existing_dre(tmp_path):
    """When PDF and XLSX both have a DRE for the same entity, XLSX wins."""
    from src.pipeline.orchestrator import _merge_xlsx_financials

    pdf_chapter = _make_chapter_with_entity("Foo", dre_label="Receita Bruta", value_2024=999.0)

    # Build an XLSX where Foo's Receita Bruta is 12.5M (→ 12500 k)
    wb = _new_workbook()
    _add_cover(wb, last_update_year=2025)
    _add_dre_sheet(
        wb, "DRE Foo",
        rows=[
            ("Receita Bruta", 12_500_000, 13_000_000, 14_000_000),
        ],
        years=("2024", "2025", "2026"),
    )
    xlsx_path = tmp_path / "fin.xlsx"
    wb.save(xlsx_path)

    merged = _merge_xlsx_financials(base=pdf_chapter, xlsx_path=str(xlsx_path))

    foo = merged.get_entity("Foo")
    assert foo is not None
    rb = next(l for l in foo.dre.lines if "Receita Bruta" in l.label)
    # XLSX value (12500), not the PDF value (999)
    assert rb.values["2024"] == pytest.approx(12_500.0)


def test_merge_xlsx_adds_new_entities_keeps_pdf_only_ones(tmp_path):
    """Entities only in PDF stay; entities only in XLSX get added."""
    from src.pipeline.orchestrator import _merge_xlsx_financials

    # PDF has Foo and Baz
    pdf_chapter = _make_chapter_with_entity("Foo", dre_label="X", value_2024=1.0)
    # add Baz manually
    from src.models.financials import (
        FinancialEntity, FinancialStatement, FinancialLine,
    )
    pdf_chapter.entities.append(
        FinancialEntity(
            name="Baz",
            dre=FinancialStatement(
                entity_name="Baz", statement_type="dre",
                lines=[FinancialLine(label="X", values={"2024": 7.0},
                                     is_projected={"2024": False}, unit="BRL k")],
                years=["2024"],
            ),
        )
    )

    # XLSX has Foo (overwrites) and Bar (new)
    wb = _new_workbook()
    _add_cover(wb, last_update_year=2025)
    _add_dre_sheet(wb, "DRE Foo", rows=_basic_dre_rows())
    _add_dre_sheet(wb, "DRE Bar", rows=_basic_dre_rows())
    xlsx_path = tmp_path / "fin.xlsx"
    wb.save(xlsx_path)

    merged = _merge_xlsx_financials(base=pdf_chapter, xlsx_path=str(xlsx_path))

    names = sorted(e.name for e in merged.entities)
    # Foo (overwritten), Baz (kept from PDF), Bar (added from XLSX)
    assert names == ["Bar", "Baz", "Foo"]


def test_merge_xlsx_promotes_non_operating_flag(tmp_path):
    """If XLSX flags an entity non-operating, that flag wins post-merge."""
    from src.pipeline.orchestrator import _merge_xlsx_financials

    # PDF chapter has CSC as a regular operating entity (non_operating=False).
    pdf_chapter = _make_chapter_with_entity("CSC", dre_label="X", value_2024=1.0)

    # XLSX has CSC and the parser auto-flags it non-operating.
    wb = _new_workbook()
    _add_cover(wb, last_update_year=2025)
    _add_dre_sheet(wb, "DRE CSC", rows=_basic_dre_rows())
    xlsx_path = tmp_path / "fin.xlsx"
    wb.save(xlsx_path)

    merged = _merge_xlsx_financials(base=pdf_chapter, xlsx_path=str(xlsx_path))
    csc = merged.get_entity("CSC")
    assert csc is not None
    assert csc.non_operating is True


def test_merge_xlsx_replaces_consolidated_outright(tmp_path):
    """An XLSX-derived consolidated DRE replaces any pre-existing one."""
    from src.pipeline.orchestrator import _merge_xlsx_financials
    from src.models.financials import FinancialStatement, FinancialLine

    pdf_chapter = _make_chapter_with_entity("Foo", dre_label="X", value_2024=1.0)
    pdf_chapter.dre_consolidated = FinancialStatement(
        entity_name="PDF Group", statement_type="dre",
        lines=[FinancialLine(label="Receita", values={"2024": 1.0},
                             is_projected={"2024": False}, unit="BRL k")],
        years=["2024"],
    )

    wb = _new_workbook()
    _add_cover(wb, last_update_year=2025)
    _add_dre_sheet(
        wb, "DRE Consolidado",
        in_sheet_label="DRE Grupo",
        rows=_basic_dre_rows(),
    )
    xlsx_path = tmp_path / "fin.xlsx"
    wb.save(xlsx_path)

    merged = _merge_xlsx_financials(base=pdf_chapter, xlsx_path=str(xlsx_path))

    assert merged.dre_consolidated is not None
    # The XLSX consolidated has the synthetic _basic_dre_rows() — Receita Bruta
    # of 10M which becomes 10000k. The PDF one had 1.0. Verify XLSX won.
    rb = next(l for l in merged.dre_consolidated.lines if l.label == "Receita Bruta")
    assert rb.values["2024"] == pytest.approx(10_000.0)


# ── Legacy interface preservation ───────────────────────────────────────
def test_legacy_pdf_path_kwarg_still_dispatches_correctly(monkeypatch, tmp_path):
    """The kwarg `pdf_path=...` keeps working — this is what conftest uses
    for the Frank regression suite. We don't need a real PDF to test the
    dispatch logic; we just check that the orchestrator translates
    pdf_path→inputs correctly. Heavy parsers are stubbed."""
    from src.pipeline import orchestrator
    from src.models.company import CompanyChapter
    from src.models.market import MarketChapter, TransactionChapter
    from src.models.financials import FinancialChapter

    fake_pdf = tmp_path / "fake.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 stub")

    # Stub every heavy step so we can reach the dispatch logic without
    # actually running pdfplumber, classifier, or LLM.
    captured = {}

    def fake_parse_pdf(path):
        captured["parse_pdf_path"] = path
        return []
    def fake_classify(blocks):
        return []
    def fake_extract_financials(classified, source_file):
        captured["financials_source"] = source_file
        return FinancialChapter()
    def fake_extract_with_rules(classified, source_file):
        return CompanyChapter(), MarketChapter(), TransactionChapter()

    monkeypatch.setattr(orchestrator, "parse_pdf", fake_parse_pdf)
    monkeypatch.setattr(orchestrator, "classify_pages", fake_classify)
    monkeypatch.setattr(orchestrator, "_extract_financials", fake_extract_financials)
    monkeypatch.setattr(orchestrator, "_extract_with_rules", fake_extract_with_rules)

    dossier = orchestrator.run_pipeline(
        pdf_path=str(fake_pdf), use_llm=False, project_name="Legacy Test",
    )
    assert captured["parse_pdf_path"] == str(fake_pdf)
    assert captured["financials_source"] == "fake.pdf"
    assert dossier.metadata.source_files == ["fake.pdf"]
    assert dossier.metadata.project_name == "Legacy Test"
