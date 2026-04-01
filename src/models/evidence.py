"""
Core evidence and tracking models.
Every piece of data in the dossier is traceable back to its source.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar, Literal
from datetime import datetime

T = TypeVar("T")


@dataclass
class Evidence:
    """Tracks where a piece of information came from."""
    source_file: str                     # "Projeto_Frank_CIM.pdf"
    page: int | None = None              # 47
    excerpt: str = ""                    # Relevant text excerpt (max ~500 chars)
    confidence: float = 1.0              # 0.0 to 1.0
    extraction_method: str = "text_parse" # "text_parse" | "llm_extraction" | "table_parse" | "manual"
    extracted_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return {
            "source_file": self.source_file,
            "page": self.page,
            "excerpt": self.excerpt[:500],
            "confidence": self.confidence,
            "extraction_method": self.extraction_method,
            "extracted_at": self.extracted_at,
        }


@dataclass
class FieldStatus:
    """Status of a field in the dossier."""
    status: str = "empty"  # "filled" | "partial" | "empty" | "conflicting"
    evidences: list[Evidence] = field(default_factory=list)
    notes: str | None = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "evidences": [e.to_dict() for e in self.evidences],
            "notes": self.notes,
        }


@dataclass
class TrackedField:
    """A field value with full traceability metadata.
    
    Usage:
        name = TrackedField(value="Mercadão dos Óculos", field_status=FieldStatus(
            status="filled",
            evidences=[Evidence(source_file="CIM.pdf", page=2)]
        ))
    """
    value: Any = None
    field_status: FieldStatus = field(default_factory=FieldStatus)

    @property
    def is_filled(self) -> bool:
        return self.field_status.status == "filled" and self.value is not None

    @property
    def is_empty(self) -> bool:
        return self.field_status.status == "empty" or self.value is None

    def to_dict(self) -> dict:
        val = self.value
        if hasattr(val, "to_dict"):
            val = val.to_dict()
        return {
            "value": val,
            "field_status": self.field_status.to_dict(),
        }

    @staticmethod
    def filled(value: Any, evidence: Evidence) -> TrackedField:
        """Convenience factory for a filled field with evidence."""
        return TrackedField(
            value=value,
            field_status=FieldStatus(
                status="filled",
                evidences=[evidence],
            ),
        )

    @staticmethod
    def empty(notes: str | None = None) -> TrackedField:
        """Convenience factory for an empty field."""
        return TrackedField(
            value=None,
            field_status=FieldStatus(status="empty", notes=notes),
        )


@dataclass
class Gap:
    """A detected gap (missing information) in the dossier."""
    chapter: str                          # "company", "financials", "market", "transaction"
    field_path: str                       # "company.profile.number_of_employees"
    severity: str = "important"           # "critical" | "important" | "nice_to_have"
    description: str = ""                 # "Número de funcionários não encontrado"
    suggested_source: str | None = None   # "LinkedIn, RAIS"
    requires_internet: bool = False       # True = can't fill without web search

    def to_dict(self) -> dict:
        return {
            "chapter": self.chapter,
            "field_path": self.field_path,
            "severity": self.severity,
            "description": self.description,
            "suggested_source": self.suggested_source,
            "requires_internet": self.requires_internet,
        }
