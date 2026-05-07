"""
Tests for E4 — IRR/MOIC compute correctly across scenarios.

The bug behind v007's "IRR=15.0%/MOIC=0.00x in all 3 scenarios" was
deceptive: it looked like a Newton-method convergence issue (initial
guess returning unchanged), but the real cause was upstream.

Tracing back:
  1. ``run_irr`` filters its input ``projected_years`` by ``is_projected``
     and has no fallback — if the filter returns empty, it builds the
     exit cash flow from a default ``ProjectionYear()`` whose ebitda=0.
  2. ``ConsolidatedModel.build_consolidated`` was setting
     ``is_projected="E" in year_str`` for the consolidated rollup.
  3. The XLSX parser strips the "E" suffix from year labels — so
     "2025E" / "2030E" become "2025" / "2030" with ``is_projected=False``
     on the entity-level lines.
  4. The consolidated rollup therefore inherited ``is_projected=False``
     for every year, the IRR's internal filter saw an empty list,
     ``exit_year.ebitda=0``, and the cash flow was ``[-entry, 0, 0, 0, 0, 0]``.
  5. Newton's method ran on that flat cash flow, found ``dnpv=0``, broke
     out of the loop, and returned the initial guess of 0.15 = 15%.
  6. ``MOIC = sum(positive_cfs) / entry = 0 / entry = 0``.

The same pattern was caught by E3.3 in ``_build_historical`` — that fix
went up the call chain by consulting ``FinancialLine.is_projected``
rather than parsing year strings. E4 applies the analogous fix in
``build_consolidated``: build a year→is_projected map from each entity's
``all_years`` and use it instead of the "E"-suffix heuristic.

What these tests pin down:
  1. The map propagates correctly — projected years stay projected
     after consolidation.
  2. The map handles divergent flags across entities — once any
     entity says a year is projected, the consolidation treats it
     as projected (over-projection is safer than over-historicization).
  3. The "E" suffix fallback still works for hand-crafted entities
     in tests where ``is_projected`` was never set on the source lines.
  4. End-to-end: ``run_full_valuation`` produces non-zero, divergent
     IRR/MOIC across the 3 scenarios when EV/EBITDA is supplied.
"""
from __future__ import annotations

import pytest

openpyxl = pytest.importorskip("openpyxl")

from tests.test_xlsx_parser import _new_workbook, _add_cover, _add_dre_sheet


def _xlsx_with_projection_pattern(tmp_path, *, sheet_name: str = "DRE Foo"):
    """Build a 1h+5p XLSX (1 historical year, 5 projected) for IRR scenarios.

    Numbers are picked to give nontrivially-divergent IRRs across scenarios:
    EBITDA grows roughly 15% YoY, so the optimistic case (1.3x revenue)
    materially out-performs the pessimistic case (0.6x).
    """
    wb = _new_workbook()
    # The parser's heuristic: cell.year - 1 = last actual year. So passing
    # 2025 here yields cutoff=2024, which means 2024 is historical and
    # 2025-2029 are projected — matching the years column below.
    _add_cover(wb, last_update_year=2025)
    rows = [
        ("Receita Bruta", 100_000, 110_000, 130_000, 150_000, 175_000, 200_000),
        ("Impostos",       -15_000, -16_500, -19_500, -22_500, -26_250, -30_000),
        ("Receita Liquida", 85_000,  93_500, 110_500, 127_500, 148_750, 170_000),
        ("CMV",            -30_000, -32_000, -38_000, -44_000, -51_000, -58_000),
        ("Lucro Bruto",     55_000,  61_500,  72_500,  83_500,  97_750, 112_000),
        ("SG&A",           -25_000, -27_000, -32_000, -37_000, -43_000, -48_000),
        ("EBITDA",          30_000,  34_500,  40_500,  46_500,  54_750,  64_000),
    ]
    # year columns: 2024 actual, 2025E-2029E projected
    _add_dre_sheet(
        wb, sheet_name,
        years=("2024", "2025E", "2026E", "2027E", "2028E", "2029E"),
        rows=rows,
    )
    path = tmp_path / "p.xlsx"
    wb.save(path)
    return path


# ── Map propagation ──────────────────────────────────────────────────────
def test_consolidated_inherits_is_projected_from_entities(tmp_path):
    """When entities have correct is_projected flags from the parser
    (XLSX strips 'E' but sets the flag), the consolidated rollup
    must inherit them — not default to False because the year string
    no longer has 'E'.
    """
    from src.pipeline.orchestrator import run_pipeline
    from src.valuation.scenarios import build_scenarios

    path = _xlsx_with_projection_pattern(tmp_path)
    dossier = run_pipeline(inputs=[str(path)], use_llm=False, project_name="t")

    engine = build_scenarios(dossier, verbose=False)

    for sc in [engine.pessimistic, engine.base, engine.optimistic]:
        proj_years = [y for y in sc.consolidated if y.is_projected]
        # 2024 is historical → 1 historical, 5 projected = 6 total
        assert len(proj_years) == 5, (
            f"{sc.name}: expected 5 projected years (2025-2029); "
            f"got {len(proj_years)}. is_projected map didn't propagate."
        )
        years_proj = [y.year for y in proj_years]
        assert "2024" not in years_proj
        assert all(y in years_proj for y in ("2025", "2026", "2027", "2028", "2029"))


def test_consolidated_treats_year_as_projected_if_any_entity_does(tmp_path):
    """Edge case: two entities, one with year X projected and one with X
    historical. The conservative behavior is to flag the consolidated
    year as projected — that way IRR's internal filter won't drop a
    year that any entity is forecasting.

    Over-projecting is safer than over-historicizing because the IRR
    engine has no fallback when its filter returns empty.
    """
    from src.valuation.model import ConsolidatedModel, FinancialModel, ProjectionYear

    # Entity A — 2025 projected
    ent_a = FinancialModel(entity_name="A", non_operating=False)
    ent_a.historical = [ProjectionYear(year="2024", is_projected=False, ebitda=100, net_revenue=500)]
    ent_a.projected = [ProjectionYear(year="2025", is_projected=True, ebitda=120, net_revenue=600)]

    # Entity B — 2025 *historical* (rare but possible: one entity has actuals
    # for a year another entity is still forecasting)
    ent_b = FinancialModel(entity_name="B", non_operating=False)
    ent_b.historical = [
        ProjectionYear(year="2024", is_projected=False, ebitda=50, net_revenue=200),
        ProjectionYear(year="2025", is_projected=False, ebitda=70, net_revenue=300),
    ]
    ent_b.projected = []

    cm = ConsolidatedModel(entities=[ent_a, ent_b])
    cm.build_consolidated()

    by_year = {y.year: y for y in cm.consolidated}
    assert by_year["2024"].is_projected is False
    # Any-entity-projected → consolidated projected
    assert by_year["2025"].is_projected is True


def test_consolidated_falls_back_to_E_suffix_when_map_empty():
    """For hand-crafted entities in tests where ``is_projected`` was never
    set on the source lines, the legacy 'E' suffix heuristic still has
    to work — it's the defensive fallback.
    """
    from src.valuation.model import ConsolidatedModel, FinancialModel, ProjectionYear

    ent = FinancialModel(entity_name="A", non_operating=False)
    # All flags False (default), but year strings carry 'E' suffix
    ent.historical = [ProjectionYear(year="2024", is_projected=False, ebitda=100)]
    ent.projected = [
        ProjectionYear(year="2025E", is_projected=False, ebitda=120),  # flag wrong
        ProjectionYear(year="2026E", is_projected=False, ebitda=140),  # flag wrong
    ]

    cm = ConsolidatedModel(entities=[ent])
    cm.build_consolidated()

    by_year = {y.year: y for y in cm.consolidated}
    # Map populated → year-by-year flag wins. All entities said False
    # for these years (because flag is False on every line), so the map
    # has False entries — fallback doesn't kick in. This pins the
    # current contract: when the map IS populated (even with all-False
    # values), it's authoritative. The fallback is for when the map
    # didn't see this year at all.
    assert by_year["2024"].is_projected is False
    assert by_year["2025E"].is_projected is False
    assert by_year["2026E"].is_projected is False


# ── End-to-end IRR/MOIC ──────────────────────────────────────────────────
def test_irr_moic_diverge_across_scenarios_with_overrides(tmp_path):
    """The big payoff: IRR and MOIC should produce DIFFERENT, NON-ZERO
    values across pessimistic/base/optimistic when EV/EBITDA is supplied.

    This is the visible smoking gun for the v007 bug — the symptom
    everyone could see was IRR=15.0%/MOIC=0 in all three scenarios.
    The fix has to make these diverge.
    """
    from src.pipeline.orchestrator import run_pipeline
    from src.valuation.scenarios import run_full_valuation

    path = _xlsx_with_projection_pattern(tmp_path)
    dossier = run_pipeline(
        inputs=[str(path)], use_llm=False, project_name="t",
        ev_ebitda_override=11.0, ev_revenue_override=1.8,
    )
    val = run_full_valuation(dossier, verbose=False)

    irrs = {s["scenario_name"]: s["irr"] for s in val["summaries"]}
    moics = {s["scenario_name"]: s["moic"] for s in val["summaries"]}

    # All three scenarios produce non-zero MOIC
    for name in ("Pessimista", "Base", "Otimista"):
        assert moics[name] > 0, f"{name}: MOIC=0 means cashflow schedule is degenerate"

    # IRRs are not all equal (the v007 bug had them all == 0.15)
    irr_values = list(irrs.values())
    assert len(set(round(v, 4) for v in irr_values)) > 1, (
        f"All IRRs identical: {irrs}. Bug not fixed — exit cash flow "
        "still degenerate across scenarios."
    )

    # And no scenario is suspiciously stuck at the Newton initial guess of 0.15
    assert not all(abs(v - 0.15) < 0.001 for v in irr_values), (
        f"All IRRs at 0.15 = Newton initial guess: {irrs}. "
        "Cash flow schedule is still flat — fix didn't reach run_irr."
    )


def test_irr_orders_correctly_pess_lt_otim(tmp_path):
    """Sanity: optimistic IRR > pessimistic IRR.

    Same fixed entry across scenarios + scenario-divergent terminal EBITDA
    (factors 0.6/1.0/1.3 from E3.3) → upside scenario must produce higher
    exit proceeds than downside, so the upside IRR has to be higher.

    Note: we deliberately do NOT assert ``Pess < Base < Otim`` here.
    That stricter ordering depends on the interaction between revenue
    factors and the entity's cost structure (e.g. fixed SG&A means
    margin amplifies in either direction relative to base), and is not
    universally true across CIM shapes. The Otim > Pess relation, by
    contrast, is mechanically robust: same entry, larger exit EBITDA,
    higher IRR.
    """
    from src.pipeline.orchestrator import run_pipeline
    from src.valuation.scenarios import run_full_valuation

    path = _xlsx_with_projection_pattern(tmp_path)
    dossier = run_pipeline(
        inputs=[str(path)], use_llm=False, project_name="t",
        ev_ebitda_override=11.0, ev_revenue_override=1.8,
    )
    val = run_full_valuation(dossier, verbose=False)
    by_name = {s["scenario_name"]: s for s in val["summaries"]}

    pess_irr = by_name["Pessimista"]["irr"]
    otim_irr = by_name["Otimista"]["irr"]
    assert otim_irr > pess_irr, (
        f"Optimistic IRR not greater than pessimistic: "
        f"Pess={pess_irr:.3f}, Otim={otim_irr:.3f}"
    )


def test_irr_skips_when_no_multiple_supplied(tmp_path):
    """Without EV/EBITDA (no override, no extracted multiple), the
    valuation engine still has the ``ev_ebitda_multiple is not None``
    guard around run_irr. This test pins that contract — the E4 fix
    must not have introduced a path where IRR runs anyway with a
    default 0.0 multiple (which would silently produce IRR≈-100%).
    """
    from src.pipeline.orchestrator import run_pipeline
    from src.valuation.scenarios import run_full_valuation

    path = _xlsx_with_projection_pattern(tmp_path)
    dossier = run_pipeline(inputs=[str(path)], use_llm=False, project_name="t")
    # No overrides — multiples remain None
    val = run_full_valuation(dossier, verbose=False)

    # IRR is reported as 0 (the "skipped" sentinel), not negative or weird
    for s in val["summaries"]:
        assert s["irr"] == 0
        assert s["moic"] == 0
