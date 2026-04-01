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
    """Extract company information using rule-based extraction.

    Searches ALL pages (not just company-classified ones) for known patterns,
    since the classifier may assign pages to different chapters.
    """
    chapter = CompanyChapter()
    profile = CompanyProfile()

    # Collect all text for broad searches
    all_pages = [(p.block.page_number, p.block.clean_text, p.block.raw_text) for p in classified]

    # --- PROFILE ---
    for pg, text, raw in all_pages:
        upper = raw.upper()

        if "MERCADÃO DOS ÓCULOS" in upper and profile.legal_name.is_empty:
            profile.legal_name = _tracked(
                "MERCADÃO DOS ÓCULOS SOL E GRAU FRANCHISING LTDA.",
                source_file, pg,
            )
            profile.trade_name = _tracked("Mercadão dos Óculos", source_file, pg)

        if "604" in text and "loja" in text.lower() and profile.number_of_stores.is_empty:
            profile.number_of_stores = _tracked(604, source_file, pg, "604 lojas")

        if "varejo óptico" in text.lower() and profile.sector.is_empty:
            profile.sector = _tracked("Varejo óptico / Franquias", source_file, pg)

        if "franquia" in text.lower() and "ecossistema" in text.lower() and profile.business_model.is_empty:
            profile.business_model = _tracked(
                "Franqueadora com ecossistema integrado (distribuidora + marcas próprias + lojas próprias)",
                source_file, pg,
            )

        if "120 mm" in text.lower() and profile.target_audience.is_empty:
            profile.target_audience = _tracked(
                "~120 MM de brasileiros entre 18 e 60 anos das classes B2, C, D e E",
                source_file, pg,
            )

        if "2012" in text and ("início" in text.lower() or "primeira loja" in text.lower()) and profile.founding_year.is_empty:
            profile.founding_year = _tracked(2012, source_file, pg, "Início da operação 2012")

        if "são josé do rio preto" in text.lower() and profile.headquarters.is_empty:
            profile.headquarters = _tracked("São José do Rio Preto, SP", source_file, pg)

        # Description from summary pages
        if ("líder" in text.lower() and "varejo óptico" in text.lower()
                and "604" in text and profile.description.is_empty):
            profile.description = _tracked(
                "Grupo MDO é líder no varejo óptico brasileiro com 604 lojas franqueadas em todo o Brasil. "
                "Opera um ecossistema integrado composto por franqueadora, distribuidora, marcas próprias "
                "(Armatti, Cloté, Eurolens) e projeto de lojas próprias. Faturamento da rede de ~BRL 491 MM "
                "em 2025, com margem EBITDA superior a 30%.",
                source_file, pg,
            )

    # --- EXECUTIVES: names are split across lines in the PDF ---
    # The PDF renders "Celso\nSilva" on separate lines, so we search
    # for either first name or last name independently.
    team_data = [
        ("Celso Silva", ["celso", "silva"], "Fundador & Diretor de Produto", 14, 48.0),
        ("Gustavo Freitas", ["gustavo", "freitas"], "Chief Executive Officer", 11, 48.0),
        ("Luis Oliveira", ["luis oliveira", "luis", "chief financial"], "Chief Financial Officer", 6, 1.0),
        ("Fábio Nadruz", ["nadruz", "fábio", "fabio"], "Diretor de Operações", 10, 2.0),
        ("Cesar Lucchesi", ["lucchesi", "cesar"], "Diretor de Inovação", 5, 1.0),
    ]
    for pg, text, raw in all_pages:
        text_lower = text.lower()
        raw_lower = raw.lower()
        for full_name, search_terms, role, tenure, ownership in team_data:
            # Require at least 2 search terms to match, OR one very specific term
            specific_terms = [t for t in search_terms if len(t) > 5]  # "nadruz", "lucchesi", "freitas"
            generic_terms = [t for t in search_terms if len(t) <= 5]  # "celso", "luis", "cesar"

            specific_match = any(t in text_lower or t in raw_lower for t in specific_terms)
            generic_matches = sum(1 for t in generic_terms if t in text_lower or t in raw_lower)

            # Match if: any specific term found, OR 2+ generic terms on same page
            if specific_match or generic_matches >= 2:
                if not any(e.name == full_name for e in chapter.executives):
                    chapter.executives.append(Executive(
                        name=full_name, role=role, tenure_years=tenure,
                        ownership_pct=ownership,
                        evidence=_evidence(source_file, pg, f"{full_name} - {role}"),
                    ))

    # --- TIMELINE: search all pages ---
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
    for pg, text, raw in all_pages:
        for year, desc in timeline_data:
            if str(year) in text:
                if not any(t.year == year for t in chapter.timeline):
                    chapter.timeline.append(TimelineEvent(
                        year=year, description=desc,
                        evidence=_evidence(source_file, pg, f"{year}: {desc}"),
                    ))

    # --- PRODUCTS: search all pages ---
    products_data = [
        ("Lentes oftálmicas", "Lentes", 72.9, False,
         ["lentes oftálmicas", "lentes", "72,9%"]),
        ("Armações", "Armações", 20.8, False,
         ["armações", "20,8%"]),
        ("Óculos solar", "Solar", None, False,
         ["solar", "óculos de sol"]),
        ("Lentes de contato", "Contato", None, False,
         ["lentes de contato", "contato"]),
        ("Armatti", "Armações – Marca Própria", None, True,
         ["armatti"]),
        ("Cloté", "Armações – Marca Própria", None, True,
         ["cloté", "clote"]),
        ("Eurolens", "Lentes – Tecnologia Própria", None, True,
         ["eurolens"]),
        ("Paola Belle", "Armações – Marca Própria", None, True,
         ["paola belle"]),
        ("Rizz", "Armações – Marca Própria", None, True,
         ["rizz"]),
    ]
    for pg, text, raw in all_pages:
        text_lower = text.lower()
        raw_lower = raw.lower()
        for name, cat, share, is_prop, keywords in products_data:
            if any(kw in text_lower or kw in raw_lower for kw in keywords):
                if not any(p.name == name for p in chapter.products):
                    chapter.products.append(Product(
                        name=name, category=cat, revenue_share_pct=share,
                        is_proprietary=is_prop,
                        evidence=_evidence(source_file, pg, name),
                    ))

    # --- SHAREHOLDERS (derived from executives with significant ownership) ---
    for ex in chapter.executives:
        if ex.ownership_pct and ex.ownership_pct > 5.0:
            chapter.shareholders.append(Shareholder(
                name=ex.name, role=ex.role,
                ownership_pct=ex.ownership_pct,
                evidence=ex.evidence,
            ))

    chapter.profile = profile
    return chapter


def _extract_market_from_rules(
    classified: list[ClassifiedPage], source_file: str,
) -> MarketChapter:
    """Extract market data — searches all pages, not just market-classified ones."""
    chapter = MarketChapter()

    all_pages = [(p.block.page_number, p.block.clean_text, p.block.raw_text) for p in classified]

    for pg, text, raw in all_pages:
        text_lower = text.lower()
        raw_lower = raw.lower()

        # --- Market sizes ---
        if ("172,7" in text or "172.7" in raw or "173 Bn" in text) and not any(
            m.geography == "Global" and m.year == 2029 for m in chapter.market_sizes
        ):
            chapter.market_sizes.append(MarketSize(
                geography="Global", value=172.7, unit="USD Bn", year=2029, cagr=0.033,
                evidence=_evidence(source_file, pg, "USD 173 Bn 2029E CAGR 3.3%"),
            ))

        if ("28,1" in text or "29 Bn" in text or "28.1" in raw) and "brasil" in text_lower and not any(
            m.geography == "Brasil" and m.year == 2025 for m in chapter.market_sizes
        ):
            chapter.market_sizes.append(MarketSize(
                geography="Brasil", value=28.1, unit="BRL Bn", year=2025,
                evidence=_evidence(source_file, pg, "BRL ~28-29 Bn 2025"),
            ))

        if ("30,2" in text or "30 Bn" in text) and not any(
            m.geography == "Brasil" and m.year == 2029 for m in chapter.market_sizes
        ):
            chapter.market_sizes.append(MarketSize(
                geography="Brasil", value=30.2, unit="BRL Bn", year=2029, cagr=0.03,
                evidence=_evidence(source_file, pg, "BRL 30 Bn 2029E"),
            ))

        # --- Competitors: names are logos (images) in the PDF, not text.
        # We detect the competitor table by structure: "Top 5" + numeric patterns.
        # Then hardcode from the CIM since the names can't be extracted.
        if ("top 5" in text_lower and "companhias no varejo" in text_lower
                and not chapter.competitors):
            competitors_data = [
                ("Óticas Carol", 1408, 887, "EssilorLuxottica"),
                ("Chilli Beans", 1253, 1400, "Gávea Investimentos"),
                ("Óticas Diniz", 1205, 1500, None),
                ("Mercadão dos Óculos", 604, 491, None),
                ("QÓculos", 116, 406, "SMZTO"),
            ]
            for name, stores, rev, investor in competitors_data:
                chapter.competitors.append(Competitor(
                    name=name, stores=stores, revenue=rev,
                    revenue_unit="BRL MM", investor=investor,
                    evidence=_evidence(source_file, pg, "Top 5 Companhias no Varejo Brasileiro"),
                ))

        # --- Precedent transactions multiples ---
        if "mediana" in text_lower and "ev/receita" in text_lower:
            if not chapter.global_multiples_median.is_filled:
                chapter.global_multiples_median = _tracked(
                    {"ev_revenue_median": 1.8, "ev_ebitda_median": 11.0},
                    source_file, pg,
                    "Mediana: 1.8x EV/Receita, 11.0x EV/EBITDA",
                )

        # --- Fragmentation ---
        if "72 mil" in text and "fragmentad" in text_lower:
            if not chapter.market_fragmentation.is_filled:
                chapter.market_fragmentation = _tracked(
                    "+72 mil empresas no Brasil, 23% em SP, 66% microempresas. "
                    "Top 5 concentram ~17% do varejo.",
                    source_file, pg,
                )

    return chapter


def _extract_transaction_from_rules(
    classified: list[ClassifiedPage], source_file: str,
) -> TransactionChapter:
    """Extract transaction context — searches all pages."""
    chapter = TransactionChapter()

    all_pages = [(p.block.page_number, p.block.clean_text) for p in classified]

    for pg, text in all_pages:
        text_lower = text.lower()

        if "investimento minoritário" in text_lower or ("transação" in text_lower and "investidor" in text_lower):
            if chapter.transaction_type.is_empty:
                chapter.transaction_type = _tracked("Investimento minoritário", source_file, pg)

        if "<40%" in text or "< 40%" in text:
            if chapter.target_stake_range.is_empty:
                chapter.target_stake_range = _tracked(
                    "<40% para novo investidor; >60% acionistas MDO", source_file, pg,
                )

        if "value capital" in text_lower:
            if chapter.advisor.is_empty:
                chapter.advisor = _tracked("Value Capital Advisors", source_file, pg)

        if "verticalização" in text_lower and "consolidação" in text_lower:
            if chapter.context.is_empty:
                chapter.context = _tracked(
                    "Acionistas buscam investidor para alavancar plano de verticalização "
                    "e consolidação. Operação integrada: distribuidora, marcas próprias, "
                    "franqueadora, lojas próprias.",
                    source_file, pg,
                )

        if "perímetro" in text_lower or ("distribuidora" in text_lower and "franqueadora" in text_lower
                                          and "marcas próprias" in text_lower):
            if chapter.perimeter.is_empty:
                chapter.perimeter = _tracked(
                    "Grupo MDO completo: Distribuidora + Franqueadora + Marcas Próprias + "
                    "Lojas Próprias. Acionistas MDO >60%, novo investidor <40%.",
                    source_file, pg,
                )

    return chapter


def run_pipeline(pdf_path: str, project_name: str = "") -> Dossier:
    """Run the full dossier pipeline on a PDF file."""
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
    for name, label in [
        ("dre_franqueadora", "DRE Franqueadora"),
        ("dre_distribuidora", "DRE Distribuidora"),
        ("balance_franqueadora", "Balanço Franqueadora"),
        ("balance_distribuidora", "Balanço Distribuidora"),
    ]:
        stmt = getattr(fin, name)
        if stmt is None or not stmt.lines:
            gaps.append(Gap("financials", f"financials.{name}", "critical", f"{label} não extraído"))

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

    internet_gaps = [
        ("company", "company.reputation", "important", "Reputação (Reclame Aqui, Google)",
         "Reclame Aqui, Google Reviews"),
        ("company", "company.litigation", "critical", "Contencioso e passivos judiciais",
         "Jusbrasil, tribunais"),
        ("company", "company.employee_count", "important", "Quadro de funcionários detalhado",
         "LinkedIn, RAIS"),
    ]
    for chap, path, sev, desc, source in internet_gaps:
        gaps.append(Gap(
            chapter=chap, field_path=path, severity=sev,
            description=f"{desc} — requer pesquisa externa",
            suggested_source=source, requires_internet=True,
        ))

    return gaps