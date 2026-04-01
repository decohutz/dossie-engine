"""
Chapter 3: Market and Chapter 4: Transaction models.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from .evidence import Evidence, TrackedField


# --- MARKET ---

@dataclass
class MarketSize:
    geography: str = ""         # "Brasil", "Global"
    value: float = 0.0
    unit: str = "BRL Bn"
    year: int = 0
    cagr: float | None = None
    evidence: Evidence = field(default_factory=lambda: Evidence(source_file=""))

    def to_dict(self) -> dict:
        return {
            "geography": self.geography, "value": self.value, "unit": self.unit,
            "year": self.year, "cagr": self.cagr, "evidence": self.evidence.to_dict(),
        }


@dataclass
class Competitor:
    name: str = ""
    stores: int | None = None
    revenue: float | None = None
    revenue_unit: str | None = None
    market_share_pct: float | None = None
    investor: str | None = None
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    evidence: Evidence = field(default_factory=lambda: Evidence(source_file=""))

    def to_dict(self) -> dict:
        return {
            "name": self.name, "stores": self.stores, "revenue": self.revenue,
            "revenue_unit": self.revenue_unit, "market_share_pct": self.market_share_pct,
            "investor": self.investor, "strengths": self.strengths, "weaknesses": self.weaknesses,
            "evidence": self.evidence.to_dict(),
        }


@dataclass
class PrecedentTransaction:
    date: str = ""
    buyer: str = ""
    target: str = ""
    description: str = ""
    stake_pct: float | None = None
    value: str = ""              # "~USD 920MM"
    ev_revenue: float | None = None
    ev_ebitda: float | None = None
    evidence: Evidence = field(default_factory=lambda: Evidence(source_file=""))

    def to_dict(self) -> dict:
        return {
            "date": self.date, "buyer": self.buyer, "target": self.target,
            "description": self.description, "stake_pct": self.stake_pct,
            "value": self.value, "ev_revenue": self.ev_revenue, "ev_ebitda": self.ev_ebitda,
            "evidence": self.evidence.to_dict(),
        }


@dataclass
class MarketChapter:
    market_sizes: list[MarketSize] = field(default_factory=list)
    growth_drivers: list[TrackedField] = field(default_factory=list)
    market_fragmentation: TrackedField = field(default_factory=TrackedField.empty)
    competitors: list[Competitor] = field(default_factory=list)
    value_chain: TrackedField = field(default_factory=TrackedField.empty)
    barriers_to_entry: list[TrackedField] = field(default_factory=list)
    precedent_transactions: list[PrecedentTransaction] = field(default_factory=list)
    global_multiples_median: TrackedField = field(default_factory=TrackedField.empty)

    def to_dict(self) -> dict:
        return {
            "market_sizes": [m.to_dict() for m in self.market_sizes],
            "growth_drivers": [g.to_dict() for g in self.growth_drivers],
            "market_fragmentation": self.market_fragmentation.to_dict(),
            "competitors": [c.to_dict() for c in self.competitors],
            "value_chain": self.value_chain.to_dict(),
            "barriers_to_entry": [b.to_dict() for b in self.barriers_to_entry],
            "precedent_transactions": [t.to_dict() for t in self.precedent_transactions],
            "global_multiples_median": self.global_multiples_median.to_dict(),
        }


# --- TRANSACTION ---

@dataclass
class TransactionChapter:
    context: TrackedField = field(default_factory=TrackedField.empty)
    transaction_type: TrackedField = field(default_factory=TrackedField.empty)
    target_stake_range: TrackedField = field(default_factory=TrackedField.empty)
    capital_needed: TrackedField = field(default_factory=TrackedField.empty)
    opex_component: TrackedField = field(default_factory=TrackedField.empty)
    capex_component: TrackedField = field(default_factory=TrackedField.empty)
    use_of_proceeds: TrackedField = field(default_factory=TrackedField.empty)
    advisor: TrackedField = field(default_factory=TrackedField.empty)
    perimeter: TrackedField = field(default_factory=TrackedField.empty)

    def to_dict(self) -> dict:
        return {k: getattr(self, k).to_dict() for k in self.__dataclass_fields__}
