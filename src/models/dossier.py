"""
Root Dossier model — the central contract of the system.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from .evidence import Gap
from .company import CompanyChapter
from .financials import FinancialChapter
from .market import MarketChapter, TransactionChapter


@dataclass
class DossierMetadata:
    project_name: str = ""                  # "Projeto Frank"
    target_company: str = ""                # "Mercadão dos Óculos"
    source_files: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    version: str = "v001"
    pipeline_version: str = "0.1.0"

    def to_dict(self) -> dict:
        return {
            "project_name": self.project_name, "target_company": self.target_company,
            "source_files": self.source_files, "created_at": self.created_at,
            "updated_at": self.updated_at, "version": self.version,
            "pipeline_version": self.pipeline_version,
        }


@dataclass
class Dossier:
    """The complete dossier — everything the system knows about the target."""
    metadata: DossierMetadata = field(default_factory=DossierMetadata)
    company: CompanyChapter = field(default_factory=CompanyChapter)
    financials: FinancialChapter = field(default_factory=FinancialChapter)
    market: MarketChapter = field(default_factory=MarketChapter)
    transaction: TransactionChapter = field(default_factory=TransactionChapter)
    gaps: list[Gap] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "metadata": self.metadata.to_dict(),
            "company": self.company.to_dict(),
            "financials": self.financials.to_dict(),
            "market": self.market.to_dict(),
            "transaction": self.transaction.to_dict(),
            "gaps": [g.to_dict() for g in self.gaps],
        }

    def summary(self) -> dict:
        """Quick stats about dossier completeness."""
        stmts = self.financials.all_statements()
        return {
            "project": self.metadata.project_name,
            "company": self.metadata.target_company,
            "version": self.metadata.version,
            "timeline_events": len(self.company.timeline),
            "shareholders": len(self.company.shareholders),
            "executives": len(self.company.executives),
            "products": len(self.company.products),
            "financial_statements": len(stmts),
            "market_sizes": len(self.market.market_sizes),
            "competitors": len(self.market.competitors),
            "precedent_transactions": len(self.market.precedent_transactions),
            "gaps_total": len(self.gaps),
            "gaps_critical": len([g for g in self.gaps if g.severity == "critical"]),
            "gaps_important": len([g for g in self.gaps if g.severity == "important"]),
        }
