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


def _generate_what_needs_to_be_true(
    scenario_name: str,
    base_assumptions: ModelAssumptions,
    scenario_assumptions: ModelAssumptions,
) -> list[WhatNeedsToBeTrueItem]:
    """Generate 'what needs to be true' analysis for a scenario."""
    items = []

    if scenario_name == "Base":
        items.append(WhatNeedsToBeTrueItem(
            category="Receita",
            condition=f"Crescimento de receita de {base_assumptions.revenue_growth_rate*100:.1f}% a.a. se mantém",
            metric="revenue_growth_rate",
            value=base_assumptions.revenue_growth_rate,
        ))
        items.append(WhatNeedsToBeTrueItem(
            category="Margem",
            condition=f"Margem COGS estabiliza em {base_assumptions.cogs_pct_revenue*100:.1f}% da receita",
            metric="cogs_pct_revenue",
            value=base_assumptions.cogs_pct_revenue,
        ))
        items.append(WhatNeedsToBeTrueItem(
            category="Operacional",
            condition=f"SG&A se mantém em {base_assumptions.sga_pct_revenue*100:.1f}% da receita",
            metric="sga_pct_revenue",
            value=base_assumptions.sga_pct_revenue,
        ))
        items.append(WhatNeedsToBeTrueItem(
            category="Mercado",
            condition="Plano de expansão e verticalização executa conforme previsto",
            metric="execution",
            value=1.0,
        ))

    elif scenario_name == "Pessimista":
        growth_reduction = (1 - scenario_assumptions.revenue_growth_rate / base_assumptions.revenue_growth_rate) * 100 \
            if base_assumptions.revenue_growth_rate else 0
        items.append(WhatNeedsToBeTrueItem(
            category="Receita",
            condition=f"Crescimento de receita cai {growth_reduction:.0f}% vs. plano "
                      f"({scenario_assumptions.revenue_growth_rate*100:.1f}% a.a.)",
            metric="revenue_growth_rate",
            value=scenario_assumptions.revenue_growth_rate,
        ))
        items.append(WhatNeedsToBeTrueItem(
            category="Margem",
            condition=f"Pressão em COGS: sobe para {scenario_assumptions.cogs_pct_revenue*100:.1f}% da receita",
            metric="cogs_pct_revenue",
            value=scenario_assumptions.cogs_pct_revenue,
        ))
        items.append(WhatNeedsToBeTrueItem(
            category="Operacional",
            condition="Desaceleração na expansão de lojas, atrasos em verticalização",
            metric="execution",
            value=0.6,
        ))
        items.append(WhatNeedsToBeTrueItem(
            category="Mercado",
            condition="Competição intensifica ou mercado cresce abaixo do esperado",
            metric="market",
            value=0.7,
        ))

    elif scenario_name == "Otimista":
        items.append(WhatNeedsToBeTrueItem(
            category="Receita",
            condition=f"Crescimento acelerado: {scenario_assumptions.revenue_growth_rate*100:.1f}% a.a. "
                      f"(+30% vs. plano)",
            metric="revenue_growth_rate",
            value=scenario_assumptions.revenue_growth_rate,
        ))
        items.append(WhatNeedsToBeTrueItem(
            category="Margem",
            condition=f"Ganhos de escala: COGS cai para {scenario_assumptions.cogs_pct_revenue*100:.1f}% da receita",
            metric="cogs_pct_revenue",
            value=scenario_assumptions.cogs_pct_revenue,
        ))
        items.append(WhatNeedsToBeTrueItem(
            category="Operacional",
            condition="Expansão acelerada de lojas próprias + marcas próprias ganham share",
            metric="execution",
            value=1.3,
        ))
        items.append(WhatNeedsToBeTrueItem(
            category="Mercado",
            condition="Mercado óptico cresce acima do esperado, consolidação favorece líderes",
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

    entities = [
        ("Franqueadora", fin.dre_franqueadora),
        ("Distribuidora", fin.dre_distribuidora),
        ("Lojas Próprias", fin.dre_lojas_proprias),
    ]

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
        "Base", base_assumptions_by_entity.get("Franqueadora", ModelAssumptions()),
        base_assumptions_by_entity.get("Franqueadora", ModelAssumptions()),
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
        base_assumptions_by_entity.get("Franqueadora", ModelAssumptions()),
        pess_models[0].assumptions if pess_models else ModelAssumptions(),
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
        base_assumptions_by_entity.get("Franqueadora", ModelAssumptions()),
        opt_models[0].assumptions if opt_models else ModelAssumptions(),
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
    stake_pct: float = 0.30,
    net_debt: float = 0,
    verbose: bool = False,
) -> dict:
    """Run complete valuation: scenarios + DCF + multiples + IRR.

    Args:
        dossier: The dossier with financial and market data
        wacc_inputs: WACC parameters (auto-derived if None)
        ev_ebitda_multiple: EV/EBITDA multiple (uses extracted if None)
        ev_revenue_multiple: EV/Revenue multiple (uses extracted if None)
        stake_pct: Target investor stake
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

    # Extract stake from dossier
    stake_range = dossier.transaction.target_stake_range.value
    if stake_range and isinstance(stake_range, str):
        import re
        nums = re.findall(r'(\d+)', str(stake_range))
        if nums:
            # Use the first percentage found, capped at 60%
            parsed = min(int(nums[0]), 60) / 100
            if 0.05 <= parsed <= 0.60:
                stake_pct = parsed

    # Step 1: Build scenarios
    engine = build_scenarios(dossier, verbose=verbose)

    # Step 2: Run DCF + Multiples + IRR for each scenario
    if verbose:
        print(f"\n  [Valuation] Running DCF + Múltiplos + IRR...")
        print(f"    WACC: {wacc_inputs.wacc*100:.1f}%  |  "
              f"EV/EBITDA: {ev_ebitda_multiple}x  |  "
              f"EV/Revenue: {ev_revenue_multiple}x  |  "
              f"Stake: {stake_pct*100:.0f}%")

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

        # Multiples
        mult = run_multiples(
            proj, ev_ebitda_multiple, ev_revenue_multiple,
            net_debt=net_debt, verbose=verbose,
        )

        # IRR (use blended equity as entry price)
        entry_eq = mult.equity_blended if mult.equity_blended > 0 else dcf_perp.bridge.equity_value
        irr = run_irr(
            proj, entry_equity_value=entry_eq,
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
            "net_debt": net_debt,
        },
    }