"""
Chapter 1: Company information models.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from .evidence import Evidence, TrackedField


@dataclass
class CompanyProfile:
    legal_name: TrackedField = field(default_factory=TrackedField.empty)
    trade_name: TrackedField = field(default_factory=TrackedField.empty)
    description: TrackedField = field(default_factory=TrackedField.empty)
    founding_year: TrackedField = field(default_factory=TrackedField.empty)
    headquarters: TrackedField = field(default_factory=TrackedField.empty)
    sector: TrackedField = field(default_factory=TrackedField.empty)
    business_model: TrackedField = field(default_factory=TrackedField.empty)
    target_audience: TrackedField = field(default_factory=TrackedField.empty)
    number_of_stores: TrackedField = field(default_factory=TrackedField.empty)
    number_of_employees: TrackedField = field(default_factory=TrackedField.empty)

    def to_dict(self) -> dict:
        return {k: getattr(self, k).to_dict() for k in self.__dataclass_fields__}


@dataclass
class TimelineEvent:
    year: int = 0
    description: str = ""
    evidence: Evidence = field(default_factory=lambda: Evidence(source_file=""))

    def to_dict(self) -> dict:
        return {"year": self.year, "description": self.description, "evidence": self.evidence.to_dict()}


@dataclass
class Shareholder:
    name: str = ""
    role: str | None = None
    ownership_pct: float | None = None
    evidence: Evidence = field(default_factory=lambda: Evidence(source_file=""))

    def to_dict(self) -> dict:
        return {
            "name": self.name, "role": self.role,
            "ownership_pct": self.ownership_pct, "evidence": self.evidence.to_dict(),
        }


@dataclass
class Executive:
    name: str = ""
    role: str = ""
    tenure_years: int | None = None
    background: str | None = None
    ownership_pct: float | None = None
    evidence: Evidence = field(default_factory=lambda: Evidence(source_file=""))

    def to_dict(self) -> dict:
        return {
            "name": self.name, "role": self.role, "tenure_years": self.tenure_years,
            "background": self.background, "ownership_pct": self.ownership_pct,
            "evidence": self.evidence.to_dict(),
        }


@dataclass
class Product:
    name: str = ""
    category: str = ""
    description: str | None = None
    revenue_share_pct: float | None = None
    is_proprietary: bool = False
    evidence: Evidence = field(default_factory=lambda: Evidence(source_file=""))

    def to_dict(self) -> dict:
        return {
            "name": self.name, "category": self.category, "description": self.description,
            "revenue_share_pct": self.revenue_share_pct, "is_proprietary": self.is_proprietary,
            "evidence": self.evidence.to_dict(),
        }


@dataclass
class CompanyChapter:
    profile: CompanyProfile = field(default_factory=CompanyProfile)
    timeline: list[TimelineEvent] = field(default_factory=list)
    shareholders: list[Shareholder] = field(default_factory=list)
    executives: list[Executive] = field(default_factory=list)
    products: list[Product] = field(default_factory=list)
    brands: list[TrackedField] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "profile": self.profile.to_dict(),
            "timeline": [t.to_dict() for t in self.timeline],
            "shareholders": [s.to_dict() for s in self.shareholders],
            "executives": [e.to_dict() for e in self.executives],
            "products": [p.to_dict() for p in self.products],
            "brands": [b.to_dict() for b in self.brands],
        }
