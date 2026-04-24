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
class FinancialEntity:
    """A single entity within the group (subsidiary, segment, or business unit),
    grouping its DRE and Balance Sheet together.

    Examples:
        FinancialEntity(name="Franqueadora", dre=<stmt>, balance_sheet=<stmt>)
        FinancialEntity(name="SaaS Co", dre=<stmt>)  # no balance sheet available
    """
    name: str
    dre: FinancialStatement | None = None
    balance_sheet: FinancialStatement | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "dre": self.dre.to_dict() if self.dre else None,
            "balance_sheet": self.balance_sheet.to_dict() if self.balance_sheet else None,
        }


@dataclass
class FinancialChapter:
    """All financial data for the dossier.

    Entities are discovered dynamically from the source document (one per
    subsidiary / segment / business unit). The chapter supports 1 or N
    entities — a SaaS company with a single P&L produces 1 entity, while a
    holding with 5 subsidiaries produces 5.
    """
    entities: list[FinancialEntity] = field(default_factory=list)

    # Consolidated view (from the CIM itself, if provided)
    dre_consolidated: FinancialStatement | None = None

    # Derived
    metrics: FinancialMetrics = field(default_factory=FinancialMetrics)
    capex_projection: TrackedField = field(default_factory=TrackedField.empty)
    dividend_projection: TrackedField = field(default_factory=TrackedField.empty)

    # ── Lookup / upsert helpers ───────────────────────────────────────
    def get_entity(self, name: str) -> FinancialEntity | None:
        """Find an entity by name (case-insensitive, accent-insensitive)."""
        target = _normalize(name)
        for e in self.entities:
            if _normalize(e.name) == target:
                return e
        return None

    def _get_or_create_entity(self, name: str) -> FinancialEntity:
        """Get existing entity by name or create and append a new one."""
        existing = self.get_entity(name)
        if existing is not None:
            return existing
        entity = FinancialEntity(name=name)
        self.entities.append(entity)
        return entity

    def upsert_dre(self, entity_name: str, stmt: FinancialStatement) -> None:
        """Attach a DRE to the given entity, creating the entity if needed."""
        self._get_or_create_entity(entity_name).dre = stmt

    def upsert_balance(self, entity_name: str, stmt: FinancialStatement) -> None:
        """Attach a balance sheet to the given entity, creating the entity if needed."""
        self._get_or_create_entity(entity_name).balance_sheet = stmt

    # ── Bulk accessors ────────────────────────────────────────────────
    @property
    def all_dres(self) -> list[FinancialStatement]:
        """Return all non-None DREs, in entity discovery order."""
        return [e.dre for e in self.entities if e.dre is not None]

    @property
    def all_balances(self) -> list[FinancialStatement]:
        """Return all non-None balance sheets, in entity discovery order."""
        return [e.balance_sheet for e in self.entities if e.balance_sheet is not None]

    def all_statements(self) -> list[FinancialStatement]:
        """Return all non-None statements (DREs first, then balance sheets)."""
        return self.all_dres + self.all_balances

    def to_dict(self) -> dict:
        return {
            "entities": [e.to_dict() for e in self.entities],
            "dre_consolidated": self.dre_consolidated.to_dict() if self.dre_consolidated else None,
            "metrics": self.metrics.to_dict(),
            "capex_projection": self.capex_projection.to_dict(),
            "dividend_projection": self.dividend_projection.to_dict(),
        }


def _normalize(s: str) -> str:
    """Case-insensitive, accent-insensitive string compare key.

    Used for entity name matching so 'Lojas Próprias', 'lojas proprias',
    and 'LOJAS PRÓPRIAS' all resolve to the same entity.
    """
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", s or "")
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return stripped.strip().lower()
