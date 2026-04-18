"""
Multiples-based valuation and IRR calculation.

Applies market multiples (EV/EBITDA, EV/Revenue) to derive enterprise value,
and calculates IRR for the investor based on entry/exit assumptions.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional

from .model import ProjectionYear


# ═══════════════════════════════════════════════════════════════
# MULTIPLES VALUATION
# ═══════════════════════════════════════════════════════════════
@dataclass
class MultiplesResult:
    """Valuation by comparable multiples."""
    # EV/EBITDA
    ebitda_reference: float = 0        # EBITDA used (terminal year)
    ev_ebitda_multiple: float = 0      # Multiple applied
    ev_by_ebitda: float = 0            # Enterprise value
    # EV/Revenue
    revenue_reference: float = 0       # Revenue used (terminal year)
    ev_revenue_multiple: float = 0     # Multiple applied
    ev_by_revenue: float = 0           # Enterprise value
    # Blended
    ev_blended: float = 0              # Average of the two methods
    equity_blended: float = 0          # After net debt
    net_debt: float = 0
    # Source
    reference_year: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def run_multiples(
    projected_years: list[ProjectionYear],
    ev_ebitda_multiple: float = 11.0,
    ev_revenue_multiple: float = 1.8,
    net_debt: float = 0,
    reference_year_index: int = -1,
    verbose: bool = False,
) -> MultiplesResult:
    """Value by applying market multiples to projected financials.

    Args:
        projected_years: Projected years from the model
        ev_ebitda_multiple: EV/EBITDA multiple (from market data)
        ev_revenue_multiple: EV/Revenue multiple (from market data)
        net_debt: Net debt for equity bridge
        reference_year_index: Which projected year to use (-1 = last)
        verbose: Print progress
    """
    result = MultiplesResult(
        ev_ebitda_multiple=ev_ebitda_multiple,
        ev_revenue_multiple=ev_revenue_multiple,
        net_debt=net_debt,
    )

    if not projected_years:
        return result

    ref = projected_years[reference_year_index]
    result.reference_year = ref.year
    result.ebitda_reference = ref.ebitda
    result.revenue_reference = ref.net_revenue

    # EV by EBITDA
    result.ev_by_ebitda = ref.ebitda * ev_ebitda_multiple

    # EV by Revenue
    result.ev_by_revenue = ref.net_revenue * ev_revenue_multiple

    # Blended (simple average)
    result.ev_blended = (result.ev_by_ebitda + result.ev_by_revenue) / 2
    result.equity_blended = result.ev_blended - net_debt

    if verbose:
        print(f"      Ref year: {ref.year}")
        print(f"      EV/EBITDA ({ev_ebitda_multiple}x): {result.ev_by_ebitda:,.0f}")
        print(f"      EV/Revenue ({ev_revenue_multiple}x): {result.ev_by_revenue:,.0f}")
        print(f"      Blended EV: {result.ev_blended:,.0f}")

    return result


# ═══════════════════════════════════════════════════════════════
# IRR CALCULATION
# ═══════════════════════════════════════════════════════════════
@dataclass
class IRRResult:
    """IRR calculation result for the investor."""
    entry_equity_value: float = 0      # Equity value at entry
    stake_pct: float = 0.30            # Investor's stake (e.g., 30%)
    entry_check: float = 0             # Money invested (entry_equity × stake)
    holding_period: int = 5            # Years
    exit_ev_ebitda: float = 0          # Exit multiple
    exit_ebitda: float = 0             # EBITDA at exit year
    exit_ev: float = 0                 # EV at exit
    exit_equity: float = 0             # Equity at exit
    exit_proceeds: float = 0           # Investor's share at exit
    irr: float = 0                     # Internal rate of return
    moic: float = 0                    # Multiple on invested capital
    # Cash flow schedule
    cash_flows: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _calc_irr(cash_flows: list[float], max_iter: int = 1000, tol: float = 1e-8) -> float:
    """Calculate IRR using Newton's method.

    Args:
        cash_flows: List of cash flows (first is negative = investment)
        max_iter: Maximum iterations
        tol: Convergence tolerance

    Returns:
        IRR as a decimal (e.g., 0.25 = 25%)
    """
    if not cash_flows or len(cash_flows) < 2:
        return 0.0

    # Initial guess
    rate = 0.15

    for _ in range(max_iter):
        npv = sum(cf / (1 + rate) ** t for t, cf in enumerate(cash_flows))
        dnpv = sum(-t * cf / (1 + rate) ** (t + 1) for t, cf in enumerate(cash_flows))

        if abs(dnpv) < 1e-12:
            break

        new_rate = rate - npv / dnpv

        # Guard against divergence
        if new_rate < -0.99:
            new_rate = -0.5
        if new_rate > 10:
            new_rate = 5.0

        if abs(new_rate - rate) < tol:
            return new_rate

        rate = new_rate

    return rate


def run_irr(
    projected_years: list[ProjectionYear],
    entry_equity_value: float,
    stake_pct: float = 0.30,
    exit_ev_ebitda: float = 11.0,
    net_debt_at_exit: float = 0,
    holding_period: int = 5,
    dividends_pct_fcf: float = 0.0,
    verbose: bool = False,
) -> IRRResult:
    """Calculate IRR for the investor.

    Args:
        projected_years: Projected years from the model
        entry_equity_value: Equity value at entry (from DCF or multiples)
        stake_pct: Investor's ownership stake (0-1)
        exit_ev_ebitda: Exit multiple for terminal value
        net_debt_at_exit: Net debt at exit year
        holding_period: Investment period in years
        dividends_pct_fcf: % of FCF distributed as dividends (0-1)
        verbose: Print progress
    """
    result = IRRResult(
        entry_equity_value=entry_equity_value,
        stake_pct=stake_pct,
        holding_period=holding_period,
        exit_ev_ebitda=exit_ev_ebitda,
    )

    # Entry check
    result.entry_check = entry_equity_value * stake_pct

    if not projected_years or result.entry_check <= 0:
        return result

    # Build cash flow schedule
    # Year 0: investment (negative)
    cf_list = [-result.entry_check]
    schedule = [{"year": "Entry", "cash_flow": -result.entry_check, "type": "investment"}]

    # Intermediate years: dividends (if any)
    proj = [y for y in projected_years if y.is_projected]
    for i, year in enumerate(proj[:holding_period - 1]):
        dividend = year.free_cash_flow * dividends_pct_fcf * stake_pct
        cf_list.append(dividend)
        schedule.append({
            "year": year.year,
            "cash_flow": dividend,
            "type": "dividend",
        })

    # Exit year: sale proceeds
    exit_year_idx = min(holding_period - 1, len(proj) - 1)
    exit_year = proj[exit_year_idx] if proj else ProjectionYear()

    result.exit_ebitda = exit_year.ebitda
    result.exit_ev = exit_year.ebitda * exit_ev_ebitda
    result.exit_equity = result.exit_ev - net_debt_at_exit
    result.exit_proceeds = result.exit_equity * stake_pct

    cf_list.append(result.exit_proceeds)
    schedule.append({
        "year": exit_year.year,
        "cash_flow": result.exit_proceeds,
        "type": "exit",
    })

    result.cash_flows = schedule

    # Calculate IRR
    result.irr = _calc_irr(cf_list)

    # MOIC
    total_inflows = sum(cf for cf in cf_list[1:])
    result.moic = total_inflows / result.entry_check if result.entry_check > 0 else 0

    if verbose:
        print(f"      Entry: {result.entry_check:,.0f} ({stake_pct*100:.0f}% stake)")
        print(f"      Exit EBITDA: {result.exit_ebitda:,.0f} × {exit_ev_ebitda}x = EV {result.exit_ev:,.0f}")
        print(f"      Exit proceeds: {result.exit_proceeds:,.0f}")
        print(f"      IRR: {result.irr*100:.1f}%")
        print(f"      MOIC: {result.moic:.2f}x")

    return result


# ═══════════════════════════════════════════════════════════════
# SENSITIVITY TABLE
# ═══════════════════════════════════════════════════════════════
@dataclass
class ValuationSummary:
    """Complete valuation summary across methods and scenarios."""
    scenario_name: str = ""
    dcf_perpetuity: float = 0       # EV by DCF (perpetuity)
    dcf_exit_multiple: float = 0    # EV by DCF (exit multiple)
    multiples_ev_ebitda: float = 0  # EV by EV/EBITDA
    multiples_ev_revenue: float = 0 # EV by EV/Revenue
    ev_range_low: float = 0
    ev_range_high: float = 0
    equity_range_low: float = 0
    equity_range_high: float = 0
    irr: float = 0
    moic: float = 0

    def to_dict(self) -> dict:
        return asdict(self)


def build_valuation_summary(
    scenario_name: str,
    dcf_perp_ev: float,
    dcf_exit_ev: float,
    mult_ebitda_ev: float,
    mult_rev_ev: float,
    net_debt: float = 0,
    irr: float = 0,
    moic: float = 0,
) -> ValuationSummary:
    """Build a summary row for the sensitivity table."""
    evs = [v for v in [dcf_perp_ev, dcf_exit_ev, mult_ebitda_ev, mult_rev_ev] if v > 0]

    return ValuationSummary(
        scenario_name=scenario_name,
        dcf_perpetuity=dcf_perp_ev,
        dcf_exit_multiple=dcf_exit_ev,
        multiples_ev_ebitda=mult_ebitda_ev,
        multiples_ev_revenue=mult_rev_ev,
        ev_range_low=min(evs) if evs else 0,
        ev_range_high=max(evs) if evs else 0,
        equity_range_low=(min(evs) - net_debt) if evs else 0,
        equity_range_high=(max(evs) - net_debt) if evs else 0,
        irr=irr,
        moic=moic,
    )