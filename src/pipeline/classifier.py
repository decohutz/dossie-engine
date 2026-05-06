"""
Page classifier.
Maps each ContentBlock to one or more dossier chapters.

Phase 1: Rule-based classification using keywords.
Phase 2 (future): LLM-assisted classification for ambiguous pages.
"""
from __future__ import annotations
import re
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
#
# These rules are intentionally **sector-agnostic by default**. The original
# version of this list was tuned for an optical-retail CIM (Frank) and used
# brand names like "Óticas Carol", "EssilorLuxottica" as keywords — that
# meant any deck not in the optical retail space had nothing to match
# against, and most of its pages fell through to "unknown".
#
# Each rule below tries to capture concepts that are universal across CIMs
# and investor decks: "linha do tempo" / "track record" for timelines,
# "sócios" / "fundador" / "CEO" for team, "produtos" / "soluções" for
# product catalogs, etc. Frank-specific phrases (Óticas Carol, Value
# Capital) are kept as additional keywords on the relevant rules so the
# Frank baseline doesn't regress, but they're no longer load-bearing.
_RULES: list[tuple[str, str, list[str]]] = [
    # --- FINANCIAL ---
    # Per-entity DREs and balance sheets are discovered dynamically by the
    # orchestrator from each page's heading; these generic rules just give
    # those pages a "financials" classification for downstream consumers.
    ("financials", "dre",                  ["Demonstração de Resultados", "DRE"]),
    ("financials", "balance_sheet",        ["Balanço Patrimonial"]),
    ("financials", "revenue_breakdown",    ["Receita Bruta", "Receita Líquida", "EBITDA", "Lucro", "Faturamento", "Margem"]),
    ("financials", "structure",            ["informações financeiras", "veículos societários", "CNPJs", "indicadores financeiros"]),
    ("financials", "projections",          ["CAPEX", "Dividendos", "asset-light", "desalavancagem", "projeções", "investimento estimado", "payback", "TIR"]),

    # --- MARKET ---
    ("market", "global_market",       ["Mercado Global", "USD Bn", "CAGR", "mercado mundial", "mercado óptico"]),
    ("market", "local_market",        ["mercado brasileiro", "BRL Bn", "varejo óptico brasileiro", "tamanho de mercado", "mercado nacional"]),
    ("market", "demographics",        ["envelhecimento", "miopia", "população", "OMS", "consumidores", "comportamento do consumidor", "público brasileiro", "mulheres brasileiras"]),
    ("market", "competitors",         ["principais concorrentes", "Top 5", "players", "concorrência", "competidores",
                                        # Frank-specific bonus
                                        "Óticas Carol", "Chilli Beans", "Óticas Diniz"]),
    ("market", "value_chain",         ["cadeia de valor", "verticalizados", "fabricação", "distribuição", "varejo"]),
    ("market", "precedent_txns",      ["Transações Precedentes", "EV/Receita", "EV/EBITDA", "transações comparáveis", "múltiplos"]),
    ("market", "global_players",      ["players globais",
                                        # Frank-specific bonus
                                        "EssilorLuxottica", "Warby Parker", "Fielmann", "National Vision"]),
    ("market", "barriers",            ["barreiras", "regulatórias", "logísticas"]),
    ("market", "trends",              ["tendência", "tendências", "consumo consciente", "clean beauty", "sustentabilidade", "ESG"]),

    # --- COMPANY ---
    ("company", "overview",           ["Por que investir", "ecossistema", "visão", "missão", "propósito", "diferencial"]),
    ("company", "timeline",           ["linha do tempo", "track record", "história", "história da empresa", "marcos", "trajetória",
                                        "expansão e consolidação", "referência na criação"]),
    ("company", "team",               ["equipe", "sócios", "Chief Executive", "Chief Financial", "Diretor", "Diretora",
                                        "fundador", "fundadora", "CEO", "CFO", "COO", "founder", "co-founder",
                                        "depoimentos"]),
    ("company", "operations",         ["operações", "rede operacional", "pontos de venda", "unidades", "lojas", "salões", "filiais", "matriz", "sede",
                                        "Operações da Companhia"]),
    ("company", "products",           ["produtos", "soluções", "marcas próprias", "linha de produtos", "portfólio",
                                        # Frank-specific bonus
                                        "sell-out", "lentes oftálmicas", "armações"]),
    ("company", "distribution",       ["distribuidora", "abastecimento", "markup", "Centro de Distribuição"]),
    ("company", "franchise",          ["Franquias", "franqueados", "franchising", "payback", "multifranqueados", "modelo de franquia"]),
    ("company", "expansion",          ["plano de crescimento", "plano de expansão", "lojas próprias", "novos mercados",
                                        "855 unidades"]),
    ("company", "target_audience",    ["público alvo", "público-alvo", "classes sociais", "renda disponível", "perfil do cliente"]),
    ("company", "esg",                ["carbono neutro", "carbon free", "sustentável", "reflorestamento", "embalagens", "neutralização", "ESG"]),

    # --- TRANSACTION ---
    ("transaction", "overview",       ["Transação envolve", "investimento minoritário", "investimento majoritário", "perímetro", "estrutura da operação"]),
    ("transaction", "deal_context",   ["acionistas", "investidor", "verticalização", "consolidação", "captação", "rodada"]),

    # --- META ---
    ("meta", "disclaimer",           ["Disclaimer", "confidencialidade", "Confidential"]),
    ("meta", "contacts",             ["Value Capital Advisors", "daniel.lasse", "Informações Gerais", "@", "Obrigado!"]),
    ("meta", "agenda",               ["Agenda", "Sumário", "Página"]),
]


def classify_pages(blocks: list[ContentBlock]) -> list[ClassifiedPage]:
    """Classify each page into a dossier chapter using keyword rules."""
    results: list[ClassifiedPage] = []

    # The last page (whatever its page_number is) gets meta/closing.
    # We use max(page_number) instead of len(blocks) because callers
    # sometimes hand us a non-contiguous slice of pages (e.g. a synthetic
    # test, or a debug subset of a larger deck).
    last_page_number = max((b.page_number for b in blocks), default=0)
    first_page_number = min((b.page_number for b in blocks), default=0)
    # Special case: a single-block input has first==last. We don't want to
    # silently classify that single page as cover/closing, so disable the
    # boundary heuristics entirely when there's only one page.
    use_boundary_meta = last_page_number != first_page_number

    for block in blocks:
        if block.page_type in ("separator", "title"):
            results.append(ClassifiedPage(
                block=block, chapter="skip", sub_chapter="section_divider",
                confidence=1.0,
            ))
            continue

        if use_boundary_meta and block.page_number == first_page_number:
            results.append(ClassifiedPage(
                block=block, chapter="meta", sub_chapter="cover",
                confidence=1.0,
            ))
            continue

        if use_boundary_meta and block.page_number == last_page_number:
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

        # Structural fallback: if no keyword rule matched, try to infer
        # the chapter from page-level signals. These rules deliberately
        # don't use sector-specific words — they trigger on shape, not
        # vocabulary, so they generalize across decks. Each fallback
        # gets confidence=0.3 (clearly weaker than keyword matches).
        if best_score == 0:
            inferred = _structural_fallback(block)
            if inferred is not None:
                best_chapter, best_sub = inferred
                best_score = 1   # represent as a weak match for display purposes
                confidence_override = 0.3
            else:
                confidence_override = None
        else:
            confidence_override = None

        if confidence_override is not None:
            confidence = confidence_override
        else:
            confidence = min(best_score / 3.0, 1.0) if best_score > 0 else 0.0

        results.append(ClassifiedPage(
            block=block,
            chapter=best_chapter if best_score > 0 else "unknown",
            sub_chapter=best_sub,
            confidence=confidence,
            method="rules" if confidence_override is None else "structural",
        ))

    return results


# Sector-agnostic structural patterns. Each tries to identify what KIND of
# slide we're looking at by shape, not by what brand/industry it's selling.
#
# Brazilian state codes appear in two forms in the wild: the normal "SP",
# "RJ", and a pdfplumber-mangled form "S P" / "R J" / "M G" that appears
# when the source PDF uses heavily-tracked decorative fonts. The regex
# below accepts both. We anchor on whitespace boundaries (not \b) because
# the spaced form would otherwise break word-boundary matching.
_BR_STATE_RE = re.compile(
    r"(?:(?<=\s)|(?<=^)|(?<=-))"
    r"(?:S[\s]*P|R[\s]*J|M[\s]*G|R[\s]*S|P[\s]*R|S[\s]*C|"
    r"B[\s]*A|P[\s]*E|C[\s]*E|G[\s]*O|D[\s]*F|E[\s]*S|"
    r"A[\s]*M|P[\s]*A|M[\s]*T|M[\s]*S|R[\s]*N|P[\s]*B|"
    r"P[\s]*I|M[\s]*A|T[\s]*O|A[\s]*L|S[\s]*E|R[\s]*O|"
    r"A[\s]*C|A[\s]*P|R[\s]*R|B[\s]*H)"
    r"(?=\s|$|[).,])",
    re.IGNORECASE,
)
_LOCATION_HINT_RE = re.compile(
    # "Morumbi - SP" or "Jardins - S P"
    r"[-–—]\s*(?:S\s*P|R\s*J|M\s*G|R\s*S|P\s*R|S\s*C|B\s*A|P\s*E|"
    r"C\s*E|G\s*O|D\s*F|E\s*S|A\s*M|P\s*A|M\s*T|M\s*S|R\s*N|P\s*B|"
    r"P\s*I|M\s*A|T\s*O|A\s*L|S\s*E|R\s*O|A\s*C|A\s*P|R\s*R|B\s*H)"
    r"(?=\s|$|[).,])",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-3]\d)\b")
_PERSON_NAME_RE = re.compile(r"\b[A-ZÀ-Ý][a-zà-ÿ]+\s+[A-ZÀ-Ý][a-zà-ÿ]+")
_PRODUCT_HINT_RE = re.compile(
    r"\b(?:spray|mousse|kit|linha|coleção|gel|creme|máscara|óleo|sérum|"
    r"capsule|pack|edition|vegano|natural|orgânico|coloração|tonalizante|"
    r"shampoo|condicionador|esmalte|perfume|fragrância|tratamento)\b",
    re.IGNORECASE,
)


def _structural_fallback(block: ContentBlock) -> tuple[str, str] | None:
    """Infer (chapter, sub_chapter) from non-vocabulary signals.

    Returns ``None`` when no structural pattern matches (page stays unknown).

    The patterns:

    * **Location-tagged short pages** ("Morumbi - SP", "BH", "Barra"):
      operations / store-list pages, mapped to ``company.operations``.
    * **Year-rich pages** with multiple years and short prose:
      ``company.timeline``. Captures pages whose only text is a sequence
      of dates and event labels even when no "linha do tempo" header
      survived OCR.
    * **Pages with prominent person names** in ALL-CAPS or Title Case
      and short text: ``company.team``. Captures founder-photo slides.
    * **Pages whose vocabulary suggests a product taxonomy** without
      naming concrete brands: ``company.products``. Note that the hints
      here are deliberately generic (spray, kit, vegano) — not "lentes"
      or "frames" which would over-fit to a specific industry.

    These heuristics are intentionally conservative: they only fire when
    the keyword classifier produced no match, and they always tag the
    result with confidence 0.3 so downstream code can prefer keyword-
    classified pages when both are available.
    """
    text = block.clean_text or ""
    if not text.strip():
        return None

    # Heuristic 1: city/state location tag ⇒ operations
    if _LOCATION_HINT_RE.search(text) or (
        _BR_STATE_RE.search(text) and len(text) < 200
    ):
        return ("company", "operations")

    # Heuristic 2: multiple distinct years ⇒ timeline
    years = set(_YEAR_RE.findall(text))
    if len(years) >= 3:
        return ("company", "timeline")

    # Heuristic 3: product vocabulary ⇒ products
    if _PRODUCT_HINT_RE.search(text):
        return ("company", "products")

    # Heuristic 4: short page with 2+ person names ⇒ team
    person_matches = _PERSON_NAME_RE.findall(text)
    if len(person_matches) >= 2 and len(text) < 600:
        return ("company", "team")

    # No structural pattern matched.
    return None


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