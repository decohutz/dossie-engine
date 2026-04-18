"""
DCF (Discounted Cash Flow) valuation.

Calculates enterprise value by discounting projected free cash flows
and adding a terminal value (perpetuity growth or exit multiple).

Includes WACC estimation and equity bridge (EV → Equity Value).
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional

from .model import ProjectionYear


@dataclass
class WACCInputs:
    """Inputs for WACC calculation."""
    # Cost of equity (CAPM)
    risk_free_rate: float = 0.045       # Selic / US Treasury + Brazil CDS
    equity_risk_premium: float = 0.065  # Brazil ERP
    beta: float = 1.0                   # Unlevered beta (retail/franchise)
    size_premium: float = 0.02          # Small-cap premium
    country_risk: float = 0.025         # Brazil country risk
    # Cost of debt
    cost_of_debt_pretax: float = 0.12   # CDI + spread
    tax_rate: float = 0.34              # IR+CSLL
    # Capital structure
    debt_to_total: float = 0.20         # D / (D+E)
    equity_to_total: float = 0.80       # E / (D+E)

    @property
    def cost_of_equity(self) -> float:
        """CAPM: Ke = Rf + β × ERP + size premium + country risk"""
        return (self.risk_free_rate
                + self.beta * self.equity_risk_premium
                + self.size_premium
                + self.country_risk)

    @property
    def cost_of_debt_aftertax(self) -> float:
        return self.cost_of_debt_pretax * (1 - self.tax_rate)

    @property
    def wacc(self) -> float:
        return (self.equity_to_total * self.cost_of_equity
                + self.debt_to_total * self.cost_of_debt_aftertax)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["cost_of_equity"] = self.cost_of_equity
        d["cost_of_debt_aftertax"] = self.cost_of_debt_aftertax
        d["wacc"] = self.wacc
        return d


@dataclass
class EquityBridge:
    """Bridge from Enterprise Value to Equity Value."""
    enterprise_value: float = 0
    net_debt: float = 0          # Dívida líquida (positivo = mais dívida que caixa)
    minority_interests: float = 0
    equity_value: float = 0

    def compute(self):
        self.equity_value = self.enterprise_value - self.net_debt - self.minority_interests

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DCFResult:
    """Complete DCF valuation result."""
    wacc_inputs: WACCInputs = field(default_factory=WACCInputs)
    wacc: float = 0
    # FCF schedule
    projected_fcfs: list[dict] = field(default_factory=list)  # [{year, fcf, discount_factor, pv}]
    pv_fcfs: float = 0
    # Terminal value
    terminal_method: str = "perpetuity"  # "perpetuity" or "exit_multiple"
    terminal_growth_rate: float = 0.03   # g for perpetuity
    exit_multiple: float = 11.0          # EV/EBITDA for exit multiple
    terminal_value: float = 0
    pv_terminal: float = 0
    # Totals
    enterprise_value: float = 0
    bridge: EquityBridge = field(default_factory=EquityBridge)
    # Implied metrics
    implied_ev_ebitda: float = 0
    implied_ev_revenue: float = 0

    def to_dict(self) -> dict:
        return {
            "wacc_inputs": self.wacc_inputs.to_dict(),
            "wacc": self.wacc,
            "projected_fcfs": self.projected_fcfs,
            "pv_fcfs": self.pv_fcfs,
            "terminal_method": self.terminal_method,
            "terminal_growth_rate": self.terminal_growth_rate,
            "exit_multiple": self.exit_multiple,
            "terminal_value": self.terminal_value,
            "pv_terminal": self.pv_terminal,
            "enterprise_value": self.enterprise_value,
            "bridge": self.bridge.to_dict(),
            "implied_ev_ebitda": self.implied_ev_ebitda,
            "implied_ev_revenue": self.implied_ev_revenue,
        }


def run_dcf(
    projected_years: list[ProjectionYear],
    wacc_inputs: WACCInputs | None = None,
    terminal_method: str = "perpetuity",
    terminal_growth_rate: float = 0.03,
    exit_multiple: float = 11.0,
    net_debt: float = 0,
    verbose: bool = False,
) -> DCFResult:
    """Run a DCF valuation on projected cash flows.

    Args:
        projected_years: List of projected years with FCF
        wacc_inputs: WACC parameters (uses defaults if None)
        terminal_method: "perpetuity" or "exit_multiple"
        terminal_growth_rate: Long-term growth rate for perpetuity
        exit_multiple: EV/EBITDA for exit multiple method
        net_debt: Net debt for equity bridge
        verbose: Print progress
    """
    if not wacc_inputs:
        wacc_inputs = WACCInputs()

    result = DCFResult(wacc_inputs=wacc_inputs)
    result.wacc = wacc_inputs.wacc
    result.terminal_method = terminal_method
    result.terminal_growth_rate = terminal_growth_rate
    result.exit_multiple = exit_multiple

    if not projected_years:
        return result

    # Filter to only projected years with positive FCF potential
    proj = [y for y in projected_years if y.is_projected]
    if not proj:
        proj = projected_years

    # Discount projected FCFs
    fcf_schedule = []
    total_pv_fcf = 0
    for i, year in enumerate(proj):
        t = i + 1  # Year 1, 2, 3...
        discount_factor = 1 / (1 + result.wacc) ** t
        pv = year.free_cash_flow * discount_factor

        fcf_schedule.append({
            "year": year.year,
            "fcf": year.free_cash_flow,
            "ebitda": year.ebitda,
            "discount_factor": round(discount_factor, 4),
            "pv": round(pv, 1),
        })
        total_pv_fcf += pv

    result.projected_fcfs = fcf_schedule
    result.pv_fcfs = total_pv_fcf

    # Terminal value
    last_year = proj[-1]
    n = len(proj)

    if terminal_method == "perpetuity":
        # Gordon Growth: TV = FCF_terminal × (1+g) / (WACC - g)
        if result.wacc > terminal_growth_rate:
            result.terminal_value = (
                last_year.free_cash_flow * (1 + terminal_growth_rate)
                / (result.wacc - terminal_growth_rate)
            )
        else:
            # Fallback if WACC ≤ g (shouldn't happen with reasonable inputs)
            result.terminal_value = last_year.free_cash_flow * 15
    else:
        # Exit multiple: TV = EBITDA_terminal × multiple
        result.terminal_value = last_year.ebitda * exit_multiple

    # Discount terminal value back
    terminal_discount = 1 / (1 + result.wacc) ** n
    result.pv_terminal = result.terminal_value * terminal_discount

    # Enterprise Value = PV(FCFs) + PV(TV)
    result.enterprise_value = result.pv_fcfs + result.pv_terminal

    # Equity bridge
    result.bridge = EquityBridge(
        enterprise_value=result.enterprise_value,
        net_debt=net_debt,
    )
    result.bridge.compute()

    # Implied multiples
    if last_year.ebitda and last_year.ebitda > 0:
        result.implied_ev_ebitda = result.enterprise_value / last_year.ebitda
    if last_year.net_revenue and last_year.net_revenue > 0:
        result.implied_ev_revenue = result.enterprise_value / last_year.net_revenue

    if verbose:
        print(f"      WACC: {result.wacc*100:.1f}%")
        print(f"      PV(FCFs): {result.pv_fcfs:,.0f}")
        print(f"      Terminal ({terminal_method}): {result.terminal_value:,.0f}")
        print(f"      PV(Terminal): {result.pv_terminal:,.0f}")
        print(f"      EV: {result.enterprise_value:,.0f}")
        print(f"      Equity Value: {result.bridge.equity_value:,.0f}")
        print(f"      Implied EV/EBITDA: {result.implied_ev_ebitda:.1f}x")

    return result