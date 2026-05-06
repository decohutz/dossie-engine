"""
Pipeline orchestrator.
Runs the full pipeline: parse → classify → extract (LLM or rules) → gaps → enrich → assemble.
"""
from __future__ import annotations
import os
import re
from datetime import datetime

from ..models.dossier import Dossier, DossierMetadata
from ..models.evidence import Evidence, TrackedField, FieldStatus, Gap
from ..models.company import CompanyChapter, CompanyProfile
from ..models.financials import FinancialChapter
from ..models.market import MarketChapter, TransactionChapter
from ..parsers.pdf_parser import parse_pdf
from ..parsers.financial_parser import parse_financial_text
from ..pipeline.classifier import classify_pages, ClassifiedPage


def _evidence(source: str, page: int, excerpt: str = "", confidence: float = 0.85) -> Evidence:
    return Evidence(
        source_file=source, page=page, excerpt=excerpt[:300],
        confidence=confidence, extraction_method="rule_extraction",
    )


_DRE_HEADING_RE = re.compile(
    r"demonstra[cç][aã]o\s+de\s+resultados\s*[–\-—:]\s*(.+)",
    re.IGNORECASE,
)
_BALANCE_HEADING_RE = re.compile(
    r"balan[cç]o\s+patrimonial\s*[–\-—:]\s*(.+)",
    re.IGNORECASE,
)


def _parse_financial_heading(heading: str) -> tuple[str, str] | None:
    """Parse a financial-table heading into (statement_type, entity_name).

    Recognizes patterns like:
      "Demonstração de Resultados – Franqueadora" → ("dre", "Franqueadora")
      "Balanço Patrimonial – Distribuidora"       → ("balance_sheet", "Distribuidora")

    Returns None if the heading doesn't look like a per-entity financial table.
    """
    if not heading:
        return None
    h = heading.strip()
    m = _DRE_HEADING_RE.search(h)
    if m:
        stmt_type = "dre"
    else:
        m = _BALANCE_HEADING_RE.search(h)
        if not m:
            return None
        stmt_type = "balance_sheet"

    entity = m.group(1).strip()
    # Trim trailing parentheticals / footnotes / notes
    entity = re.sub(r"\s*[\(\[].*$", "", entity).strip()
    # Strip trailing punctuation
    entity = entity.rstrip(" .,;:")
    if not entity:
        return None
    return stmt_type, entity


def _extract_financials(
    classified: list[ClassifiedPage], source_file: str,
) -> FinancialChapter:
    """Extract financial statements from financial_table pages.

    Entities are discovered dynamically from each page's heading — e.g. a
    heading "Demonstração de Resultados – Distribuidora" creates an entity
    named "Distribuidora" with a DRE attached. This handles CIMs with any
    number of entities (1, 3, 5, ...) without hardcoding.
    """
    chapter = FinancialChapter()
    fin_pages = [p for p in classified if p.block.page_type == "financial_table"]

    for page in fin_pages:
        parsed = _parse_financial_heading(page.block.first_heading)
        if parsed is None:
            continue
        stmt_type, entity_name = parsed
        stmt = parse_financial_text(
            text=page.block.raw_text, entity_name=entity_name,
            statement_type=stmt_type, source_file=source_file,
            page=page.block.page_number,
        )
        if stmt_type == "dre":
            chapter.upsert_dre(entity_name, stmt)
        else:
            chapter.upsert_balance(entity_name, stmt)

    return chapter


def run_pipeline(
    pdf_path: str | None = None,
    project_name: str = "",
    use_llm: bool = True,
    enrich: bool = False,
    verbose: bool = False,
    *,
    inputs: list[str] | None = None,
) -> Dossier:
    """Run the full dossier pipeline on one or more input files.

    Two calling conventions are supported:

    1. **Multi-input (preferred, E2+)** — pass ``inputs=[file1, file2, ...]``.
       Files are routed by extension: ``.pdf`` goes through the existing
       PDF pipeline (parse → classify → extract LLM/rules → financial
       text-parser); ``.xlsx`` / ``.xls`` goes through
       ``parse_xlsx_financials``. The pipeline runs the PDF path first
       (so soft chapters — profile, executives, market, transaction —
       come from the PDF), then merges in any XLSX-derived financials.
       Per the agreed merge policy, **XLSX always wins on financial
       data**: any DRE / balance sheet / consolidated DRE produced by
       the XLSX parser overwrites the corresponding entry from the PDF
       parser.

    2. **Legacy (single PDF)** — pass ``pdf_path=path``. This is the
       interface used by the existing tests and ``cli.py`` before E2.
       It is kept as a thin shim that internally builds
       ``inputs=[pdf_path]``.

    Parameters
    ----------
    pdf_path : str, optional
        Legacy single-PDF entry point. Mutually exclusive with ``inputs``.
    inputs : list[str], optional
        New multi-input entry point. Order does not matter for routing
        (files are dispatched by extension), but it does decide where
        each file's name appears in ``metadata.source_files``.
    project_name, use_llm, enrich, verbose
        Same semantics as before.
    """
    # ── Resolve inputs ────────────────────────────────────────────────
    if inputs is None and pdf_path is None:
        raise ValueError("run_pipeline requires either `inputs` or `pdf_path`")
    if inputs is not None and pdf_path is not None:
        raise ValueError("pass either `inputs` or `pdf_path`, not both")
    if inputs is None:
        inputs = [pdf_path]                     # legacy shim

    pdf_inputs = [p for p in inputs if _is_pdf_path(p)]
    xlsx_inputs = [p for p in inputs if _is_xlsx_path(p)]
    unknown = [p for p in inputs if not _is_pdf_path(p) and not _is_xlsx_path(p)]
    if unknown:
        raise ValueError(
            f"unrecognized input file extension(s): {unknown}. "
            f"Supported: .pdf, .xlsx, .xls"
        )
    if len(pdf_inputs) > 1:
        # Could be supported later, but for now keep it simple — Frank/Regenera
        # both have at most one PDF.
        raise ValueError(
            f"multiple PDF inputs not supported yet ({len(pdf_inputs)} given). "
            f"Pass exactly one PDF and any number of XLSX files."
        )

    primary_pdf = pdf_inputs[0] if pdf_inputs else None
    source_files = [os.path.basename(p) for p in inputs]

    if not project_name:
        if primary_pdf is not None:
            project_name = os.path.basename(primary_pdf).replace(".pdf", "").replace("_", " ")
        elif xlsx_inputs:
            project_name = (
                os.path.basename(xlsx_inputs[0])
                .rsplit(".", 1)[0]
                .replace("_", " ")
            )

    # ── PDF-driven path (soft chapters + PDF-financials, if any) ──────
    if primary_pdf is not None:
        pdf_basename = os.path.basename(primary_pdf)
        if verbose:
            print(f"Step 1: Parsing PDF ({pdf_basename})...")
        blocks = parse_pdf(primary_pdf)

        if verbose:
            print(f"Step 2: Classifying {len(blocks)} pages...")
        classified = classify_pages(blocks)

        if verbose:
            print("Step 3: Extracting financials from PDF...")
        financials = _extract_financials(classified, pdf_basename)

        if use_llm:
            if verbose:
                print("Step 4: Extracting with LLM (Ollama)...")
            company, market, transaction = _extract_with_llm(
                classified, pdf_basename, verbose, pdf_path=primary_pdf,
            )
        else:
            if verbose:
                print("Step 4: Extracting with rules (fallback)...")
            company, market, transaction = _extract_with_rules(classified, pdf_basename)
    else:
        # No PDF given — soft chapters stay empty, will surface as gaps.
        if verbose:
            print("Step 1-4: No PDF input; soft chapters left empty (will surface as gaps).")
        financials = FinancialChapter()
        company = CompanyChapter()
        market = MarketChapter()
        transaction = TransactionChapter()

    # ── XLSX merge (financial data only) ──────────────────────────────
    if xlsx_inputs:
        if verbose:
            print(f"Step 3b: Merging financial data from {len(xlsx_inputs)} XLSX file(s)...")
        for xlsx_path in xlsx_inputs:
            financials = _merge_xlsx_financials(
                base=financials, xlsx_path=xlsx_path, verbose=verbose,
            )

    # ── Assemble ─────────────────────────────────────────────────────
    dossier = Dossier(
        metadata=DossierMetadata(
            project_name=project_name,
            target_company=company.profile.trade_name.value or "Unknown",
            source_files=source_files,
            version="v001",
        ),
        company=company,
        financials=financials,
        market=market,
        transaction=transaction,
    )

    # Gap analysis — runs the same regardless of how data got in
    if verbose:
        print("Step 5: Analyzing gaps...")
    dossier.gaps = _analyze_gaps(dossier)

    if enrich:
        if verbose:
            print("Step 6: Web enrichment...")
        from ..enrichment.enricher import enrich_dossier
        dossier = enrich_dossier(dossier, use_llm=use_llm, verbose=verbose)

    return dossier


# ── Multi-input helpers ──────────────────────────────────────────────────
def _is_pdf_path(p: str) -> bool:
    return p.lower().endswith(".pdf")


def _is_xlsx_path(p: str) -> bool:
    return p.lower().endswith((".xlsx", ".xls"))


def _merge_xlsx_financials(
    *,
    base: FinancialChapter,
    xlsx_path: str,
    verbose: bool = False,
) -> FinancialChapter:
    """Merge an XLSX-parsed FinancialChapter into an existing one.

    Merge policy (decided in the E2 design discussion):
        * **XLSX wins on financial data.** Any entity DRE/BS produced by
          the XLSX parser replaces the corresponding entry in `base`.
          Same for `dre_consolidated`.
        * Entities present only in the PDF chapter are kept.
        * Entities present only in the XLSX chapter are added.
        * The `non_operating` flag from the XLSX side is honored.
        * Derived fields on `base` (metrics, capex_projection,
          dividend_projection) are preserved — those are populated
          by post-processing that reads from the merged statements,
          and re-running it after merge happens elsewhere in the
          pipeline if needed.

    The merge is non-destructive on `base`: a new FinancialChapter
    is built and returned. This keeps `_extract_financials`'s output
    intact in case a later step wants to inspect the pre-merge state.
    """
    from ..parsers.xlsx_financial_parser import parse_xlsx_financials

    result = parse_xlsx_financials(xlsx_path)
    if verbose:
        for issue in result.issues:
            print(f"  xlsx parse: {issue}")

    xlsx_chapter = result.chapter

    # Start from a shallow copy of `base`. We rebuild `entities` because we
    # need to merge by name, not by position.
    merged = FinancialChapter(
        entities=list(base.entities),                  # may be replaced below
        dre_consolidated=base.dre_consolidated,
        metrics=base.metrics,
        capex_projection=base.capex_projection,
        dividend_projection=base.dividend_projection,
    )

    # Index existing entities by normalized name for O(1) overwrite lookup.
    # We use the chapter's own `get_entity` (accent/case-insensitive) so
    # "Lojas Próprias" from PDF and "Lojas Proprias" from XLSX collide.
    for xlsx_entity in xlsx_chapter.entities:
        existing = merged.get_entity(xlsx_entity.name)
        if existing is None:
            merged.entities.append(xlsx_entity)
        else:
            # XLSX wins: replace DRE and BS if XLSX has them.
            if xlsx_entity.dre is not None:
                existing.dre = xlsx_entity.dre
            if xlsx_entity.balance_sheet is not None:
                existing.balance_sheet = xlsx_entity.balance_sheet
            # Promote non_operating flag from XLSX (XLSX is the
            # structured source — if it says non-op, trust it).
            if xlsx_entity.non_operating:
                existing.non_operating = True

    # Consolidated DRE: XLSX wins outright.
    if xlsx_chapter.dre_consolidated is not None:
        merged.dre_consolidated = xlsx_chapter.dre_consolidated

    return merged



def _extract_with_llm(
    classified: list[ClassifiedPage], source_file: str, verbose: bool = False,
    pdf_path: str = "",
) -> tuple[CompanyChapter, MarketChapter, TransactionChapter]:
    """Extract using LLM (Ollama)."""
    from ..llm.client import OllamaClient
    from ..pipeline.llm_extractor import (
        extract_company_llm, extract_market_llm, extract_transaction_llm,
    )

    client = OllamaClient()

    if not client.is_available():
        print("⚠️  Ollama não está disponível. Usando extração por regras como fallback.")
        return _extract_with_rules(classified, source_file)

    if verbose:
        print(f"  Conectado ao Ollama: {client.model}")

    company = extract_company_llm(client, classified, source_file, verbose, pdf_path=pdf_path)
    market = extract_market_llm(client, classified, source_file, verbose, pdf_path=pdf_path)
    transaction = extract_transaction_llm(client, classified, source_file, verbose) 

    return company, market, transaction


def _extract_with_rules(
    classified: list[ClassifiedPage], source_file: str,
) -> tuple[CompanyChapter, MarketChapter, TransactionChapter]:
    """Fallback: extract using hardcoded rules (only works well with Projeto Frank)."""
    from ..pipeline.rules_extractor import (
        extract_company_rules, extract_market_rules, extract_transaction_rules,
    )
    company = extract_company_rules(classified, source_file)
    market = extract_market_rules(classified, source_file)
    transaction = extract_transaction_rules(classified, source_file)
    return company, market, transaction


def _analyze_gaps(dossier: Dossier) -> list[Gap]:
    """Identify missing information in the dossier."""
    gaps: list[Gap] = []
    p = dossier.company.profile

    field_checks = [
        (p.legal_name, "company.profile.legal_name", "critical", "Razão social"),
        (p.trade_name, "company.profile.trade_name", "critical", "Nome fantasia"),
        (p.founding_year, "company.profile.founding_year", "important", "Ano de fundação"),
        (p.headquarters, "company.profile.headquarters", "important", "Sede"),
        (p.sector, "company.profile.sector", "important", "Setor de atuação"),
        (p.business_model, "company.profile.business_model", "important", "Modelo de negócio"),
        (p.number_of_stores, "company.profile.number_of_stores", "important", "Número de lojas"),
        (p.number_of_employees, "company.profile.number_of_employees", "important",
         "Número de funcionários", "RAIS, LinkedIn", True),
        (p.description, "company.profile.description", "important", "Descrição da empresa"),
    ]

    for item in field_checks:
        tf, path, sev, desc = item[0], item[1], item[2], item[3]
        source = item[4] if len(item) > 4 else None
        needs_web = item[5] if len(item) > 5 else False
        if tf.is_empty:
            gaps.append(Gap(
                chapter="company", field_path=path, severity=sev,
                description=f"{desc} não encontrado",
                suggested_source=source, requires_internet=needs_web,
            ))

    if not dossier.company.executives:
        gaps.append(Gap("company", "company.executives", "critical", "Diretoria não extraída"))
    if not dossier.company.timeline:
        gaps.append(Gap("company", "company.timeline", "important", "Histórico não extraído"))
    if not dossier.company.products:
        gaps.append(Gap("company", "company.products", "important", "Produtos não extraídos"))

    fin = dossier.financials
    # Report a gap for every entity that's missing a DRE or balance sheet.
    # For the Frank CIM (Franqueadora/Distribuidora/Lojas Próprias) both DRE
    # and balance sheet are expected for each entity. For other CIMs, whatever
    # entities were discovered get checked individually.
    for ent in fin.entities:
        if ent.dre is None or not ent.dre.lines:
            gaps.append(Gap("financials", f"financials.entities[{ent.name}].dre",
                            "critical", f"DRE {ent.name} não extraído"))
        if ent.balance_sheet is None or not ent.balance_sheet.lines:
            gaps.append(Gap("financials", f"financials.entities[{ent.name}].balance_sheet",
                            "critical", f"Balanço {ent.name} não extraído"))

    if not dossier.market.market_sizes:
        gaps.append(Gap("market", "market.market_sizes", "important", "Tamanho de mercado não extraído"))
    if not dossier.market.competitors:
        gaps.append(Gap("market", "market.competitors", "important", "Concorrentes não extraídos"))

    t = dossier.transaction
    if t.capital_needed.is_empty:
        gaps.append(Gap("transaction", "transaction.capital_needed", "critical",
                        "Volume de capital necessário não identificado",
                        "Solicitar à empresa ou advisor"))
    if t.opex_component.is_empty:
        gaps.append(Gap("transaction", "transaction.opex_component", "important",
                        "Decomposição OPEX/CAPEX não identificada"))

    # Internet-dependent gaps
    internet_gaps = [
        ("company", "company.reputation", "important", "Reputação (Reclame Aqui, Google)",
         "Reclame Aqui, Google Reviews"),
        ("company", "company.litigation", "critical", "Contencioso e passivos judiciais",
         "Jusbrasil, tribunais"),
        ("company", "company.employee_count", "important", "Quadro de funcionários detalhado",
         "LinkedIn, RAIS"),
    ]

    for chap, path, sev, desc, source in internet_gaps:
        # Only add gap if the field is still empty
        should_add = True
        if path == "company.reputation" and hasattr(p, 'reputation') and p.reputation.is_filled:
            should_add = False
        elif path == "company.litigation" and hasattr(p, 'litigation') and p.litigation.is_filled:
            should_add = False
        elif path == "company.employee_count" and p.number_of_employees.is_filled:
            should_add = False

        if should_add:
            gaps.append(Gap(
                chapter=chap, field_path=path, severity=sev,
                description=f"{desc} — requer pesquisa externa",
                suggested_source=source, requires_internet=True,
            ))

    return gaps