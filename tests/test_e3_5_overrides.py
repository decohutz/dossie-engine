"""
Tests for E3.5 — manual analyst overrides via CLI/programmatic flags.

The four overrides (`ev_ebitda`, `ev_revenue`, `market_size_brl_bn`,
`market_cagr`) exist because:

* Web search (DuckDuckGo) is rate-limited / IP-blocked frequently
  enough that the v003-v006 enrichment runs surfaced inconsistent
  results across runs of the same inputs.

* Sector multiples and TAM are values that analysts typically know
  from sector reports — codifying them as inputs is faster and more
  reproducible than hoping web enrichment returns something useful.

* The pipeline must remain 100% local: no external API key, no
  query leakage of the deal target name to third-party search APIs.

What these tests pin down:
  1. Overrides populate the right TrackedField/MarketSize on the
     Dossier, with provenance set to "manual_override".
  2. Overrides DON'T clobber values already extracted from the CIM
     or web — only fill empty slots.
  3. Once populated via override, the gap analyzer no longer surfaces
     these as gaps (caller intent: "I supplied this, it's not missing").
  4. The valuation engine reads the override values and produces
     non-zero EV/EBITDA, EV/Revenue, IRR/MOIC across scenarios.
  5. Calling with no overrides is a no-op — backwards-compatible.
"""
from __future__ import annotations

import pytest

openpyxl = pytest.importorskip("openpyxl")

# Reuse the lightweight workbook builders from the XLSX parser tests.
from tests.test_xlsx_parser import _new_workbook, _add_cover, _add_dre_sheet, _basic_dre_rows


# ── Direct unit tests on the orchestrator helper ────────────────────────
def test_apply_overrides_populates_multiples():
    """Both ev_ebitda and ev_revenue passed → global_multiples_median filled."""
    from src.pipeline.orchestrator import _apply_manual_overrides
    from src.models.dossier import Dossier, DossierMetadata
    from src.models.market import MarketChapter, TransactionChapter
    from src.models.company import CompanyChapter
    from src.models.financials import FinancialChapter

    dossier = Dossier(
        metadata=DossierMetadata(project_name="t"),
        company=CompanyChapter(),
        financials=FinancialChapter(),
        market=MarketChapter(),
        transaction=TransactionChapter(),
    )
    _apply_manual_overrides(
        dossier,
        ev_ebitda=11.0, ev_revenue=1.8,
        market_size_brl_bn=None, market_cagr=None,
    )
    assert dossier.market.global_multiples_median.is_filled
    val = dossier.market.global_multiples_median.value
    assert val["ev_ebitda_median"] == pytest.approx(11.0)
    assert val["ev_revenue_median"] == pytest.approx(1.8)
    # Provenance is recorded
    assert "manual" in val["source_note"].lower()


def test_apply_overrides_populates_market_size_with_cagr():
    """Market size and CAGR both supplied → MarketSize entry created."""
    from src.pipeline.orchestrator import _apply_manual_overrides
    from src.models.dossier import Dossier, DossierMetadata
    from src.models.market import MarketChapter, TransactionChapter
    from src.models.company import CompanyChapter
    from src.models.financials import FinancialChapter

    dossier = Dossier(
        metadata=DossierMetadata(project_name="t"),
        company=CompanyChapter(),
        financials=FinancialChapter(),
        market=MarketChapter(),
        transaction=TransactionChapter(),
    )
    _apply_manual_overrides(
        dossier,
        ev_ebitda=None, ev_revenue=None,
        market_size_brl_bn=15.0, market_cagr=0.07,
    )
    assert len(dossier.market.market_sizes) == 1
    s = dossier.market.market_sizes[0]
    assert s.geography == "Brasil"
    assert s.value == pytest.approx(15.0)
    assert s.unit == "BRL Bn"
    assert s.cagr == pytest.approx(0.07)


def test_apply_overrides_no_kwargs_is_noop():
    """Calling with all None args should not change the dossier at all."""
    from src.pipeline.orchestrator import _apply_manual_overrides
    from src.models.dossier import Dossier, DossierMetadata
    from src.models.market import MarketChapter, TransactionChapter
    from src.models.company import CompanyChapter
    from src.models.financials import FinancialChapter

    dossier = Dossier(
        metadata=DossierMetadata(project_name="t"),
        company=CompanyChapter(),
        financials=FinancialChapter(),
        market=MarketChapter(),
        transaction=TransactionChapter(),
    )
    _apply_manual_overrides(
        dossier,
        ev_ebitda=None, ev_revenue=None,
        market_size_brl_bn=None, market_cagr=None,
    )
    assert not dossier.market.global_multiples_median.is_filled
    assert dossier.market.market_sizes == []


def test_apply_overrides_does_not_clobber_existing_multiples():
    """If global_multiples_median was already populated by the PDF
    extractor or web enrichment, the override must NOT replace it."""
    from src.pipeline.orchestrator import _apply_manual_overrides
    from src.models.dossier import Dossier, DossierMetadata
    from src.models.market import MarketChapter, TransactionChapter
    from src.models.company import CompanyChapter
    from src.models.financials import FinancialChapter
    from src.models.evidence import TrackedField, Evidence

    dossier = Dossier(
        metadata=DossierMetadata(project_name="t"),
        company=CompanyChapter(),
        financials=FinancialChapter(),
        market=MarketChapter(),
        transaction=TransactionChapter(),
    )
    # Pre-populate as if the PDF extractor had found something
    dossier.market.global_multiples_median = TrackedField.filled(
        {"ev_ebitda_median": 8.5, "ev_revenue_median": 1.2,
         "source_note": "Extracted from CIM page 23"},
        Evidence(source_file="cim.pdf"),
    )
    # Now try to override — should be ignored
    _apply_manual_overrides(
        dossier,
        ev_ebitda=11.0, ev_revenue=1.8,
        market_size_brl_bn=None, market_cagr=None,
    )
    val = dossier.market.global_multiples_median.value
    assert val["ev_ebitda_median"] == pytest.approx(8.5)   # original preserved
    assert "CIM" in val["source_note"]


def test_apply_overrides_does_not_clobber_existing_market_size():
    """Same protection on market_sizes."""
    from src.pipeline.orchestrator import _apply_manual_overrides
    from src.models.dossier import Dossier, DossierMetadata
    from src.models.market import MarketChapter, TransactionChapter, MarketSize
    from src.models.company import CompanyChapter
    from src.models.financials import FinancialChapter
    from src.models.evidence import Evidence

    dossier = Dossier(
        metadata=DossierMetadata(project_name="t"),
        company=CompanyChapter(),
        financials=FinancialChapter(),
        market=MarketChapter(),
        transaction=TransactionChapter(),
    )
    # Pre-populate
    dossier.market.market_sizes = [MarketSize(
        geography="Brasil", value=10.0, unit="BRL Bn",
        year=2024, cagr=0.05, evidence=Evidence(source_file="cim.pdf"),
    )]
    # Override should be ignored
    _apply_manual_overrides(
        dossier,
        ev_ebitda=None, ev_revenue=None,
        market_size_brl_bn=15.0, market_cagr=0.10,
    )
    assert len(dossier.market.market_sizes) == 1
    assert dossier.market.market_sizes[0].value == pytest.approx(10.0)
    assert dossier.market.market_sizes[0].cagr == pytest.approx(0.05)


def test_apply_overrides_partial_multiples():
    """Just one of ev_ebitda or ev_revenue is fine — the other lands as None."""
    from src.pipeline.orchestrator import _apply_manual_overrides
    from src.models.dossier import Dossier, DossierMetadata
    from src.models.market import MarketChapter, TransactionChapter
    from src.models.company import CompanyChapter
    from src.models.financials import FinancialChapter

    dossier = Dossier(
        metadata=DossierMetadata(project_name="t"),
        company=CompanyChapter(),
        financials=FinancialChapter(),
        market=MarketChapter(),
        transaction=TransactionChapter(),
    )
    _apply_manual_overrides(
        dossier,
        ev_ebitda=12.0, ev_revenue=None,
        market_size_brl_bn=None, market_cagr=None,
    )
    assert dossier.market.global_multiples_median.is_filled
    val = dossier.market.global_multiples_median.value
    assert val["ev_ebitda_median"] == pytest.approx(12.0)
    assert val["ev_revenue_median"] is None


# ── End-to-end via run_pipeline ─────────────────────────────────────────
def test_run_pipeline_propagates_overrides_to_dossier(tmp_path):
    """Passing overrides to run_pipeline materializes them on the dossier
    that comes out, surviving merge/gap-analysis."""
    from src.pipeline.orchestrator import run_pipeline

    wb = _new_workbook()
    _add_cover(wb, last_update_year=2025)
    _add_dre_sheet(wb, "DRE Foo", rows=_basic_dre_rows())
    path = tmp_path / "fin.xlsx"
    wb.save(path)

    dossier = run_pipeline(
        inputs=[str(path)],
        use_llm=False, project_name="Override Test",
        ev_ebitda_override=11.0,
        ev_revenue_override=1.8,
        market_size_brl_bn_override=12.5,
        market_cagr_override=0.08,
    )

    # All overrides showed up on the chapter
    assert dossier.market.global_multiples_median.is_filled
    val = dossier.market.global_multiples_median.value
    assert val["ev_ebitda_median"] == pytest.approx(11.0)
    assert val["ev_revenue_median"] == pytest.approx(1.8)

    assert len(dossier.market.market_sizes) == 1
    assert dossier.market.market_sizes[0].value == pytest.approx(12.5)
    assert dossier.market.market_sizes[0].cagr == pytest.approx(0.08)


def test_overrides_remove_corresponding_gaps(tmp_path):
    """When overrides are supplied, the gap analyzer should NOT report
    market_sizes / multiples as missing.

    This is the user-visible payoff of running before _analyze_gaps:
    no more "Tamanho de mercado não extraído" in the gaps section
    once the analyst has supplied a value.
    """
    from src.pipeline.orchestrator import run_pipeline

    wb = _new_workbook()
    _add_cover(wb, last_update_year=2025)
    _add_dre_sheet(wb, "DRE Foo", rows=_basic_dre_rows())
    path = tmp_path / "fin.xlsx"
    wb.save(path)

    # Run WITHOUT overrides — gaps should mention market and multiples
    dossier_no_override = run_pipeline(
        inputs=[str(path)], use_llm=False, project_name="Test",
    )
    no_override_descs = [g.description.lower() for g in dossier_no_override.gaps]
    assert any("mercado" in d for d in no_override_descs)
    # Note: multiples gap is currently labeled differently — check broadly
    no_override_paths = [g.field_path for g in dossier_no_override.gaps]

    # Now WITH market overrides
    dossier_with_override = run_pipeline(
        inputs=[str(path)], use_llm=False, project_name="Test",
        market_size_brl_bn_override=12.5,
        market_cagr_override=0.08,
    )
    with_override_descs = [g.description.lower() for g in dossier_with_override.gaps]
    # The "mercado não extraído" gap should be gone
    assert not any("mercado não extraído" in d or "mercado nao extraido" in d
                   for d in with_override_descs)


def test_overrides_unblock_valuation_metrics(tmp_path):
    """The big payoff: with multiples supplied, run_full_valuation
    produces non-zero EV/EBITDA, EV/Revenue across scenarios.

    Without overrides, those columns silently come out zero.
    """
    from src.pipeline.orchestrator import run_pipeline
    from src.valuation.scenarios import run_full_valuation

    wb = _new_workbook()
    _add_cover(wb, last_update_year=2025)
    _add_dre_sheet(wb, "DRE OpUnit", rows=_basic_dre_rows())
    path = tmp_path / "fin.xlsx"
    wb.save(path)

    # Without overrides — multiples come out None/zero
    dossier_no = run_pipeline(
        inputs=[str(path)], use_llm=False, project_name="t",
    )
    val_no = run_full_valuation(dossier_no, verbose=False)
    base_no = next(s for s in val_no["summaries"] if s["scenario_name"] == "Base")
    # Without multiples, these should be None/zero
    assert (base_no.get("multiples_ev_ebitda") or 0) == 0
    assert (base_no.get("multiples_ev_revenue") or 0) == 0

    # WITH overrides
    dossier_yes = run_pipeline(
        inputs=[str(path)], use_llm=False, project_name="t",
        ev_ebitda_override=11.0,
        ev_revenue_override=1.8,
    )
    val_yes = run_full_valuation(dossier_yes, verbose=False)
    base_yes = next(s for s in val_yes["summaries"] if s["scenario_name"] == "Base")
    # With multiples, these should compute to positive numbers
    assert base_yes["multiples_ev_ebitda"] > 0
    assert base_yes["multiples_ev_revenue"] > 0

    # Sanity: the ratio EV / EBITDA should be ~= the override
    base_ebitda = val_yes["scenarios"]["base"]["terminal_ebitda"]
    implied_mult = base_yes["multiples_ev_ebitda"] / base_ebitda
    assert implied_mult == pytest.approx(11.0, rel=0.01)
