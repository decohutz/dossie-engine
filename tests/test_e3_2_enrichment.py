"""
Tests for E3.2 — web enrichment expanded to cover market sizing,
competitor lists, and sector trading multiples.

The enrichers each follow the same pattern:
  search_X(...) → raw text snippets
  → LLM extracts structured JSON
  → JSON is mapped onto MarketChapter fields
  → corresponding gaps are removed by _update_gaps

We mock both layers so the tests run without internet or Ollama:
* the source functions are monkey-patched to return fixed snippet text
* the OllamaClient is replaced with a stub that returns canned JSON

This pins down two contracts:
1. When the search returns useful snippets and the LLM extracts good
   JSON, the chapter fields get populated and the gap analyzer
   consumes them as filled.
2. When the search returns nothing, when the LLM fails, or when the
   sector is unknown, the enrichers fail closed — no crashes, no
   spurious entries.
"""
from __future__ import annotations

import pytest


# ── Test fixtures ───────────────────────────────────────────────────────
class _StubLLM:
    """OllamaClient stub. Returns whatever was set in `responses` for
    each successive call. is_available() always returns True.
    """
    def __init__(self, responses: list[dict] | None = None):
        self.responses = list(responses or [])
        self.calls: list[tuple[str, str]] = []

    def is_available(self):
        return True

    def extract_json(self, prompt: str, system: str = ""):
        self.calls.append((prompt, system))
        if self.responses:
            return self.responses.pop(0)
        return None


def _make_test_dossier(*, sector: str = "Beleza e Cuidados Pessoais",
                      trade_name: str = "Laces"):
    """Build a minimal dossier suitable for enrichment tests."""
    from src.models.dossier import Dossier, DossierMetadata
    from src.models.company import CompanyChapter
    from src.models.market import MarketChapter, TransactionChapter
    from src.models.financials import FinancialChapter
    from src.models.evidence import TrackedField, Evidence

    company = CompanyChapter()
    if trade_name:
        company.profile.trade_name = TrackedField.filled(
            trade_name, Evidence(source_file="test.pdf"),
        )
    if sector:
        company.profile.sector = TrackedField.filled(
            sector, Evidence(source_file="test.pdf"),
        )

    return Dossier(
        metadata=DossierMetadata(
            project_name="Test", target_company=trade_name,
        ),
        company=company,
        financials=FinancialChapter(),
        market=MarketChapter(),
        transaction=TransactionChapter(),
    )


# ── _enrich_market ──────────────────────────────────────────────────────
def test_enrich_market_populates_market_sizes(monkeypatch):
    """When search returns snippets and LLM extracts a list of sizes,
    they're appended to market.market_sizes."""
    from src.enrichment import enricher

    # Mock the search function to return fixed text
    def fake_search(sector, geography="Brasil", verbose=False):
        return {
            "text": f"Mercado {sector} no Brasil de R$ 12.5 bilhões em 2024 com CAGR 8%",
            "url": "https://example.com/report",
            "source": "market_size_search",
        }
    monkeypatch.setattr(enricher, "search_market_size", fake_search)

    llm = _StubLLM(responses=[{
        "market_sizes": [
            {"geography": "Brasil", "value": 12.5, "unit": "BRL Bn", "year": 2024, "cagr": 0.08},
            {"geography": "Global", "value": 200.0, "unit": "USD Bn", "year": 2024, "cagr": 0.05},
        ],
        "summary": "Mercado em expansão sustentável",
    }])

    dossier = _make_test_dossier()
    enricher._enrich_market(dossier, "Laces", llm, verbose=False)

    assert len(dossier.market.market_sizes) == 2
    br = next(s for s in dossier.market.market_sizes if s.geography == "Brasil")
    assert br.value == pytest.approx(12.5)
    assert br.unit == "BRL Bn"
    assert br.year == 2024
    assert br.cagr == pytest.approx(0.08)


def test_enrich_market_skips_when_sector_unknown(monkeypatch):
    """No sector → no search performed → no entries added."""
    from src.enrichment import enricher

    called = {"yes": False}
    def fake_search(*a, **k):
        called["yes"] = True
        return None
    monkeypatch.setattr(enricher, "search_market_size", fake_search)

    dossier = _make_test_dossier(sector="")
    enricher._enrich_market(dossier, "Laces", _StubLLM(), verbose=False)

    assert called["yes"] is False
    assert dossier.market.market_sizes == []


def test_enrich_market_does_not_overwrite_existing(monkeypatch):
    """If the dossier already has market sizes (e.g. PDF-extracted),
    we don't overwrite them with web-derived data."""
    from src.enrichment import enricher
    from src.models.market import MarketSize
    from src.models.evidence import Evidence

    fake_search_called = {"yes": False}
    def fake_search(*a, **k):
        fake_search_called["yes"] = True
        return None
    monkeypatch.setattr(enricher, "search_market_size", fake_search)

    dossier = _make_test_dossier()
    dossier.market.market_sizes = [
        MarketSize(geography="Brasil", value=10.0, unit="BRL Bn",
                   year=2024, evidence=Evidence(source_file="cim.pdf")),
    ]
    enricher._enrich_market(dossier, "Laces", _StubLLM(), verbose=False)

    assert fake_search_called["yes"] is False  # short-circuited
    assert len(dossier.market.market_sizes) == 1
    assert dossier.market.market_sizes[0].evidence.source_file == "cim.pdf"


def test_enrich_market_drops_invalid_entries(monkeypatch):
    """Entries with non-numeric or zero values are silently dropped —
    we never inject zero-valued market sizes."""
    from src.enrichment import enricher

    def fake_search(*a, **k):
        return {"text": "stuff", "url": "u", "source": "s"}
    monkeypatch.setattr(enricher, "search_market_size", fake_search)

    llm = _StubLLM(responses=[{
        "market_sizes": [
            {"geography": "Brasil", "value": 12.5, "unit": "BRL Bn", "year": 2024},
            {"geography": "Bogus", "value": 0, "unit": "BRL Bn", "year": 2024},        # zero → drop
            {"geography": "Bogus", "value": "not a number", "unit": "BRL Bn"},          # non-numeric → drop
            "garbage non-dict entry",                                                    # not a dict → drop
        ],
        "summary": "ok",
    }])

    dossier = _make_test_dossier()
    enricher._enrich_market(dossier, "Laces", llm, verbose=False)

    assert len(dossier.market.market_sizes) == 1
    assert dossier.market.market_sizes[0].geography == "Brasil"


# ── _enrich_competitors ─────────────────────────────────────────────────
def test_enrich_competitors_populates_list(monkeypatch):
    from src.enrichment import enricher

    def fake_search(*a, **k):
        return {"text": "Concorrentes do setor de beleza...", "url": "u", "source": "s"}
    monkeypatch.setattr(enricher, "search_competitors", fake_search)

    llm = _StubLLM(responses=[{
        "competitors": [
            {"name": "Concorrente Um", "stores": 100, "revenue": 500.0,
             "revenue_unit": "BRL MM", "investor": "ABC Capital"},
            {"name": "Concorrente Dois", "stores": None, "revenue": None,
             "revenue_unit": None, "investor": None},
        ],
    }])

    dossier = _make_test_dossier(trade_name="Laces")
    enricher._enrich_competitors(dossier, "Laces", llm, verbose=False)

    assert len(dossier.market.competitors) == 2
    c1 = dossier.market.competitors[0]
    assert c1.name == "Concorrente Um"
    assert c1.stores == 100
    assert c1.revenue == pytest.approx(500.0)
    assert c1.investor == "ABC Capital"


def test_enrich_competitors_excludes_self(monkeypatch):
    """The target company itself shouldn't end up in its own competitor list."""
    from src.enrichment import enricher

    def fake_search(*a, **k):
        return {"text": "...", "url": "u", "source": "s"}
    monkeypatch.setattr(enricher, "search_competitors", fake_search)

    llm = _StubLLM(responses=[{
        "competitors": [
            {"name": "Real Competitor", "stores": 50, "revenue": 100.0,
             "revenue_unit": "BRL MM"},
            {"name": "Laces Beauty Brand",  # contains target name → drop
             "stores": 10, "revenue": 50.0, "revenue_unit": "BRL MM"},
        ],
    }])

    dossier = _make_test_dossier(trade_name="Laces")
    enricher._enrich_competitors(dossier, "Laces", llm, verbose=False)

    names = [c.name for c in dossier.market.competitors]
    assert "Real Competitor" in names
    assert not any("Laces" in n for n in names)


def test_enrich_competitors_skips_when_already_populated(monkeypatch):
    from src.enrichment import enricher
    from src.models.market import Competitor
    from src.models.evidence import Evidence

    called = {"yes": False}
    def fake_search(*a, **k):
        called["yes"] = True
        return None
    monkeypatch.setattr(enricher, "search_competitors", fake_search)

    dossier = _make_test_dossier()
    dossier.market.competitors = [
        Competitor(name="Existing", stores=10,
                   evidence=Evidence(source_file="cim.pdf")),
    ]
    enricher._enrich_competitors(dossier, "Laces", _StubLLM(), verbose=False)

    assert called["yes"] is False
    assert len(dossier.market.competitors) == 1


# ── _enrich_multiples ───────────────────────────────────────────────────
def test_enrich_multiples_populates_global_multiples_median(monkeypatch):
    from src.enrichment import enricher

    def fake_search(*a, **k):
        return {"text": "EV/EBITDA do setor de beleza: 11x; EV/Revenue 2x",
                "url": "u", "source": "s"}
    monkeypatch.setattr(enricher, "search_sector_multiples", fake_search)

    llm = _StubLLM(responses=[{
        "ev_ebitda_median": 11.0,
        "ev_revenue_median": 2.0,
        "source_note": "Damodaran Beauty/Personal Care 2024",
    }])

    dossier = _make_test_dossier()
    enricher._enrich_multiples(dossier, llm, verbose=False)

    assert dossier.market.global_multiples_median.is_filled
    val = dossier.market.global_multiples_median.value
    assert val["ev_ebitda_median"] == pytest.approx(11.0)
    assert val["ev_revenue_median"] == pytest.approx(2.0)
    assert "Damodaran" in val["source_note"]


def test_enrich_multiples_skips_when_no_values_found(monkeypatch):
    """LLM returning all-null values means we shouldn't fill the field."""
    from src.enrichment import enricher

    def fake_search(*a, **k):
        return {"text": "noise", "url": "u", "source": "s"}
    monkeypatch.setattr(enricher, "search_sector_multiples", fake_search)

    llm = _StubLLM(responses=[{
        "ev_ebitda_median": None, "ev_revenue_median": None,
        "source_note": "",
    }])

    dossier = _make_test_dossier()
    enricher._enrich_multiples(dossier, llm, verbose=False)

    assert not dossier.market.global_multiples_median.is_filled


def test_enrich_multiples_handles_partial_extraction(monkeypatch):
    """Common case: only one of the two multiples is identified.
    The single value should still get stored."""
    from src.enrichment import enricher

    def fake_search(*a, **k):
        return {"text": "...", "url": "u", "source": "s"}
    monkeypatch.setattr(enricher, "search_sector_multiples", fake_search)

    llm = _StubLLM(responses=[{
        "ev_ebitda_median": 10.5,
        "ev_revenue_median": None,
        "source_note": "",
    }])

    dossier = _make_test_dossier()
    enricher._enrich_multiples(dossier, llm, verbose=False)

    assert dossier.market.global_multiples_median.is_filled
    val = dossier.market.global_multiples_median.value
    assert val["ev_ebitda_median"] == pytest.approx(10.5)
    assert val["ev_revenue_median"] is None


# ── End-to-end enrich_dossier with stubs ────────────────────────────────
def test_full_enrichment_runs_without_crash_when_everything_returns_nothing(monkeypatch):
    """Defensive: with all sources returning None, the pipeline should
    just complete and return the dossier without modifications, not crash.
    """
    from src.enrichment import enricher

    none_search = lambda *a, **k: None
    monkeypatch.setattr(enricher, "scrape_reclame_aqui", none_search)
    monkeypatch.setattr(enricher, "search_jusbrasil", none_search)
    monkeypatch.setattr(enricher, "search_company_info", none_search)
    monkeypatch.setattr(enricher, "search_google_reviews", none_search)
    monkeypatch.setattr(enricher, "search_market_size", none_search)
    monkeypatch.setattr(enricher, "search_competitors", none_search)
    monkeypatch.setattr(enricher, "search_sector_multiples", none_search)
    # And bypass the OllamaClient construction entirely
    class _NoLLM:
        def is_available(self): return False
    monkeypatch.setattr(enricher, "OllamaClient", lambda *a, **k: _NoLLM())

    dossier = _make_test_dossier()
    result = enricher.enrich_dossier(dossier, use_llm=True, verbose=False)

    assert result is dossier
    assert dossier.market.market_sizes == []
    assert dossier.market.competitors == []
    assert not dossier.market.global_multiples_median.is_filled
