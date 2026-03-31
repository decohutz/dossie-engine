"""
Chapter 2: Financial information models.
Handles DRE, Balance Sheet, and derived metrics.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from .evidence import Evidence, TrackedField


@dataclass
class FinancialLine:
    """A single line in a DRE or Balance Sheet.
    
    Example:
        FinancialLine(
            label="Receita Bruta",
            values={"2021": 22575, "2022": 30838, ...},
            is_projected={"2021": False, ..., "2026E": True},
        )
    """
    label: str = ""
    values: dict[str, float] = field(default_factory=dict)       # year -> value (BRL k)
    is_projected: dict[str, bool] = field(default_factory=dict)  # year -> is estimate?
    unit: str = "BRL k"
    evidence: Evidence = field(default_factory=lambda: Evidence(source_file=""))

    def to_dict(self) -> dict:
        return {
            "label": self.label, "values": self.values,
            "is_projected": self.is_projected, "unit": self.unit,
            "evidence": self.evidence.to_dict(),
        }


@dataclass
class FinancialStatement:
    """A complete financial statement (DRE or Balance Sheet) for one entity."""
    entity_name: str = ""              # "Franqueadora", "Distribuidora", "Lojas Próprias"
    statement_type: str = ""           # "dre" | "balance_sheet"
    lines: list[FinancialLine] = field(default_factory=list)
    years: list[str] = field(default_factory=list)
    evidence: Evidence = field(default_factory=lambda: Evidence(source_file=""))

    def get_line(self, label: str) -> FinancialLine | None:
        """Find a line by label (case-insensitive partial match)."""
        label_lower = label.lower()
        for line in self.lines:
            if label_lower in line.label.lower():
                return line
        return None

    def get_value(self, label: str, year: str) -> float | None:
        """Get a specific value by label and year."""
        line = self.get_line(label)
        if line and year in line.values:
            return line.values[year]
        return None

    def to_dict(self) -> dict:
        return {
            "entity_name": self.entity_name,
            "statement_type": self.statement_type,
            "lines": [l.to_dict() for l in self.lines],
            "years": self.years,
            "evidence": self.evidence.to_dict(),
        }


@dataclass
class FinancialMetrics:
    """Derived financial metrics computed from the statements."""
    ebitda_margin: TrackedField = field(default_factory=TrackedField.empty)
    net_margin: TrackedField = field(default_factory=TrackedField.empty)
    net_debt: TrackedField = field(default_factory=TrackedField.empty)
    leverage_ratio: TrackedField = field(default_factory=TrackedField.empty)

    def to_dict(self) -> dict:
        return {k: getattr(self, k).to_dict() for k in self.__dataclass_fields__}


@dataclass
class FinancialChapter:
    """All financial data for the dossier."""
    # Individual DREs
    dre_consolidated: FinancialStatement | None = None
    dre_franqueadora: FinancialStatement | None = None
    dre_distribuidora: FinancialStatement | None = None
    dre_lojas_proprias: FinancialStatement | None = None
    
    # Individual Balance Sheets
    balance_franqueadora: FinancialStatement | None = None
    balance_distribuidora: FinancialStatement | None = None
    balance_lojas_proprias: FinancialStatement | None = None
    
    # Derived
    metrics: FinancialMetrics = field(default_factory=FinancialMetrics)
    capex_projection: TrackedField = field(default_factory=TrackedField.empty)
    dividend_projection: TrackedField = field(default_factory=TrackedField.empty)

    def all_statements(self) -> list[FinancialStatement]:
        """Return all non-None statements."""
        return [s for s in [
            self.dre_franqueadora, self.dre_distribuidora, self.dre_lojas_proprias,
            self.balance_franqueadora, self.balance_distribuidora, self.balance_lojas_proprias,
        ] if s is not None]

    def to_dict(self) -> dict:
        result = {}
        for name in ["dre_consolidated", "dre_franqueadora", "dre_distribuidora", 
                      "dre_lojas_proprias", "balance_franqueadora", "balance_distribuidora",
                      "balance_lojas_proprias"]:
            val = getattr(self, name)
            result[name] = val.to_dict() if val else None
        result["metrics"] = self.metrics.to_dict()
        result["capex_projection"] = self.capex_projection.to_dict()
        result["dividend_projection"] = self.dividend_projection.to_dict()
        return result
