"""
Page classifier.
Maps each ContentBlock to one or more dossier chapters.

Phase 1: Rule-based classification using keywords.
Phase 2 (future): LLM-assisted classification for ambiguous pages.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from ..parsers.pdf_parser import ContentBlock


@dataclass
class ClassifiedPage:
    """A page with its classification result."""
    block: ContentBlock
    chapter: str              # primary chapter: "company" | "financials" | "market" | "transaction" | "meta" | "skip"
    sub_chapter: str = ""     # e.g. "dre_franqueadora", "competitors", "timeline"
    confidence: float = 1.0   # 0.0 to 1.0
    method: str = "rules"     # "rules" | "llm"


# Keyword rules: (chapter, sub_chapter, keywords_in_text)
_RULES: list[tuple[str, str, list[str]]] = [
    # --- FINANCIAL ---
    # Per-entity DREs and balance sheets are discovered dynamically by the
    # orchestrator from each page's heading; these generic rules just give
    # those pages a "financials" classification for downstream consumers.
    ("financials", "dre",                  ["Demonstração de Resultados"]),
    ("financials", "balance_sheet",        ["Balanço Patrimonial"]),
    ("financials", "revenue_breakdown",    ["Receita Bruta", "Receita Líquida", "EBITDA", "Lucro"]),
    ("financials", "structure",            ["informações financeiras", "veículos societários", "CNPJs"]),
    ("financials", "projections",          ["CAPEX", "Dividendos", "asset-light", "desalavancagem"]),

    # --- MARKET ---
    ("market", "global_market",       ["mercado óptico", "Mercado Global", "USD Bn", "CAGR"]),
    ("market", "local_market",        ["mercado brasileiro", "BRL Bn", "varejo óptico brasileiro"]),
    ("market", "demographics",        ["envelhecimento", "miopia", "população", "OMS"]),
    ("market", "competitors",         ["Top 5", "Óticas Carol", "Chilli Beans", "Óticas Diniz", "QÓculos"]),
    ("market", "value_chain",         ["cadeia de valor", "verticalizados", "fabricação", "distribuição", "varejo"]),
    ("market", "precedent_txns",      ["Transações Precedentes", "EV/Receita", "EV/EBITDA"]),
    ("market", "global_players",      ["EssilorLuxottica", "Warby Parker", "Fielmann", "National Vision"]),
    ("market", "barriers",            ["barreiras", "regulatórias", "logísticas"]),
    ("market", "local_transactions",  ["investidores estrangeiros", "crescimento inorgânico", "LentesPlus"]),

    # --- COMPANY ---
    ("company", "overview",           ["Por que investir", "ecossistema integrado", "liderança do Grupo"]),
    ("company", "timeline",           ["track record", "referência na criação", "expansão e consolidação"]),
    ("company", "team",               ["equipe de sócios", "Chief Executive", "Chief Financial", "Diretor"]),
    ("company", "operations",         ["Operações da Companhia", "rede operacional", "pontos de venda"]),
    ("company", "products",           ["sell-out", "lentes oftálmicas", "armações", "marcas próprias"]),
    ("company", "distribution",       ["distribuidora", "abastecimento", "markup", "Centro de Distribuição"]),
    ("company", "franchise",          ["Franquias", "franqueados", "payback", "multifranqueados"]),
    ("company", "expansion",          ["plano de crescimento", "855 unidades", "lojas próprias"]),
    ("company", "target_audience",    ["público alvo", "classes sociais", "renda disponível"]),

    # --- TRANSACTION ---
    ("transaction", "overview",       ["Transação envolve", "investimento minoritário", "perímetro"]),
    ("transaction", "deal_context",   ["acionistas", "investidor", "verticalização", "consolidação"]),

    # --- META ---
    ("meta", "disclaimer",           ["Disclaimer", "confidencialidade"]),
    ("meta", "contacts",             ["Value Capital Advisors", "daniel.lasse", "Informações Gerais"]),
    ("meta", "agenda",               ["Agenda", "Conteúdo", "Página"]),
]


def classify_pages(blocks: list[ContentBlock]) -> list[ClassifiedPage]:
    """Classify each page into a dossier chapter using keyword rules."""
    results: list[ClassifiedPage] = []

    for block in blocks:
        if block.page_type in ("separator", "title"):
            results.append(ClassifiedPage(
                block=block, chapter="skip", sub_chapter="section_divider",
                confidence=1.0,
            ))
            continue

        if block.page_number == 1:
            results.append(ClassifiedPage(
                block=block, chapter="meta", sub_chapter="cover",
                confidence=1.0,
            ))
            continue

        if block.page_number == len(blocks):
            results.append(ClassifiedPage(
                block=block, chapter="meta", sub_chapter="closing",
                confidence=1.0,
            ))
            continue

        # Score each rule
        best_chapter = "unknown"
        best_sub = ""
        best_score = 0

        text = block.clean_text.lower()

        for chapter, sub, keywords in _RULES:
            score = sum(1 for kw in keywords if kw.lower() in text)
            if score > best_score:
                best_score = score
                best_chapter = chapter
                best_sub = sub

        confidence = min(best_score / 3.0, 1.0) if best_score > 0 else 0.0

        results.append(ClassifiedPage(
            block=block,
            chapter=best_chapter if best_score > 0 else "unknown",
            sub_chapter=best_sub,
            confidence=confidence,
            method="rules",
        ))

    return results


def print_classification_summary(pages: list[ClassifiedPage]) -> None:
    """Print classification results."""
    chapter_counts: dict[str, int] = {}
    for p in pages:
        chapter_counts[p.chapter] = chapter_counts.get(p.chapter, 0) + 1

    print(f"\n{'=' * 70}")
    print(f"  CLASSIFICATION SUMMARY")
    print(f"  {len(pages)} pages classified")
    print(f"  Chapters: {chapter_counts}")
    print(f"{'=' * 70}")

    icons = {
        "company": "🏢", "financials": "💰", "market": "📈",
        "transaction": "🤝", "meta": "ℹ️ ", "skip": "➖", "unknown": "❓",
    }

    for p in pages:
        icon = icons.get(p.chapter, "❓")
        conf = f"{p.confidence:.0%}" if p.confidence < 1.0 else "100%"
        sub = f"/{p.sub_chapter}" if p.sub_chapter else ""
        heading = p.block.first_heading[:50] if p.block.first_heading else ""
        print(f"  {icon} Pág {p.block.page_number:2d} {p.chapter:12s}{sub:30s} [{conf:>4s}] {heading}")