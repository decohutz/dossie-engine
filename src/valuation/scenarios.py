"""
Scenario engine for valuation.

Generates 3 scenarios per the Dossiê.docx spec:
- Pessimista: crescimento 30-50% abaixo do caso base
- Base: plano entregue pela empresa (CIM projections)
- Otimista: crescimento 30% acima do caso base

Each scenario includes "what needs to be true" analysis.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from copy import deepcopy

from .model import (
    FinancialModel, ModelAssumptions, ConsolidatedModel,
    build_entity_model, ProjectionYear,
)
from ..models.dossier import Dossier


@dataclass
class WhatNeedsToBeTrueItem:
    """One condition for a scenario to materialize."""
    category: str = ""   # "Receita", "Margem", "Operacional", "Mercado"
    condition: str = ""  # Human-readable description
    metric: str = ""     # e.g., "revenue_growth_rate"
    value: float = 0     # The assumption value


@dataclass
class Scenario:
    """A complete valuation scenario."""
    name: str = ""        # "Base", "Pessimista", "Otimista"
    label: str = ""       # Short label for tables
    description: str = ""
    models: list[FinancialModel] = field(default_factory=list)
    consolidated: list[ProjectionYear] = field(default_factory=list)
    what_needs_to_be_true: list[WhatNeedsToBeTrueItem] = field(default_factory=list)

    # Key metrics for comparison
    terminal_revenue: float = 0
    terminal_ebitda: float = 0
    terminal_ebitda_margin: float = 0
    terminal_fcf: float = 0
    revenue_cagr: float = 0

    def compute_metrics(self):
        """Compute summary metrics from consolidated data."""
        if not self.consolidated:
            return
        last = self.consolidated[-1]
        first_hist = None
        for y in self.consolidated:
            if not y.is_projected and y.net_revenue > 0:
                first_hist = y
                break

        self.terminal_revenue = last.net_revenue
        self.terminal_ebitda = last.ebitda
        self.terminal_ebitda_margin = last.ebitda_margin
        self.terminal_fcf = last.free_cash_flow

        if first_hist and last.net_revenue > 0 and first_hist.net_revenue > 0:
            n = len(self.consolidated) - 1
            if n > 0:
                self.revenue_cagr = (last.net_revenue / first_hist.net_revenue) ** (1 / n) - 1

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "description": self.description,
            "terminal_revenue": self.terminal_revenue,
            "terminal_ebitda": self.terminal_ebitda,
            "terminal_ebitda_margin": self.terminal_ebitda_margin,
            "terminal_fcf": self.terminal_fcf,
            "revenue_cagr": self.revenue_cagr,
            "what_needs_to_be_true": [asdict(w) for w in self.what_needs_to_be_true],
            "models": [m.to_dict() for m in self.models],
            "consolidated": [y.to_dict() for y in self.consolidated],
        }


@dataclass
class ScenarioEngine:
    """Generates and manages valuation scenarios."""
    base: Scenario = field(default_factory=Scenario)
    pessimistic: Scenario = field(default_factory=Scenario)
    optimistic: Scenario = field(default_factory=Scenario)

    def to_dict(self) -> dict:
        return {
            "base": self.base.to_dict(),
            "pessimistic": self.pessimistic.to_dict(),
            "optimistic": self.optimistic.to_dict(),
            "comparison": self.comparison_table(),
        }

    def comparison_table(self) -> list[dict]:
        """Generate a comparison table across scenarios."""
        rows = []
        for scenario in [self.pessimistic, self.base, self.optimistic]:
            rows.append({
                "scenario": scenario.name,
                "revenue": scenario.terminal_revenue,
                "ebitda": scenario.terminal_ebitda,
                "ebitda_margin": scenario.terminal_ebitda_margin,
                "fcf": scenario.terminal_fcf,
                "revenue_cagr": scenario.revenue_cagr,
            })
        return rows


def _adjust_growth(base_rate: float, factor: float) -> float:
    """Adjust a growth rate by a factor.

    factor < 1 = pessimistic (e.g., 0.6 = 40% reduction)
    factor > 1 = optimistic (e.g., 1.3 = 30% increase)
    """
    return base_rate * factor


def _consolidated_metrics(consolidated: list[ProjectionYear]) -> dict:
    """Compute projected CAGR and terminal margins from a consolidated series.

    Uses the TRANSITION from last historical to last projected year for the
    projected CAGR — this is the horizon that actually drives valuation.
    For margins, uses the terminal projected year.
    """
    hist = [y for y in consolidated if not y.is_projected and y.net_revenue > 0]
    proj = [y for y in consolidated if y.is_projected and y.net_revenue > 0]

    metrics = {
        "proj_revenue_cagr": None,     # CAGR from last hist to last proj
        "terminal_ebitda_margin": None,
        "terminal_cogs_pct": None,
        "terminal_sga_pct": None,
        "terminal_revenue": None,
        "terminal_ebitda": None,
        "last_hist_revenue": None,
        "last_proj_year": None,
    }

    if proj:
        terminal = proj[-1]
        metrics["terminal_revenue"] = terminal.net_revenue
        metrics["terminal_ebitda"] = terminal.ebitda
        metrics["last_proj_year"] = terminal.year
        if terminal.net_revenue > 0:
            metrics["terminal_ebitda_margin"] = terminal.ebitda / terminal.net_revenue
            # COGS and SG&A come through negative in ProjectionYear; use abs
            metrics["terminal_cogs_pct"] = abs(terminal.cogs) / terminal.net_revenue
            metrics["terminal_sga_pct"] = abs(terminal.sga) / terminal.net_revenue

    if hist and proj and hist[-1].net_revenue > 0:
        metrics["last_hist_revenue"] = hist[-1].net_revenue
        n_years = len(proj)  # steps from last hist to last proj
        start = hist[-1].net_revenue
        end = proj[-1].net_revenue
        if start > 0 and end > 0 and n_years > 0:
            metrics["proj_revenue_cagr"] = (end / start) ** (1 / n_years) - 1
    elif len(proj) >= 2 and proj[0].net_revenue > 0 and proj[-1].net_revenue > 0:
        # Fallback: CAGR within the projected span when no history is available
        n_years = len(proj) - 1
        if n_years > 0:
            metrics["proj_revenue_cagr"] = (
                proj[-1].net_revenue / proj[0].net_revenue
            ) ** (1 / n_years) - 1

    return metrics


def _generate_what_needs_to_be_true(
    scenario_name: str,
    scenario_consolidated: list[ProjectionYear],
    base_consolidated: list[ProjectionYear] | None = None,
) -> list[WhatNeedsToBeTrueItem]:
    """Generate 'what needs to be true' analysis grounded in the actual
    projected consolidated numbers of this scenario.

    Args:
        scenario_name: "Base", "Pessimista", or "Otimista"
        scenario_consolidated: this scenario's consolidated projection
        base_consolidated: the base-case consolidated (for delta comparisons
            on Pessimista/Otimista). Optional for Base itself.
    """
    items: list[WhatNeedsToBeTrueItem] = []
    m = _consolidated_metrics(scenario_consolidated)

    # Guard: if metrics couldn't be computed, return a minimal placeholder set
    if m["proj_revenue_cagr"] is None or m["terminal_ebitda_margin"] is None:
        items.append(WhatNeedsToBeTrueItem(
            category="Execução",
            condition="Métricas projetadas insuficientes para análise quantitativa",
            metric="data_quality",
            value=0,
        ))
        return items

    cagr = m["proj_revenue_cagr"]
    margin = m["terminal_ebitda_margin"]
    cogs_pct = m["terminal_cogs_pct"]
    sga_pct = m["terminal_sga_pct"]
    terminal_year = m["last_proj_year"] or "terminal"

    if scenario_name == "Base":
        items.append(WhatNeedsToBeTrueItem(
            category="Receita",
            condition=f"Receita consolidada cresce a CAGR de {cagr*100:.1f}% a.a. "
                      f"até {terminal_year} (conforme plano da empresa)",
            metric="proj_revenue_cagr",
            value=cagr,
        ))
        items.append(WhatNeedsToBeTrueItem(
            category="Margem",
            condition=f"Margem EBITDA consolidada atinge {margin*100:.1f}% em {terminal_year} "
                      f"(COGS {cogs_pct*100:.1f}%, SG&A {sga_pct*100:.1f}% da receita)",
            metric="terminal_ebitda_margin",
            value=margin,
        ))
        items.append(WhatNeedsToBeTrueItem(
            category="Operacional",
            condition="Expansão e verticalização da operação executam conforme o plano "
                      "apresentado na CIM",
            metric="execution",
            value=1.0,
        ))
        items.append(WhatNeedsToBeTrueItem(
            category="Mercado",
            condition="Mercado endereçável mantém as dinâmicas de crescimento projetadas "
                      "pelo advisor",
            metric="market",
            value=1.0,
        ))

    elif scenario_name == "Pessimista":
        # Delta vs base for narrative precision
        base_m = _consolidated_metrics(base_consolidated) if base_consolidated else {}
        base_cagr = base_m.get("proj_revenue_cagr")
        base_margin = base_m.get("terminal_ebitda_margin")

        if base_cagr is not None and base_cagr > 0:
            cagr_delta_pp = (cagr - base_cagr) * 100
            receita_cond = (
                f"Receita cresce a CAGR de {cagr*100:.1f}% a.a. "
                f"({cagr_delta_pp:+.1f}pp vs. caso Base)"
            )
        else:
            receita_cond = f"Receita cresce a CAGR de {cagr*100:.1f}% a.a."
        items.append(WhatNeedsToBeTrueItem(
            category="Receita",
            condition=receita_cond,
            metric="proj_revenue_cagr",
            value=cagr,
        ))

        if base_margin is not None:
            margin_delta_pp = (margin - base_margin) * 100
            margem_cond = (
                f"Margem EBITDA terminal comprime para {margin*100:.1f}% "
                f"({margin_delta_pp:+.1f}pp vs. Base) por pressão em COGS/SG&A"
            )
        else:
            margem_cond = f"Margem EBITDA terminal em {margin*100:.1f}% reflete pressão de custos"
        items.append(WhatNeedsToBeTrueItem(
            category="Margem",
            condition=margem_cond,
            metric="terminal_ebitda_margin",
            value=margin,
        ))

        items.append(WhatNeedsToBeTrueItem(
            category="Operacional",
            condition="Plano de expansão sofre atrasos; churn da rede persiste ou acelera",
            metric="execution",
            value=0.6,
        ))
        items.append(WhatNeedsToBeTrueItem(
            category="Mercado",
            condition="Intensificação competitiva ou crescimento de mercado abaixo do projetado",
            metric="market",
            value=0.7,
        ))

    elif scenario_name == "Otimista":
        base_m = _consolidated_metrics(base_consolidated) if base_consolidated else {}
        base_cagr = base_m.get("proj_revenue_cagr")
        base_margin = base_m.get("terminal_ebitda_margin")

        if base_cagr is not None and base_cagr > 0:
            cagr_delta_pp = (cagr - base_cagr) * 100
            receita_cond = (
                f"Receita cresce a CAGR de {cagr*100:.1f}% a.a. "
                f"({cagr_delta_pp:+.1f}pp vs. caso Base)"
            )
        else:
            receita_cond = f"Receita cresce a CAGR de {cagr*100:.1f}% a.a."
        items.append(WhatNeedsToBeTrueItem(
            category="Receita",
            condition=receita_cond,
            metric="proj_revenue_cagr",
            value=cagr,
        ))

        if base_margin is not None:
            margin_delta_pp = (margin - base_margin) * 100
            margem_cond = (
                f"Margem EBITDA terminal expande para {margin*100:.1f}% "
                f"({margin_delta_pp:+.1f}pp vs. Base) por ganhos de escala"
            )
        else:
            margem_cond = f"Margem EBITDA terminal em {margin*100:.1f}% reflete ganhos de escala"
        items.append(WhatNeedsToBeTrueItem(
            category="Margem",
            condition=margem_cond,
            metric="terminal_ebitda_margin",
            value=margin,
        ))

        items.append(WhatNeedsToBeTrueItem(
            category="Operacional",
            condition="Expansão acelerada de lojas próprias e marcas próprias ganham share de carteira",
            metric="execution",
            value=1.3,
        ))
        items.append(WhatNeedsToBeTrueItem(
            category="Mercado",
            condition="Mercado cresce acima do projetado; consolidação favorece os líderes",
            metric="market",
            value=1.3,
        ))

    return items


def build_scenarios(
    dossier: Dossier,
    pessimistic_factor: float = 0.6,   # 40% reduction in growth
    optimistic_factor: float = 1.3,    # 30% increase in growth
    verbose: bool = False,
) -> ScenarioEngine:
    """Build 3 scenarios from dossier financial data.

    Args:
        dossier: The dossier with extracted DRE data
        pessimistic_factor: Multiply growth by this (0.5-0.7 typical)
        optimistic_factor: Multiply growth by this (1.2-1.4 typical)
        verbose: Print progress

    Returns:
        ScenarioEngine with base, pessimistic, and optimistic scenarios
    """
    if verbose:
        print("  [Valuation] Building scenarios...")

    engine = ScenarioEngine()
    fin = dossier.financials

    # Discover entities dynamically from the financial chapter. Each entity
    # contributes a DRE (when available) to the consolidated projection.
    entities = [(e.name, e.dre) for e in fin.entities]

    # ── BASE SCENARIO (CIM projections as-is) ────────────────
    base_models = []
    base_assumptions_by_entity = {}

    for name, stmt in entities:
        model = build_entity_model(stmt, name, verbose=verbose)
        base_models.append(model)
        base_assumptions_by_entity[name] = deepcopy(model.assumptions)

    engine.base = Scenario(
        name="Base",
        label="Caso Base",
        description="Plano entregue pela empresa conforme CIM",
        models=base_models,
    )

    # Consolidate base
    cons = ConsolidatedModel(entities=base_models)
    cons.build_consolidated()
    engine.base.consolidated = cons.consolidated
    engine.base.what_needs_to_be_true = _generate_what_needs_to_be_true(
        "Base",
        scenario_consolidated=engine.base.consolidated,
        base_consolidated=engine.base.consolidated,
    )
    engine.base.compute_metrics()

    if verbose:
        print(f"    ✅ Base: Revenue={engine.base.terminal_revenue:,.0f}, "
              f"EBITDA={engine.base.terminal_ebitda:,.0f}")

    # ── PESSIMISTIC SCENARIO ─────────────────────────────────
    pess_models = []
    for name, stmt in entities:
        base_a = base_assumptions_by_entity.get(name, ModelAssumptions())
        # Scale CIM projections down: revenue × pessimistic_factor, worse margins
        adj_factors = {
            "revenue_factor": pessimistic_factor,     # e.g., 0.6 = 40% lower revenue
            "cogs_factor": 1.10,                      # 10% worse COGS ratio
            "sga_factor": 1.05,                       # 5% worse SG&A ratio
        }
        model = build_entity_model(
            stmt, name, verbose=False, adjustment_factors=adj_factors,
        )
        model.assumptions.label = "Pessimista"
        pess_models.append(model)

    engine.pessimistic = Scenario(
        name="Pessimista",
        label="Caso Pessimista",
        description=f"Receita {(1-pessimistic_factor)*100:.0f}% abaixo do plano, "
                    f"pressão em margens",
        models=pess_models,
    )
    cons_p = ConsolidatedModel(entities=pess_models)
    cons_p.build_consolidated()
    engine.pessimistic.consolidated = cons_p.consolidated
    engine.pessimistic.what_needs_to_be_true = _generate_what_needs_to_be_true(
        "Pessimista",
        scenario_consolidated=engine.pessimistic.consolidated,
        base_consolidated=engine.base.consolidated,
    )
    engine.pessimistic.compute_metrics()

    if verbose:
        print(f"    ✅ Pessimista: Revenue={engine.pessimistic.terminal_revenue:,.0f}, "
              f"EBITDA={engine.pessimistic.terminal_ebitda:,.0f}")

    # ── OPTIMISTIC SCENARIO ──────────────────────────────────
    opt_models = []
    for name, stmt in entities:
        base_a = base_assumptions_by_entity.get(name, ModelAssumptions())
        # Scale CIM projections up: revenue × optimistic_factor, better margins
        adj_factors = {
            "revenue_factor": optimistic_factor,      # e.g., 1.3 = 30% higher revenue
            "cogs_factor": 0.95,                      # 5% better COGS ratio
            "sga_factor": 0.97,                       # 3% better SG&A ratio
        }
        model = build_entity_model(
            stmt, name, verbose=False, adjustment_factors=adj_factors,
        )
        model.assumptions.label = "Otimista"
        opt_models.append(model)

    engine.optimistic = Scenario(
        name="Otimista",
        label="Caso Otimista",
        description=f"Receita {(optimistic_factor-1)*100:.0f}% acima do plano, "
                    f"ganhos de escala em margens",
        models=opt_models,
    )
    cons_o = ConsolidatedModel(entities=opt_models)
    cons_o.build_consolidated()
    engine.optimistic.consolidated = cons_o.consolidated
    engine.optimistic.what_needs_to_be_true = _generate_what_needs_to_be_true(
        "Otimista",
        scenario_consolidated=engine.optimistic.consolidated,
        base_consolidated=engine.base.consolidated,
    )
    engine.optimistic.compute_metrics()

    if verbose:
        print(f"    ✅ Otimista: Revenue={engine.optimistic.terminal_revenue:,.0f}, "
              f"EBITDA={engine.optimistic.terminal_ebitda:,.0f}")

        # Print comparison
        print(f"\n    {'Cenário':<15} {'Receita':>12} {'EBITDA':>12} {'Mg EBITDA':>10} {'FCF':>12}")
        print(f"    {'-'*60}")
        for sc in [engine.pessimistic, engine.base, engine.optimistic]:
            mg = f"{sc.terminal_ebitda_margin*100:.1f}%" if sc.terminal_ebitda_margin else "—"
            print(f"    {sc.name:<15} {sc.terminal_revenue:>12,.0f} {sc.terminal_ebitda:>12,.0f} "
                  f"{mg:>10} {sc.terminal_fcf:>12,.0f}")

    return engine


# ═══════════════════════════════════════════════════════════════
# FULL VALUATION ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════
def run_full_valuation(
    dossier,
    wacc_inputs=None,
    ev_ebitda_multiple: float | None = None,
    ev_revenue_multiple: float | None = None,
    stake_pct: float | None = None,
    entry_equity_value: float | None = None,
    net_debt: float = 0,
    verbose: bool = False,
) -> dict:
    """Run complete valuation: scenarios + DCF + multiples + IRR.

    Args:
        dossier: The dossier with financial and market data
        wacc_inputs: WACC parameters (auto-derived if None)
        ev_ebitda_multiple: EV/EBITDA multiple (uses extracted if None)
        ev_revenue_multiple: EV/Revenue multiple (uses extracted if None)
        stake_pct: Target investor stake. If None, parsed from the
            dossier's transaction.target_stake_range; if that also fails,
            defaults to 0.30. Pass a float (e.g., 0.35) to override.
        entry_equity_value: Fixed total equity value (100% basis) used as
            the investor's entry across ALL scenarios. If None, defaults
            to the Base-case DCF perpetuity equity value — so the Base
            represents the "fair price today" and Pessimista/Otimista
            test downside/upside at the same ticket. Pass a float to
            override (e.g., a negotiated price).
        net_debt: Net debt for equity bridge
        verbose: Print progress
    """
    from .dcf import run_dcf, WACCInputs
    from .multiples import run_multiples, run_irr, build_valuation_summary

    # Extract multiples from dossier if not provided
    if ev_ebitda_multiple is None or ev_revenue_multiple is None:
        mult_data = dossier.market.global_multiples_median.value
        if isinstance(mult_data, dict):
            if ev_ebitda_multiple is None:
                ev_ebitda_multiple = mult_data.get("ev_ebitda_median", 11.0)
            if ev_revenue_multiple is None:
                ev_revenue_multiple = mult_data.get("ev_revenue_median", 1.8)
        else:
            ev_ebitda_multiple = ev_ebitda_multiple or 11.0
            ev_revenue_multiple = ev_revenue_multiple or 1.8

    if not wacc_inputs:
        wacc_inputs = WACCInputs()

    # ── Resolve stake_pct ─────────────────────────────────────
    # Priority: explicit arg > parsed from dossier > fallback default
    resolved_stake = None
    if stake_pct is not None:
        # Caller provided an override; use it directly (no parsing).
        resolved_stake = stake_pct
    else:
        stake_range = dossier.transaction.target_stake_range.value
        if stake_range and isinstance(stake_range, str):
            import re
            s = str(stake_range)

            # The CIM typically says ">60% acionistas, <40% investidor"
            # The investor stake is the one with "<" or "menor" or "minoritário"

            # Pattern 1: explicit "<X%" — this is the investor's max stake
            lt_match = re.search(r'<\s*(\d+)\s*%', s)
            if lt_match:
                parsed = int(lt_match.group(1)) / 100
            else:
                # Pattern 2: "até X%" or "up to X%"
                ate_match = re.search(r'(?:até|up\s+to)\s+(\d+)\s*%', s, re.IGNORECASE)
                if ate_match:
                    parsed = int(ate_match.group(1)) / 100
                else:
                    # Pattern 3: take the smallest number (likely investor stake)
                    nums = re.findall(r'(\d+)\s*%', s)
                    if nums:
                        parsed = min(int(n) for n in nums) / 100
                    else:
                        parsed = None

            if parsed is not None:
                # Sanity check: investor stake should be 5%-50%
                if 0.05 <= parsed <= 0.50:
                    resolved_stake = parsed
                elif parsed > 0.50:
                    # Likely picked up the majority holder's stake; use complement
                    complement = 1.0 - parsed
                    if 0.05 <= complement <= 0.50:
                        resolved_stake = complement

    # Final fallback
    stake_pct = resolved_stake if resolved_stake is not None else 0.30

    # Step 1: Build scenarios
    engine = build_scenarios(dossier, verbose=verbose)

    # ── Resolve entry_equity_value ────────────────────────────
    # If the caller didn't pass an explicit entry price, derive it from
    # the BASE scenario's DCF. This gives us a single, consistent entry
    # across all three scenarios — so the IRR actually measures downside
    # vs upside (rather than "did you buy at fair value in each universe").
    entry_source = "override" if entry_equity_value is not None else "base_dcf"
    if entry_equity_value is None:
        base_proj = [y for y in engine.base.consolidated if y.is_projected]
        if not base_proj:
            base_proj = engine.base.consolidated
        base_dcf = run_dcf(
            base_proj, wacc_inputs, terminal_method="perpetuity",
            terminal_growth_rate=0.03, net_debt=net_debt, verbose=False,
        )
        entry_equity_value = base_dcf.bridge.equity_value
        if entry_equity_value <= 0:
            # Fallback: blended multiples-based equity if DCF degenerates
            base_mult = run_multiples(
                base_proj, ev_ebitda_multiple, ev_revenue_multiple,
                net_debt=net_debt, verbose=False,
            )
            entry_equity_value = base_mult.equity_blended

    # Step 2: Run DCF + Multiples + IRR for each scenario
    if verbose:
        print(f"\n  [Valuation] Running DCF + Múltiplos + IRR...")
        print(f"    WACC: {wacc_inputs.wacc*100:.1f}%  |  "
              f"EV/EBITDA: {ev_ebitda_multiple}x  |  "
              f"EV/Revenue: {ev_revenue_multiple}x  |  "
              f"Stake: {stake_pct*100:.0f}%")
        print(f"    Entry (100% equity, fixed across scenarios): "
              f"{entry_equity_value:,.0f} [{entry_source}]")

    summaries = []

    for scenario in [engine.pessimistic, engine.base, engine.optimistic]:
        if verbose:
            print(f"\n    --- {scenario.name} ---")

        proj = [y for y in scenario.consolidated if y.is_projected]
        if not proj:
            proj = scenario.consolidated

        # DCF - Perpetuity
        dcf_perp = run_dcf(
            proj, wacc_inputs, terminal_method="perpetuity",
            terminal_growth_rate=0.03, net_debt=net_debt, verbose=verbose,
        )

        # DCF - Exit Multiple
        dcf_exit = run_dcf(
            proj, wacc_inputs, terminal_method="exit_multiple",
            exit_multiple=ev_ebitda_multiple, net_debt=net_debt, verbose=False,
        )

        # Multiples (terminal year — for EV range)
        mult = run_multiples(
            proj, ev_ebitda_multiple, ev_revenue_multiple,
            net_debt=net_debt, verbose=verbose,
        )

        # IRR: FIXED entry (Base-case equity or user override) — same ticket
        # for all three scenarios. This lets the IRR actually compare
        # downside (Pessimista) vs upside (Otimista) at a single negotiated
        # price, instead of "buying at fair value in each parallel universe"
        # which made the IRR collapse to WACC and decrease with growth.
        if verbose:
            print(f"      Entry (fixed, 100% equity): {entry_equity_value:,.0f}")
            print(f"      Entry × stake ({stake_pct*100:.0f}%): "
                  f"{entry_equity_value * stake_pct:,.0f}")

        irr = run_irr(
            proj, entry_equity_value=entry_equity_value,
            stake_pct=stake_pct, exit_ev_ebitda=ev_ebitda_multiple,
            net_debt_at_exit=net_debt, verbose=verbose,
        )

        summary = build_valuation_summary(
            scenario.name,
            dcf_perp_ev=dcf_perp.enterprise_value,
            dcf_exit_ev=dcf_exit.enterprise_value,
            mult_ebitda_ev=mult.ev_by_ebitda,
            mult_rev_ev=mult.ev_by_revenue,
            net_debt=net_debt,
            irr=irr.irr,
            moic=irr.moic,
        )
        summaries.append(summary)

    # Print summary table
    if verbose:
        print(f"\n  {'='*80}")
        print(f"  {'Cenário':<12} {'DCF Perp':>10} {'DCF Exit':>10} "
              f"{'EV/EBITDA':>10} {'EV/Rev':>10} {'Eq Low':>10} {'Eq High':>10} "
              f"{'IRR':>7} {'MOIC':>6}")
        print(f"  {'-'*80}")
        for s in summaries:
            print(f"  {s.scenario_name:<12} {s.dcf_perpetuity:>10,.0f} {s.dcf_exit_multiple:>10,.0f} "
                  f"{s.multiples_ev_ebitda:>10,.0f} {s.multiples_ev_revenue:>10,.0f} "
                  f"{s.equity_range_low:>10,.0f} {s.equity_range_high:>10,.0f} "
                  f"{s.irr*100:>6.1f}% {s.moic:>5.2f}x")

    return {
        "scenarios": engine.to_dict(),
        "summaries": [s.to_dict() for s in summaries],
        "inputs": {
            "wacc": wacc_inputs.to_dict(),
            "ev_ebitda_multiple": ev_ebitda_multiple,
            "ev_revenue_multiple": ev_revenue_multiple,
            "stake_pct": stake_pct,
            "entry_equity_value": entry_equity_value,
            "entry_source": entry_source,
            "net_debt": net_debt,
        },
    }