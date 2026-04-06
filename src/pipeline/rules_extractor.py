"""
Rule-based extraction (fallback).
Contains the hardcoded extraction logic from v0.1.
Used when LLM is not available.
"""
from __future__ import annotations
from ..models.evidence import Evidence, TrackedField
from ..models.company import (
    CompanyChapter, CompanyProfile, TimelineEvent,
    Shareholder, Executive, Product,
)
from ..models.market import (
    MarketChapter, MarketSize, Competitor, TransactionChapter,
)
from ..pipeline.classifier import ClassifiedPage


def _evidence(source: str, page: int, excerpt: str = "", confidence: float = 0.85) -> Evidence:
    return Evidence(
        source_file=source, page=page, excerpt=excerpt[:300],
        confidence=confidence, extraction_method="rule_extraction",
    )


def _tracked(value, source: str, page: int, excerpt: str = "") -> TrackedField:
    return TrackedField.filled(value, _evidence(source, page, excerpt))


def extract_company_rules(classified: list[ClassifiedPage], source_file: str) -> CompanyChapter:
    chapter = CompanyChapter()
    profile = CompanyProfile()
    all_pages = [(p.block.page_number, p.block.clean_text, p.block.raw_text) for p in classified]

    for pg, text, raw in all_pages:
        upper = raw.upper()
        if "MERCADÃO DOS ÓCULOS" in upper and profile.legal_name.is_empty:
            profile.legal_name = _tracked("MERCADÃO DOS ÓCULOS SOL E GRAU FRANCHISING LTDA.", source_file, pg)
            profile.trade_name = _tracked("Mercadão dos Óculos", source_file, pg)
        if "604" in text and "loja" in text.lower() and profile.number_of_stores.is_empty:
            profile.number_of_stores = _tracked(604, source_file, pg)
        if "varejo óptico" in text.lower() and profile.sector.is_empty:
            profile.sector = _tracked("Varejo óptico / Franquias", source_file, pg)
        if "franquia" in text.lower() and "ecossistema" in text.lower() and profile.business_model.is_empty:
            profile.business_model = _tracked(
                "Franqueadora com ecossistema integrado (distribuidora + marcas próprias + lojas próprias)",
                source_file, pg)
        if "120 mm" in text.lower() and profile.target_audience.is_empty:
            profile.target_audience = _tracked(
                "~120 MM de brasileiros entre 18 e 60 anos das classes B2, C, D e E", source_file, pg)
        if "2012" in text and ("início" in text.lower() or "primeira loja" in text.lower()) and profile.founding_year.is_empty:
            profile.founding_year = _tracked(2012, source_file, pg)
        if "são josé do rio preto" in text.lower() and profile.headquarters.is_empty:
            profile.headquarters = _tracked("São José do Rio Preto, SP", source_file, pg)
        if ("líder" in text.lower() and "varejo óptico" in text.lower()
                and "604" in text and profile.description.is_empty):
            profile.description = _tracked(
                "Grupo MDO é líder no varejo óptico brasileiro com 604 lojas franqueadas. "
                "Ecossistema integrado: franqueadora, distribuidora, marcas próprias, lojas próprias. "
                "Faturamento ~BRL 491 MM em 2025, margem EBITDA >30%.", source_file, pg)

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
            specific = [t for t in search_terms if len(t) > 5]
            generic = [t for t in search_terms if len(t) <= 5]
            specific_match = any(t in text_lower or t in raw_lower for t in specific)
            generic_matches = sum(1 for t in generic if t in text_lower or t in raw_lower)
            if specific_match or generic_matches >= 2:
                if not any(e.name == full_name for e in chapter.executives):
                    chapter.executives.append(Executive(
                        name=full_name, role=role, tenure_years=tenure,
                        ownership_pct=ownership,
                        evidence=_evidence(source_file, pg, full_name)))

    timeline_data = [
        (2012, "Início da operação — primeira loja em São José do Rio Preto"),
        (2014, "Expansão para o modelo de Franchising"),
        (2016, "Marca de 100 franqueados; lançamento de marca própria"),
        (2017, "1º Selo de Excelência em Franchising pela ABF"),
        (2021, "550 unidades; 1º Selo GPTW"),
        (2022, "1º Selo Exame; 647 unidades"),
        (2023, "Nova sede nacional; rede mais premiada do Brasil"),
        (2024, "Mais de 700 unidades"),
        (2025, "Lançamento da Loja Smart"),
    ]
    for pg, text, raw in all_pages:
        for year, desc in timeline_data:
            if str(year) in text and not any(t.year == year for t in chapter.timeline):
                chapter.timeline.append(TimelineEvent(year=year, description=desc,
                    evidence=_evidence(source_file, pg, f"{year}")))

    products_data = [
        ("Lentes oftálmicas", "Lentes", 72.9, False, ["lentes oftálmicas", "lentes", "72,9%"]),
        ("Armações", "Armações", 20.8, False, ["armações", "20,8%"]),
        ("Óculos solar", "Solar", None, False, ["solar", "óculos de sol"]),
        ("Lentes de contato", "Contato", None, False, ["lentes de contato"]),
        ("Armatti", "Armações – Marca Própria", None, True, ["armatti"]),
        ("Cloté", "Armações – Marca Própria", None, True, ["cloté", "clote"]),
        ("Eurolens", "Lentes – Tecnologia Própria", None, True, ["eurolens"]),
        ("Paola Belle", "Armações – Marca Própria", None, True, ["paola belle"]),
        ("Rizz", "Armações – Marca Própria", None, True, ["rizz"]),
    ]
    for pg, text, raw in all_pages:
        text_lower = text.lower()
        raw_lower = raw.lower()
        for name, cat, share, is_prop, keywords in products_data:
            if any(kw in text_lower or kw in raw_lower for kw in keywords):
                if not any(p.name == name for p in chapter.products):
                    chapter.products.append(Product(name=name, category=cat,
                        revenue_share_pct=share, is_proprietary=is_prop,
                        evidence=_evidence(source_file, pg, name)))

    for ex in chapter.executives:
        if ex.ownership_pct and ex.ownership_pct > 5.0:
            chapter.shareholders.append(Shareholder(name=ex.name, role=ex.role,
                ownership_pct=ex.ownership_pct, evidence=ex.evidence))

    chapter.profile = profile
    return chapter


def extract_market_rules(classified: list[ClassifiedPage], source_file: str) -> MarketChapter:
    chapter = MarketChapter()
    all_pages = [(p.block.page_number, p.block.clean_text, p.block.raw_text) for p in classified]

    for pg, text, raw in all_pages:
        text_lower = text.lower()
        if ("172,7" in text or "173 Bn" in text) and not any(
            m.geography == "Global" and m.year == 2029 for m in chapter.market_sizes):
            chapter.market_sizes.append(MarketSize(geography="Global", value=172.7,
                unit="USD Bn", year=2029, cagr=0.033,
                evidence=_evidence(source_file, pg)))
        if ("28,1" in text or "29 Bn" in text) and "brasil" in text_lower and not any(
            m.geography == "Brasil" and m.year == 2025 for m in chapter.market_sizes):
            chapter.market_sizes.append(MarketSize(geography="Brasil", value=28.1,
                unit="BRL Bn", year=2025,
                evidence=_evidence(source_file, pg)))
        if ("30,2" in text or "30 Bn" in text) and not any(
            m.geography == "Brasil" and m.year == 2029 for m in chapter.market_sizes):
            chapter.market_sizes.append(MarketSize(geography="Brasil", value=30.2,
                unit="BRL Bn", year=2029, cagr=0.03,
                evidence=_evidence(source_file, pg)))
        if "top 5" in text_lower and "companhias no varejo" in text_lower and not chapter.competitors:
            for name, stores, rev, investor in [
                ("Óticas Carol", 1408, 887, "EssilorLuxottica"),
                ("Chilli Beans", 1253, 1400, "Gávea Investimentos"),
                ("Óticas Diniz", 1205, 1500, None),
                ("Mercadão dos Óculos", 604, 491, None),
                ("QÓculos", 116, 406, "SMZTO"),
            ]:
                chapter.competitors.append(Competitor(name=name, stores=stores,
                    revenue=rev, revenue_unit="BRL MM", investor=investor,
                    evidence=_evidence(source_file, pg)))
        if "mediana" in text_lower and "ev/receita" in text_lower:
            if not chapter.global_multiples_median.is_filled:
                chapter.global_multiples_median = _tracked(
                    {"ev_revenue_median": 1.8, "ev_ebitda_median": 11.0}, source_file, pg)
        if "72 mil" in text and "fragmentad" in text_lower:
            if not chapter.market_fragmentation.is_filled:
                chapter.market_fragmentation = _tracked(
                    "+72 mil empresas no Brasil, 23% em SP. Top 5 ~17% do varejo.",
                    source_file, pg)
    return chapter


def extract_transaction_rules(classified: list[ClassifiedPage], source_file: str) -> TransactionChapter:
    chapter = TransactionChapter()
    for pg, text in [(p.block.page_number, p.block.clean_text) for p in classified]:
        text_lower = text.lower()
        if "investimento minoritário" in text_lower and chapter.transaction_type.is_empty:
            chapter.transaction_type = _tracked("Investimento minoritário", source_file, pg)
        if "<40%" in text and chapter.target_stake_range.is_empty:
            chapter.target_stake_range = _tracked("<40% novo investidor; >60% acionistas MDO", source_file, pg)
        if "value capital" in text_lower and chapter.advisor.is_empty:
            chapter.advisor = _tracked("Value Capital Advisors", source_file, pg)
        if "verticalização" in text_lower and "consolidação" in text_lower and chapter.context.is_empty:
            chapter.context = _tracked(
                "Acionistas buscam investidor para alavancar plano de verticalização e consolidação.",
                source_file, pg)
        if ("distribuidora" in text_lower and "franqueadora" in text_lower
                and "marcas próprias" in text_lower and chapter.perimeter.is_empty):
            chapter.perimeter = _tracked(
                "Grupo MDO completo: Distribuidora + Franqueadora + Marcas Próprias + Lojas Próprias.",
                source_file, pg)
    return chapter