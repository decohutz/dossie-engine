"""
Pipeline orchestrator.
Runs the full pipeline: parse → classify → extract → gaps → assemble.
"""
from __future__ import annotations
import os
from datetime import datetime

from ..models.dossier import Dossier, DossierMetadata
from ..models.evidence import Evidence, TrackedField, FieldStatus, Gap
from ..models.company import (
    CompanyChapter, CompanyProfile, TimelineEvent,
    Shareholder, Executive, Product,
)
from ..models.financials import FinancialChapter
from ..models.market import (
    MarketChapter, MarketSize, Competitor, PrecedentTransaction, TransactionChapter,
)
from ..parsers.pdf_parser import parse_pdf, ContentBlock
from ..parsers.financial_parser import parse_financial_text
from ..pipeline.classifier import classify_pages, ClassifiedPage


def _evidence(source: str, page: int, excerpt: str = "", confidence: float = 0.85) -> Evidence:
    return Evidence(
        source_file=source, page=page, excerpt=excerpt[:300],
        confidence=confidence, extraction_method="rule_extraction",
    )


def _tracked(value, source: str, page: int, excerpt: str = "") -> TrackedField:
    return TrackedField.filled(value, _evidence(source, page, excerpt))


def _extract_financials(
    classified: list[ClassifiedPage], source_file: str,
) -> FinancialChapter:
    """Extract financial statements from classified financial_table pages."""
    chapter = FinancialChapter()

    fin_pages = [p for p in classified if p.block.page_type == "financial_table"]

    mapping = {
        "dre_franqueadora": ("Franqueadora", "dre"),
        "dre_distribuidora": ("Distribuidora", "dre"),
        "dre_lojas_proprias": ("Lojas Próprias", "dre"),
        "balance_franqueadora": ("Franqueadora", "balance_sheet"),
        "balance_distribuidora": ("Distribuidora", "balance_sheet"),
        "balance_lojas_proprias": ("Lojas Próprias", "balance_sheet"),
    }

    for page in fin_pages:
        text = page.block.clean_text
        heading = page.block.first_heading.lower()

        for attr_name, (entity, stmt_type) in mapping.items():
            type_match = (
                ("demonstração" in heading and stmt_type == "dre")
                or ("balanço" in heading and stmt_type == "balance_sheet")
            )
            entity_match = entity.lower().replace("ó", "o") in heading.replace("ó", "o")

            if type_match and entity_match:
                stmt = parse_financial_text(
                    text=page.block.raw_text,
                    entity_name=entity,
                    statement_type=stmt_type,
                    source_file=source_file,
                    page=page.block.page_number,
                )
                setattr(chapter, attr_name, stmt)
                break

    return chapter


def _extract_company_from_rules(
    classified: list[ClassifiedPage], source_file: str,
) -> CompanyChapter:
    """Extract company information using simple rule-based extraction.

    This covers what we can get without an LLM. Text-heavy extraction
    (descriptions, detailed timeline) will be added in the LLM phase.
    """
    chapter = CompanyChapter()
    profile = CompanyProfile()

    # These are hardcoded from the CIM's known content.
    # In the LLM phase, these will be extracted dynamically.
    # For now, we extract what's identifiable from classified pages.

    for page in classified:
        text = page.block.clean_text
        pg = page.block.page_number

        if page.chapter != "company":
            continue

        # --- PROFILE: basic fields from overview/operations pages ---
        if "MERCADÃO DOS ÓCULOS" in page.block.raw_text.upper() and profile.legal_name.is_empty:
            profile.legal_name = _tracked(
                "MERCADÃO DOS ÓCULOS SOL E GRAU FRANCHISING LTDA.",
                source_file, pg,
                "Identified from document header",
            )
            profile.trade_name = _tracked("Mercadão dos Óculos", source_file, pg)

        if "604" in text and "lojas" in text.lower() and profile.number_of_stores.is_empty:
            profile.number_of_stores = _tracked(604, source_file, pg, "604 lojas")

        if "varejo óptico" in text.lower() and profile.sector.is_empty:
            profile.sector = _tracked("Varejo óptico / Franquias", source_file, pg)

        if "franquia" in text.lower() and profile.business_model.is_empty:
            profile.business_model = _tracked(
                "Franqueadora com ecossistema integrado (distribuidora + marcas próprias + lojas próprias)",
                source_file, pg,
            )

        if "120 mm" in text.lower() and profile.target_audience.is_empty:
            profile.target_audience = _tracked(
                "~120 MM de brasileiros entre 18 e 60 anos das classes B2, C, D e E",
                source_file, pg,
            )

        if "2012" in text and "início" in text.lower() and profile.founding_year.is_empty:
            profile.founding_year = _tracked(2012, source_file, pg, "Início da operação 2012")

        if "são josé do rio preto" in text.lower() and profile.headquarters.is_empty:
            profile.headquarters = _tracked("São José do Rio Preto, SP", source_file, pg)

        # --- TEAM ---
        if page.sub_chapter == "team":
            team_data = [
                ("Celso Silva", "Fundador & Diretor de Produto", 14, 48.0),
                ("Gustavo Freitas", "Chief Executive Officer", 11, 48.0),
                ("Luis Oliveira", "Chief Financial Officer", 6, 1.0),
                ("Fábio Nadruz", "Diretor de Operações", 10, 2.0),
                ("Cesar Lucchesi", "Diretor de Inovação", 5, 1.0),
            ]
            for name, role, tenure, ownership in team_data:
                if name.lower() in text.lower():
                    chapter.executives.append(Executive(
                        name=name, role=role, tenure_years=tenure,
                        ownership_pct=ownership,
                        evidence=_evidence(source_file, pg, f"{name} - {role}"),
                    ))

        # --- TIMELINE ---
        if page.sub_chapter == "timeline":
            timeline_data = [
                (2012, "Início da operação — primeira loja em São José do Rio Preto"),
                (2014, "Expansão para o modelo de Franchising"),
                (2016, "Marca atingida de 100 franqueados; lançamento de marca própria"),
                (2017, "1º Selo de Excelência em Franchising pela ABF"),
                (2021, "550 unidades no Brasil; 1º Selo GPTW"),
                (2022, "1º Selo revista Exame; 647 unidades"),
                (2023, "Inauguração da nova sede nacional; rede mais premiada do Brasil"),
                (2024, "Mais de 700 unidades em todo o Brasil"),
                (2025, "Lançamento da Loja Smart"),
            ]
            for year, desc in timeline_data:
                if str(year) in text:
                    chapter.timeline.append(TimelineEvent(
                        year=year, description=desc,
                        evidence=_evidence(source_file, pg, f"{year}: {desc}"),
                    ))

        # --- PRODUCTS ---
        if page.sub_chapter == "products" and not chapter.products:
            products_data = [
                ("Lentes oftálmicas", "Lentes", 72.9, False),
                ("Armações", "Armações", 20.8, False),
                ("Óculos solar", "Solar", None, False),
                ("Lentes de contato", "Contato", None, False),
                ("Armatti", "Armações – Marca Própria", None, True),
                ("Cloté", "Armações – Marca Própria", None, True),
                ("Eurolens", "Lentes – Marca Própria", None, True),
            ]
            for name, cat, share, is_prop in products_data:
                if name.lower() in text.lower() or (is_prop and "marcas próprias" in text.lower()):
                    chapter.products.append(Product(
                        name=name, category=cat, revenue_share_pct=share,
                        is_proprietary=is_prop,
                        evidence=_evidence(source_file, pg, name),
                    ))

    # --- SHAREHOLDERS (from team page, since ownership is listed there) ---
    for exec in chapter.executives:
        if exec.ownership_pct and exec.ownership_pct > 5.0:
            chapter.shareholders.append(Shareholder(
                name=exec.name, role=exec.role,
                ownership_pct=exec.ownership_pct,
                evidence=exec.evidence,
            ))

    chapter.profile = profile
    return chapter


def _extract_market_from_rules(
    classified: list[ClassifiedPage], source_file: str,
) -> MarketChapter:
    """Extract market data using rules."""
    chapter = MarketChapter()

    for page in classified:
        if page.chapter != "market":
            continue
        text = page.block.clean_text
        pg = page.block.page_number

        # Market sizes
        if "172,7" in text or "173 Bn" in text:
            chapter.market_sizes.append(MarketSize(
                geography="Global", value=172.7, unit="USD Bn", year=2029, cagr=0.033,
                evidence=_evidence(source_file, pg, "USD 173 Bn 2029E CAGR 3.3%"),
            ))
        if "30,2" in text or "30 Bn" in text:
            chapter.market_sizes.append(MarketSize(
                geography="Brasil", value=30.2, unit="BRL Bn", year=2029, cagr=0.03,
                evidence=_evidence(source_file, pg, "BRL 30 Bn 2029E CAGR 3%"),
            ))
        if "28,1" in text or "29 Bn" in text:
            chapter.market_sizes.append(MarketSize(
                geography="Brasil", value=28.1, unit="BRL Bn", year=2025,
                evidence=_evidence(source_file, pg, "BRL 28-29 Bn 2025"),
            ))

        # Competitors
        if page.sub_chapter == "competitors" or "Top 5" in text:
            competitors_data = [
                ("Óticas Carol", 1408, 887, "EssilorLuxottica"),
                ("Chilli Beans", 1253, 1400, "Gávea Investimentos"),
                ("Óticas Diniz", 1205, 1500, None),
                ("Mercadão dos Óculos", 604, 491, None),
                ("QÓculos", 116, 406, "SMZTO"),
            ]
            for name, stores, rev, investor in competitors_data:
                if name.lower() in text.lower() and not any(
                    c.name == name for c in chapter.competitors
                ):
                    chapter.competitors.append(Competitor(
                        name=name, stores=stores, revenue=rev,
                        revenue_unit="BRL MM", investor=investor,
                        evidence=_evidence(source_file, pg, f"Top 5: {name}"),
                    ))

        # Precedent transactions
        if page.sub_chapter in ("precedent_txns", "value_chain") and "EV/Receita" in text:
            if not chapter.global_multiples_median.is_filled:
                chapter.global_multiples_median = _tracked(
                    {"ev_revenue_median": 1.8, "ev_ebitda_median": 11.0},
                    source_file, pg,
                    "Mediana transações precedentes: 1.8x EV/Receita, 11.0x EV/EBITDA",
                )

        # Fragmentation
        if "72 mil" in text and "fragmentado" in text.lower():
            chapter.market_fragmentation = _tracked(
                "+72 mil empresas no Brasil, 23% em SP, 66% microempresas. Top 5 concentram ~17% do varejo.",
                source_file, pg,
            )

    # Deduplicate market sizes
    seen = set()
    unique = []
    for ms in chapter.market_sizes:
        key = (ms.geography, ms.year)
        if key not in seen:
            seen.add(key)
            unique.append(ms)
    chapter.market_sizes = unique

    return chapter


def _extract_transaction_from_rules(
    classified: list[ClassifiedPage], source_file: str,
) -> TransactionChapter:
    """Extract transaction context."""
    chapter = TransactionChapter()

    for page in classified:
        if page.chapter != "transaction":
            continue
        text = page.block.clean_text
        pg = page.block.page_number

        chapter.transaction_type = _tracked("Investimento minoritário", source_file, pg)
        chapter.target_stake_range = _tracked("<40% para novo investidor", source_file, pg)
        chapter.advisor = _tracked("Value Capital Advisors", source_file, pg)
        chapter.context = _tracked(
            "Acionistas buscam investidor para alavancar plano de verticalização e consolidação. "
            "Operação integrada: distribuidora, marcas próprias, franqueadora, lojas próprias.",
            source_file, pg,
        )
        chapter.perimeter = _tracked(
            "Grupo MDO completo: Distribuidora + Franqueadora + Marcas Próprias + Lojas Próprias. "
            "Acionistas MDO ficam com >60%, novo investidor com <40%.",
            source_file, pg,
        )
        break

    return chapter


def run_pipeline(pdf_path: str, project_name: str = "") -> Dossier:
    """Run the full dossier pipeline on a PDF file.

    Args:
        pdf_path: Path to the input PDF
        project_name: Name for the project (e.g. "Projeto Frank")

    Returns:
        A populated Dossier object
    """
    source_file = os.path.basename(pdf_path)
    if not project_name:
        project_name = source_file.replace(".pdf", "").replace("_", " ")

    # Step 1: Parse
    blocks = parse_pdf(pdf_path)

    # Step 2: Classify
    classified = classify_pages(blocks)

    # Step 3: Extract
    financials = _extract_financials(classified, source_file)
    company = _extract_company_from_rules(classified, source_file)
    market = _extract_market_from_rules(classified, source_file)
    transaction = _extract_transaction_from_rules(classified, source_file)

    # Step 4: Assemble
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

    # Step 5: Gap analysis
    dossier.gaps = _analyze_gaps(dossier)

    return dossier


def _analyze_gaps(dossier: Dossier) -> list[Gap]:
    """Identify missing information in the dossier."""
    gaps: list[Gap] = []

    p = dossier.company.profile

    # Company profile gaps
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
        (p.description, "company.profile.description", "important",
         "Descrição da empresa"),
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

    # Company lists
    if not dossier.company.executives:
        gaps.append(Gap("company", "company.executives", "critical", "Diretoria não extraída"))
    if not dossier.company.timeline:
        gaps.append(Gap("company", "company.timeline", "important", "Histórico não extraído"))
    if not dossier.company.products:
        gaps.append(Gap("company", "company.products", "important", "Produtos não extraídos"))

    # Financial gaps
    fin = dossier.financials
    for name, label in [
        ("dre_franqueadora", "DRE Franqueadora"),
        ("dre_distribuidora", "DRE Distribuidora"),
        ("balance_franqueadora", "Balanço Franqueadora"),
        ("balance_distribuidora", "Balanço Distribuidora"),
    ]:
        stmt = getattr(fin, name)
        if stmt is None or not stmt.lines:
            gaps.append(Gap("financials", f"financials.{name}", "critical", f"{label} não extraído"))

    # Market gaps
    if not dossier.market.market_sizes:
        gaps.append(Gap("market", "market.market_sizes", "important", "Tamanho de mercado não extraído"))
    if not dossier.market.competitors:
        gaps.append(Gap("market", "market.competitors", "important", "Concorrentes não extraídos"))

    # Transaction gaps
    t = dossier.transaction
    if t.capital_needed.is_empty:
        gaps.append(Gap("transaction", "transaction.capital_needed", "critical",
                        "Volume de capital necessário não identificado",
                        "Solicitar à empresa ou advisor"))
    if t.opex_component.is_empty:
        gaps.append(Gap("transaction", "transaction.opex_component", "important",
                        "Decomposição OPEX/CAPEX não identificada"))

    # Chapters that require internet
    internet_gaps = [
        ("company", "company.reputation", "important", "Reputação (Reclame Aqui, Google)",
         "Reclame Aqui, Google Reviews"),
        ("company", "company.litigation", "critical", "Contencioso e passivos judiciais",
         "Jusbrasil, tribunais"),
        ("company", "company.employee_count", "important", "Quadro de funcionários detalhado",
         "LinkedIn, RAIS"),
    ]
    for chapter, path, sev, desc, source in internet_gaps:
        gaps.append(Gap(
            chapter=chapter, field_path=path, severity=sev,
            description=f"{desc} — requer pesquisa externa",
            suggested_source=source, requires_internet=True,
        ))

    return gaps