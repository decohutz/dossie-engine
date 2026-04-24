"""
Financial model builder.

Takes DRE data from the dossier and creates a projection model with
variable assumptions. Supports per-entity and consolidated views.

Etapa 1 of the valuation process (per Dossiê.docx):
- Construction of the model with variable assumptions
- Before any valuation calculation
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional

from ..models.dossier import Dossier
from ..models.financials import FinancialStatement, FinancialLine


# ═══════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════
@dataclass
class ModelAssumptions:
    """Variable assumptions for the financial model.

    All rates are decimals (e.g., 0.10 = 10%).
    These can be overridden by the user for scenario analysis.
    """
    # Revenue
    revenue_growth_rate: float = 0.10       # Annual revenue growth
    # Margins
    cogs_pct_revenue: float = 0.15          # COGS as % of net revenue
    sga_pct_revenue: float = 0.50           # SG&A as % of net revenue
    tax_on_gross_revenue_pct: float = 0.12  # Impostos/devoluções as % of gross revenue
    # Operating
    da_pct_revenue: float = 0.01            # D&A as % of net revenue
    financial_result_pct_revenue: float = -0.03  # Resultado financeiro as % of net revenue
    ir_csll_pct_ebt: float = 0.34           # IR+CSLL as % of EBT (effective tax rate)
    # Cash flow
    capex_pct_revenue: float = 0.03         # CAPEX as % of net revenue
    nwc_change_pct_revenue: float = 0.02    # Change in NWC as % of net revenue
    # Metadata
    entity_name: str = ""
    label: str = ""  # "Base", "Pessimista", "Otimista"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ProjectionYear:
    """Single year of projected financials."""
    year: str = ""
    is_projected: bool = True
    # Income statement
    gross_revenue: float = 0
    taxes_deductions: float = 0
    net_revenue: float = 0
    cogs: float = 0
    gross_profit: float = 0
    sga: float = 0
    ebitda: float = 0
    ebitda_margin: float = 0
    da: float = 0
    ebit: float = 0
    financial_result: float = 0
    ebt: float = 0
    ir_csll: float = 0
    net_income: float = 0
    net_margin: float = 0
    # Cash flow
    capex: float = 0
    nwc_change: float = 0
    free_cash_flow: float = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FinancialModel:
    """Complete financial model for one entity."""
    entity_name: str = ""
    assumptions: ModelAssumptions = field(default_factory=ModelAssumptions)
    historical: list[ProjectionYear] = field(default_factory=list)
    projected: list[ProjectionYear] = field(default_factory=list)

    @property
    def all_years(self) -> list[ProjectionYear]:
        return self.historical + self.projected

    @property
    def last_historical(self) -> ProjectionYear | None:
        return self.historical[-1] if self.historical else None

    @property
    def projection_years(self) -> list[str]:
        return [p.year for p in self.projected]

    def summary(self) -> dict:
        """Key metrics summary."""
        last_hist = self.last_historical
        last_proj = self.projected[-1] if self.projected else None
        return {
            "entity": self.entity_name,
            "historical_years": len(self.historical),
            "projected_years": len(self.projected),
            "last_hist_revenue": last_hist.net_revenue if last_hist else 0,
            "last_hist_ebitda": last_hist.ebitda if last_hist else 0,
            "last_proj_revenue": last_proj.net_revenue if last_proj else 0,
            "last_proj_ebitda": last_proj.ebitda if last_proj else 0,
            "last_proj_fcf": last_proj.free_cash_flow if last_proj else 0,
            "revenue_cagr": self._calc_cagr("net_revenue"),
            "ebitda_cagr": self._calc_cagr("ebitda"),
        }

    def _calc_cagr(self, field_name: str) -> float | None:
        """Calculate CAGR between first historical and last projected."""
        if not self.historical or not self.projected:
            return None
        start = getattr(self.historical[0], field_name, 0)
        end = getattr(self.projected[-1], field_name, 0)
        if not start or start <= 0 or not end or end <= 0:
            return None
        n = len(self.historical) + len(self.projected) - 1
        return (end / start) ** (1 / n) - 1

    def to_dict(self) -> dict:
        return {
            "entity_name": self.entity_name,
            "assumptions": self.assumptions.to_dict(),
            "historical": [y.to_dict() for y in self.historical],
            "projected": [y.to_dict() for y in self.projected],
            "summary": self.summary(),
        }


@dataclass
class ConsolidatedModel:
    """Consolidated model summing all entities."""
    entities: list[FinancialModel] = field(default_factory=list)
    consolidated: list[ProjectionYear] = field(default_factory=list)

    def build_consolidated(self):
        """Sum all entity projections into a consolidated view."""
        if not self.entities:
            return

        # Get all years across all entities
        all_years_set = set()
        for ent in self.entities:
            for y in ent.all_years:
                all_years_set.add(y.year)

        self.consolidated = []
        for year_str in sorted(all_years_set):
            cons = ProjectionYear(year=year_str, is_projected="E" in year_str)

            for ent in self.entities:
                for y in ent.all_years:
                    if y.year == year_str:
                        cons.gross_revenue += y.gross_revenue
                        cons.taxes_deductions += y.taxes_deductions
                        cons.net_revenue += y.net_revenue
                        cons.cogs += y.cogs
                        cons.gross_profit += y.gross_profit
                        cons.sga += y.sga
                        cons.ebitda += y.ebitda
                        cons.da += y.da
                        cons.ebit += y.ebit
                        cons.financial_result += y.financial_result
                        cons.ebt += y.ebt
                        cons.ir_csll += y.ir_csll
                        cons.net_income += y.net_income
                        cons.capex += y.capex
                        cons.nwc_change += y.nwc_change
                        cons.free_cash_flow += y.free_cash_flow
                        break

            # Recalculate margins
            if cons.net_revenue:
                cons.ebitda_margin = cons.ebitda / cons.net_revenue
                cons.net_margin = cons.net_income / cons.net_revenue

            self.consolidated.append(cons)

    def summary(self) -> dict:
        last = self.consolidated[-1] if self.consolidated else None
        first = self.consolidated[0] if self.consolidated else None
        return {
            "entities": [e.entity_name for e in self.entities],
            "years": len(self.consolidated),
            "last_year_revenue": last.net_revenue if last else 0,
            "last_year_ebitda": last.ebitda if last else 0,
            "last_year_fcf": last.free_cash_flow if last else 0,
        }

    def to_dict(self) -> dict:
        return {
            "entities": [e.to_dict() for e in self.entities],
            "consolidated": [y.to_dict() for y in self.consolidated],
            "summary": self.summary(),
        }


# ═══════════════════════════════════════════════════════════════
# MODEL BUILDER
# ═══════════════════════════════════════════════════════════════
def _extract_dre_value(stmt: FinancialStatement, year: str, label_keywords: list[str]) -> float:
    """Find a value in a DRE by matching label keywords."""
    for line in stmt.lines:
        label_lower = line.label.lower()
        if any(kw in label_lower for kw in label_keywords):
            val = line.values.get(year)
            if val is not None:
                return float(val)
    return 0.0


def _derive_assumptions(stmt: FinancialStatement, entity_name: str) -> ModelAssumptions:
    """Derive model assumptions from the last historical year's data."""
    assumptions = ModelAssumptions(entity_name=entity_name, label="Base")

    if not stmt or not stmt.lines or not stmt.years:
        return assumptions

    # Find the last historical year (no 'E' suffix)
    hist_years = [y for y in stmt.years if "E" not in y]
    if not hist_years:
        return assumptions

    last_year = hist_years[-1]
    prev_year = hist_years[-2] if len(hist_years) >= 2 else None

    # Extract key values for the last historical year
    gross_rev = _extract_dre_value(stmt, last_year, ["receita bruta"])
    net_rev = _extract_dre_value(stmt, last_year, ["receita líquida", "(=) receita"])
    cogs = abs(_extract_dre_value(stmt, last_year, ["cogs", "custo"]))
    sga = abs(_extract_dre_value(stmt, last_year, ["sg&a", "despesas"]))
    taxes = abs(_extract_dre_value(stmt, last_year, ["impostos", "devoluções"]))
    da = abs(_extract_dre_value(stmt, last_year, ["d&a", "depreciação"]))
    fin_result = _extract_dre_value(stmt, last_year, ["resultado financeiro"])
    ebt = _extract_dre_value(stmt, last_year, ["ebt", "(=) ebt"])
    ir = abs(_extract_dre_value(stmt, last_year, ["imposto de renda", "ir", "csll"]))

    # Calculate ratios (with safe division)
    if net_rev > 0:
        assumptions.cogs_pct_revenue = cogs / net_rev
        assumptions.sga_pct_revenue = sga / net_rev
        assumptions.da_pct_revenue = da / net_rev
        assumptions.financial_result_pct_revenue = fin_result / net_rev

    if gross_rev > 0:
        assumptions.tax_on_gross_revenue_pct = taxes / gross_rev

    if ebt and ebt > 0:
        assumptions.ir_csll_pct_ebt = ir / ebt
    elif ebt and ebt < 0:
        assumptions.ir_csll_pct_ebt = 0.34  # Default when EBT is negative

    # Revenue growth: calculate from last 2 historical years
    if prev_year:
        prev_net_rev = _extract_dre_value(stmt, prev_year, ["receita líquida", "(=) receita"])
        if prev_net_rev > 0 and net_rev > 0:
            assumptions.revenue_growth_rate = (net_rev / prev_net_rev) - 1

    # Default CAPEX and NWC (not in DRE, use conservative defaults)
    assumptions.capex_pct_revenue = 0.03
    assumptions.nwc_change_pct_revenue = 0.02

    return assumptions


def _build_historical(stmt: FinancialStatement) -> list[ProjectionYear]:
    """Convert DRE historical data into ProjectionYear objects."""
    years = []
    if not stmt or not stmt.lines or not stmt.years:
        return years

    for year_str in stmt.years:
        is_proj = "E" in year_str
        py = ProjectionYear(year=year_str, is_projected=is_proj)

        py.gross_revenue = _extract_dre_value(stmt, year_str, ["receita bruta"])
        py.taxes_deductions = _extract_dre_value(stmt, year_str, ["impostos", "devoluções"])
        py.net_revenue = _extract_dre_value(stmt, year_str, ["receita líquida", "(=) receita"])
        py.cogs = _extract_dre_value(stmt, year_str, ["cogs", "custo"])
        py.gross_profit = _extract_dre_value(stmt, year_str, ["lucro bruto"])
        py.sga = _extract_dre_value(stmt, year_str, ["sg&a"])
        py.ebitda = _extract_dre_value(stmt, year_str, ["ebitda"])
        py.da = _extract_dre_value(stmt, year_str, ["d&a", "depreciação"])
        py.ebit = _extract_dre_value(stmt, year_str, ["(=) ebit", "ebit"])
        py.financial_result = _extract_dre_value(stmt, year_str, ["resultado financeiro"])
        py.ebt = _extract_dre_value(stmt, year_str, ["(=) ebt", "ebt"])
        py.ir_csll = _extract_dre_value(stmt, year_str, ["imposto de renda", "ir", "csll"])
        py.net_income = _extract_dre_value(stmt, year_str, ["lucro líquido"])

        # Margins
        if py.net_revenue:
            py.ebitda_margin = py.ebitda / py.net_revenue
            py.net_margin = py.net_income / py.net_revenue

        # FCF (estimated from DRE — no balance sheet data for NWC)
        py.capex = 0  # Not available from DRE
        py.nwc_change = 0
        py.free_cash_flow = py.ebitda - py.capex - py.nwc_change

        years.append(py)

    return years


def _project_years(
    base_year: ProjectionYear,
    assumptions: ModelAssumptions,
    n_years: int = 5,
    start_year: int = 2026,
) -> list[ProjectionYear]:
    """Generate projected years from a base year and assumptions."""
    projected = []
    prev = base_year

    for i in range(n_years):
        year_str = f"{start_year + i}E"
        py = ProjectionYear(year=year_str, is_projected=True)

        # Revenue projection
        py.gross_revenue = prev.gross_revenue * (1 + assumptions.revenue_growth_rate)
        py.taxes_deductions = -abs(py.gross_revenue * assumptions.tax_on_gross_revenue_pct)
        py.net_revenue = py.gross_revenue + py.taxes_deductions

        # Cost structure
        py.cogs = -abs(py.net_revenue * assumptions.cogs_pct_revenue)
        py.gross_profit = py.net_revenue + py.cogs
        py.sga = -abs(py.net_revenue * assumptions.sga_pct_revenue)

        # EBITDA
        py.ebitda = py.gross_profit + py.sga
        py.ebitda_margin = py.ebitda / py.net_revenue if py.net_revenue else 0

        # Below EBITDA
        py.da = -abs(py.net_revenue * assumptions.da_pct_revenue)
        py.ebit = py.ebitda + py.da
        py.financial_result = py.net_revenue * assumptions.financial_result_pct_revenue
        py.ebt = py.ebit + py.financial_result

        # Taxes
        if py.ebt > 0:
            py.ir_csll = -abs(py.ebt * assumptions.ir_csll_pct_ebt)
        else:
            py.ir_csll = 0  # No tax on losses
        py.net_income = py.ebt + py.ir_csll
        py.net_margin = py.net_income / py.net_revenue if py.net_revenue else 0

        # Free cash flow
        py.capex = -abs(py.net_revenue * assumptions.capex_pct_revenue)
        py.nwc_change = -abs(py.net_revenue * assumptions.nwc_change_pct_revenue)
        py.free_cash_flow = py.ebitda + py.capex + py.nwc_change

        projected.append(py)
        prev = py

    return projected


def _adjust_projections(
    existing: list[ProjectionYear],
    factors: dict,
    assumptions: ModelAssumptions,
) -> list[ProjectionYear]:
    """Adjust CIM projections by scaling factors.

    Preserves the shape of the company plan while scaling key line items.
    This is the preferred method for scenario analysis because it keeps
    structural elements like ramp-ups (e.g., Lojas Próprias going from 0 to 90k).

    Args:
        existing: CIM projected years
        factors: Dict with revenue_factor, cogs_factor, sga_factor
        assumptions: For CAPEX/NWC estimation
    """
    from copy import deepcopy

    rev_factor = factors.get("revenue_factor", 1.0)
    cogs_factor = factors.get("cogs_factor", 1.0)
    sga_factor = factors.get("sga_factor", 1.0)

    adjusted = []
    for orig in existing:
        py = deepcopy(orig)

        # Scale revenue
        py.gross_revenue = orig.gross_revenue * rev_factor
        py.taxes_deductions = orig.taxes_deductions * rev_factor
        py.net_revenue = orig.net_revenue * rev_factor

        # Scale costs (factors > 1 = worse margins)
        py.cogs = orig.cogs * cogs_factor * rev_factor
        py.gross_profit = py.net_revenue + py.cogs
        py.sga = orig.sga * sga_factor * rev_factor

        # Recalculate EBITDA
        py.ebitda = py.gross_profit + py.sga
        py.ebitda_margin = py.ebitda / py.net_revenue if py.net_revenue else 0

        # Below EBITDA: scale proportionally
        py.da = orig.da * rev_factor
        py.ebit = py.ebitda + py.da
        py.financial_result = orig.financial_result * rev_factor
        py.ebt = py.ebit + py.financial_result

        # Taxes
        if py.ebt > 0:
            py.ir_csll = -abs(py.ebt * assumptions.ir_csll_pct_ebt)
        else:
            py.ir_csll = 0
        py.net_income = py.ebt + py.ir_csll
        py.net_margin = py.net_income / py.net_revenue if py.net_revenue else 0

        # FCF
        py.capex = -abs(py.net_revenue * assumptions.capex_pct_revenue)
        py.nwc_change = -abs(py.net_revenue * assumptions.nwc_change_pct_revenue)
        py.free_cash_flow = py.ebitda + py.capex + py.nwc_change

        adjusted.append(py)

    return adjusted


def build_entity_model(
    stmt: FinancialStatement,
    entity_name: str,
    assumption_overrides: dict | None = None,
    n_projection_years: int = 5,
    force_reproject: bool = False,
    adjustment_factors: dict | None = None,
    verbose: bool = False,
) -> FinancialModel:
    """Build a financial model for a single entity.

    Args:
        stmt: The DRE financial statement
        entity_name: Name of the entity
        assumption_overrides: Dict of assumption fields to override
        n_projection_years: Number of years to project
        force_reproject: If True, regenerate projections from last historical year
        adjustment_factors: Dict with scaling factors to apply to CIM projections.
            Keys: revenue_factor, cogs_factor, sga_factor
            E.g. {"revenue_factor": 0.85, "cogs_factor": 1.10, "sga_factor": 1.05}
            This preserves the CIM's projection shape while scaling up/down.
        verbose: Print progress
    """
    model = FinancialModel(entity_name=entity_name)

    if not stmt or not stmt.lines:
        if verbose:
            print(f"    ⚠️  No DRE data for {entity_name}")
        return model

    # Derive assumptions from historical data
    assumptions = _derive_assumptions(stmt, entity_name)

    # Apply overrides
    if assumption_overrides:
        for key, val in assumption_overrides.items():
            if hasattr(assumptions, key):
                setattr(assumptions, key, val)

    model.assumptions = assumptions

    # Build historical years from DRE
    all_years = _build_historical(stmt)

    # Split into historical and already-projected
    model.historical = [y for y in all_years if not y.is_projected]
    existing_projected = [y for y in all_years if y.is_projected]

    # Strategy: if adjustment_factors provided, scale CIM projections (preferred for scenarios)
    if existing_projected and adjustment_factors:
        model.projected = _adjust_projections(existing_projected, adjustment_factors, assumptions)
    elif existing_projected and not force_reproject:
        # Use CIM projections as-is (base case)
        model.projected = existing_projected
        # Add FCF estimates to existing projections
        for py in model.projected:
            if py.net_revenue:
                py.capex = -abs(py.net_revenue * assumptions.capex_pct_revenue)
                py.nwc_change = -abs(py.net_revenue * assumptions.nwc_change_pct_revenue)
                py.free_cash_flow = py.ebitda + py.capex + py.nwc_change
    else:
        # Generate projections
        base = model.historical[-1] if model.historical else ProjectionYear()
        last_hist_year = int(model.historical[-1].year) if model.historical else 2025
        model.projected = _project_years(
            base, assumptions, n_projection_years, last_hist_year + 1
        )

    if verbose:
        s = model.summary()
        print(f"    ✅ {entity_name}: {s['historical_years']}h + {s['projected_years']}p years")
        if s.get("revenue_cagr"):
            print(f"       Revenue CAGR: {s['revenue_cagr']*100:.1f}%")

    return model


def build_model_from_dossier(
    dossier: Dossier,
    assumption_overrides: dict[str, dict] | None = None,
    verbose: bool = False,
) -> ConsolidatedModel:
    """Build financial models for all entities in the dossier.

    Args:
        dossier: The dossier with financial data
        assumption_overrides: Dict of {entity_name: {assumption_field: value}}
        verbose: Print progress

    Returns:
        ConsolidatedModel with per-entity and consolidated projections
    """
    if verbose:
        print("  [Valuation] Building financial model...")

    fin = dossier.financials
    entities = [(e.name, e.dre) for e in fin.entities]

    consolidated = ConsolidatedModel()

    for name, stmt in entities:
        overrides = (assumption_overrides or {}).get(name)
        model = build_entity_model(
            stmt, name, overrides, verbose=verbose,
        )
        if model.historical or model.projected:
            consolidated.entities.append(model)

    # Build consolidated view
    consolidated.build_consolidated()

    if verbose:
        s = consolidated.summary()
        print(f"    ✅ Consolidado: {s['years']} years, "
              f"last revenue={s['last_year_revenue']:,.0f}, "
              f"last EBITDA={s['last_year_ebitda']:,.0f}")

    return consolidated