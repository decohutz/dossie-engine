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
    pdf_path: str,
    project_name: str = "",
    use_llm: bool = True,
    enrich: bool = False,
    verbose: bool = False,
) -> Dossier:
    """Run the full dossier pipeline on a PDF file.

    Args:
        pdf_path: Path to the input PDF
        project_name: Name for the project
        use_llm: If True, use Ollama LLM for extraction. If False, fall back to rules.
        enrich: If True, search the web to fill gaps marked with requires_internet.
        verbose: If True, print progress messages.
    """
    source_file = os.path.basename(pdf_path)
    if not project_name:
        project_name = source_file.replace(".pdf", "").replace("_", " ")

    # Step 1: Parse
    if verbose:
        print("Step 1: Parsing PDF...")
    blocks = parse_pdf(pdf_path)

    # Step 2: Classify
    if verbose:
        print(f"Step 2: Classifying {len(blocks)} pages...")
    classified = classify_pages(blocks)

    # Step 3: Extract financials (always uses text parser)
    if verbose:
        print("Step 3: Extracting financials...")
    financials = _extract_financials(classified, source_file)

    # Step 4: Extract other chapters
    if use_llm:
        if verbose:
            print("Step 4: Extracting with LLM (Ollama)...")
        company, market, transaction = _extract_with_llm(classified, source_file, verbose, pdf_path=pdf_path)
    else:
        if verbose:
            print("Step 4: Extracting with rules (fallback)...")
        company, market, transaction = _extract_with_rules(classified, source_file)

    # Step 5: Assemble
    dossier = Dossier(
        metadata=DossierMetadata(
            project_name=project_name,
            target_company=company.profile.trade_name.value or "Unknown",
            source_files=[source_file],
            version="v001",
        ),
        company=company,
        financials=financials,
        market=market,
        transaction=transaction,
    )

    # Step 5b: Gap analysis
    if verbose:
        print("Step 5: Analyzing gaps...")
    dossier.gaps = _analyze_gaps(dossier)

    # Step 6: Web enrichment (optional)
    if enrich:
        if verbose:
            print("Step 6: Web enrichment...")
        from ..enrichment.enricher import enrich_dossier
        dossier = enrich_dossier(dossier, use_llm=use_llm, verbose=verbose)

    return dossier


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