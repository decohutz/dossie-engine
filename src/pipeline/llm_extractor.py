"""
LLM-based data extraction.
Replaces hardcoded rules with LLM calls for generic extraction from any CIM.
"""
from __future__ import annotations
from ..models.evidence import Evidence, TrackedField, FieldStatus
from ..models.company import (
    CompanyChapter, CompanyProfile, TimelineEvent,
    Shareholder, Executive, Product,
)
from ..models.market import (
    MarketChapter, MarketSize, Competitor, PrecedentTransaction, TransactionChapter,
)
from ..llm.client import OllamaClient
from ..llm import prompts
from ..pipeline.classifier import ClassifiedPage


def _safe_str(val) -> str:
    if val is None:
        return ""
    return str(val).strip()


def _evidence(source: str, page: int, excerpt: str = "", confidence: float = 0.75) -> Evidence:
    return Evidence(
        source_file=source, page=page, excerpt=excerpt[:300],
        confidence=confidence, extraction_method="llm_extraction",
    )


def _tracked(value, source: str, page: int, excerpt: str = "") -> TrackedField:
    return TrackedField.filled(value, _evidence(source, page, excerpt))


def _get_pages_for_chapter(classified: list[ClassifiedPage], chapter: str) -> list[ClassifiedPage]:
    return [p for p in classified if p.chapter == chapter]


def _get_all_content_pages(classified: list[ClassifiedPage]) -> list[ClassifiedPage]:
    return [p for p in classified if p.chapter not in ("skip", "meta")]


def _combine_texts(pages: list[ClassifiedPage], max_chars: int = 8000) -> list[tuple[str, list[int]]]:
    chunks = []
    current_text = ""
    current_pages = []

    for page in pages:
        text = page.block.clean_text
        if len(current_text) + len(text) > max_chars and current_text:
            chunks.append((current_text, current_pages))
            current_text = ""
            current_pages = []
        current_text += f"\n--- Página {page.block.page_number} ---\n{text}\n"
        current_pages.append(page.block.page_number)

    if current_text:
        chunks.append((current_text, current_pages))

    return chunks


def _is_duplicate_product(name: str, existing: list[Product]) -> bool:
    """Check if a product name is a duplicate of an existing one.

    Catches cases like:
    - Exact match (case-insensitive)
    - Prefix match: "Armações" vs "Armações próprias" vs "Armações da distribuição"
    - Substring match: "Lentes" vs "Lentes oftálmicas"
    """
    name_lower = name.lower().strip()
    for p in existing:
        existing_lower = p.name.lower().strip()
        # Exact match
        if name_lower == existing_lower:
            return True
        # One is a prefix of the other
        if name_lower.startswith(existing_lower) or existing_lower.startswith(name_lower):
            return True
    return False


def extract_company_llm(
    client: OllamaClient,
    classified: list[ClassifiedPage],
    source_file: str,
    verbose: bool = False,
) -> CompanyChapter:
    """Extract company data using LLM."""
    chapter = CompanyChapter()
    profile = CompanyProfile()

    content_pages = _get_all_content_pages(classified)
    company_pages = _get_pages_for_chapter(classified, "company")

    # --- PROFILE: first pass with overview pages ---
    overview_pages = content_pages[:12]
    chunks = _combine_texts(overview_pages)

    if verbose:
        print(f"  [LLM] Extracting company profile from {len(overview_pages)} pages...")

    for text, page_nums in chunks:
        system, prompt = prompts.prompt_company_profile(text)
        data = client.extract_json(prompt, system)

        if data and isinstance(data, dict):
            pg = page_nums[0] if page_nums else 0

            for field_name in ["legal_name", "trade_name", "description", "sector",
                               "business_model", "target_audience", "headquarters"]:
                val = data.get(field_name)
                if val and getattr(profile, field_name).is_empty:
                    setattr(profile, field_name, _tracked(val, source_file, pg, field_name))

            for field_name in ["founding_year", "number_of_stores", "number_of_employees"]:
                val = data.get(field_name)
                if val is not None and getattr(profile, field_name).is_empty:
                    setattr(profile, field_name, _tracked(val, source_file, pg, field_name))
            break

    # --- PROFILE: second pass if key fields still empty ---
    empty_fields = []
    for f in ["legal_name", "headquarters", "number_of_employees"]:
        if getattr(profile, f).is_empty:
            empty_fields.append(f)

    if empty_fields and len(company_pages) > 12:
        if verbose:
            print(f"  [LLM] Second pass for profile fields: {empty_fields}")
        remaining = company_pages[12:]
        chunks2 = _combine_texts(remaining)
        for text, page_nums in chunks2:
            system, prompt = prompts.prompt_company_profile(text)
            data = client.extract_json(prompt, system)
            if data and isinstance(data, dict):
                pg = page_nums[0] if page_nums else 0
                for field_name in empty_fields:
                    val = data.get(field_name)
                    if val and getattr(profile, field_name).is_empty:
                        setattr(profile, field_name, _tracked(val, source_file, pg, field_name))

    # --- EXECUTIVES ---
    if verbose:
        print(f"  [LLM] Extracting executives...")

    exec_pages = [p for p in classified if p.sub_chapter == "team"] or company_pages
    for page in exec_pages:
        system, prompt = prompts.prompt_executives(page.block.clean_text)
        data = client.extract_json(prompt, system)

        if data and isinstance(data, dict):
            for ex in (data.get("executives") or []):
                if not isinstance(ex, dict):
                    continue
                name = _safe_str(ex.get("name"))
                if name and not any(e.name == name for e in chapter.executives):
                    # Build role with entity if available
                    role = _safe_str(ex.get("role"))
                    entity = _safe_str(ex.get("entity"))
                    if entity and entity.lower() not in role.lower():
                        role = f"{role} ({entity})"

                    chapter.executives.append(Executive(
                        name=name,
                        role=role,
                        tenure_years=ex.get("tenure_years"),
                        ownership_pct=ex.get("ownership_pct"),
                        background=_safe_str(ex.get("background")) or None,
                        evidence=_evidence(source_file, page.block.page_number, name),
                    ))

    # --- SHAREHOLDERS ---
    for ex in chapter.executives:
        if ex.ownership_pct and ex.ownership_pct > 5.0:
            chapter.shareholders.append(Shareholder(
                name=ex.name, role=ex.role,
                ownership_pct=ex.ownership_pct,
                evidence=ex.evidence,
            ))

    # --- TIMELINE: search ALL company pages, not just timeline sub_chapter ---
    if verbose:
        print(f"  [LLM] Extracting timeline from {len(company_pages)} company pages...")

    chunks = _combine_texts(company_pages)
    for text, page_nums in chunks:
        system, prompt = prompts.prompt_timeline(text)
        data = client.extract_json(prompt, system)

        if data and isinstance(data, dict):
            for ev in (data.get("events") or []):
                if not isinstance(ev, dict):
                    continue
                year = ev.get("year")
                desc = _safe_str(ev.get("description"))
                if year and desc:
                    # Allow multiple events per year (different descriptions)
                    if not any(t.year == year and t.description == desc for t in chapter.timeline):
                        pg = page_nums[0] if page_nums else 0
                        chapter.timeline.append(TimelineEvent(
                            year=year, description=desc,
                            evidence=_evidence(source_file, pg, f"{year}: {desc}"),
                        ))

    # Deduplicate: keep unique year+description, sort by year
    seen = set()
    unique_timeline = []
    for t in sorted(chapter.timeline, key=lambda x: x.year):
        key = (t.year, t.description[:50])
        if key not in seen:
            seen.add(key)
            unique_timeline.append(t)
    chapter.timeline = unique_timeline

    # --- PRODUCTS: search ALL company pages ---
    if verbose:
        print(f"  [LLM] Extracting products from {len(company_pages)} company pages...")

    chunks = _combine_texts(company_pages)
    for text, page_nums in chunks:
        system, prompt = prompts.prompt_products(text)
        data = client.extract_json(prompt, system)

        if data and isinstance(data, dict):
            pg = page_nums[0] if page_nums else 0
            for prod in (data.get("products") or []):
                if not isinstance(prod, dict):
                    continue
                name = _safe_str(prod.get("name"))
                if name and not _is_duplicate_product(name, chapter.products):
                    chapter.products.append(Product(
                        name=name,
                        category=_safe_str(prod.get("category")),
                        revenue_share_pct=prod.get("revenue_share_pct"),
                        is_proprietary=prod.get("is_proprietary", False),
                        description=_safe_str(prod.get("description")) or None,
                        evidence=_evidence(source_file, pg, name),
                    ))

    chapter.profile = profile
    return chapter


def extract_market_llm(
    client: OllamaClient,
    classified: list[ClassifiedPage],
    source_file: str,
    verbose: bool = False,
) -> MarketChapter:
    """Extract market data using LLM."""
    chapter = MarketChapter()

    market_pages = _get_pages_for_chapter(classified, "market")
    if not market_pages:
        return chapter

    # --- MARKET SIZE & FRAGMENTATION ---
    if verbose:
        print(f"  [LLM] Extracting market data from {len(market_pages)} pages...")

    chunks = _combine_texts(market_pages)

    for text, page_nums in chunks:
        system, prompt = prompts.prompt_market(text)
        data = client.extract_json(prompt, system)

        if data and isinstance(data, dict):
            pg = page_nums[0] if page_nums else 0

            for ms in (data.get("market_sizes") or []):
                if not isinstance(ms, dict):
                    continue
                geo = _safe_str(ms.get("geography"))
                year = ms.get("year", 0)
                if geo and year and not any(
                    m.geography == geo and m.year == year for m in chapter.market_sizes
                ):
                    chapter.market_sizes.append(MarketSize(
                        geography=geo, value=ms.get("value", 0),
                        unit=_safe_str(ms.get("unit")), year=year,
                        cagr=ms.get("cagr"),
                        evidence=_evidence(source_file, pg, f"{geo} {year}"),
                    ))

            frag = data.get("fragmentation")
            if frag and not chapter.market_fragmentation.is_filled:
                chapter.market_fragmentation = _tracked(frag, source_file, pg)

            for driver in (data.get("growth_drivers") or []):
                if driver and not any(g.value == driver for g in chapter.growth_drivers):
                    chapter.growth_drivers.append(_tracked(driver, source_file, pg))

            for barrier in (data.get("barriers") or []):
                if barrier and not any(b.value == barrier for b in chapter.barriers_to_entry):
                    chapter.barriers_to_entry.append(_tracked(barrier, source_file, pg))

    # --- COMPETITORS ---
    if verbose:
        print(f"  [LLM] Extracting competitors...")

    for page in market_pages:
        text_lower = page.block.clean_text.lower()
        if "top 5" in text_lower or "companhias" in text_lower or "concorrent" in text_lower:
            system, prompt = prompts.prompt_competitors(page.block.clean_text)
            data = client.extract_json(prompt, system)

            if data and isinstance(data, dict):
                for comp in (data.get("competitors") or []):
                    if not isinstance(comp, dict):
                        continue
                    name = _safe_str(comp.get("name"))
                    if name and not any(c.name == name for c in chapter.competitors):
                        chapter.competitors.append(Competitor(
                            name=name,
                            stores=comp.get("stores"),
                            revenue=comp.get("revenue"),
                            revenue_unit=_safe_str(comp.get("revenue_unit")) or None,
                            investor=_safe_str(comp.get("investor")) or None,
                            evidence=_evidence(source_file, page.block.page_number, name),
                        ))

    # --- MULTIPLES / PRECEDENT TRANSACTIONS ---
    if verbose:
        print(f"  [LLM] Extracting multiples...")

    for page in market_pages:
        if "ev/receita" in page.block.clean_text.lower() or "ev/ebitda" in page.block.clean_text.lower():
            system, prompt = prompts.prompt_multiples(page.block.clean_text)
            data = client.extract_json(prompt, system)

            if data and isinstance(data, dict):
                if data.get("median_ev_revenue") and not chapter.global_multiples_median.is_filled:
                    chapter.global_multiples_median = _tracked(
                        {
                            "ev_revenue_median": data["median_ev_revenue"],
                            "ev_ebitda_median": data.get("median_ev_ebitda"),
                        },
                        source_file, page.block.page_number,
                    )

                for txn in (data.get("precedent_transactions") or []):
                    if not isinstance(txn, dict):
                        continue
                    buyer = _safe_str(txn.get("buyer"))
                    target = _safe_str(txn.get("target"))
                    if buyer and target:
                        chapter.precedent_transactions.append(PrecedentTransaction(
                            date=_safe_str(txn.get("date")),
                            buyer=buyer, target=target,
                            stake_pct=txn.get("stake_pct"),
                            value=_safe_str(txn.get("value")),
                            ev_revenue=txn.get("ev_revenue"),
                            ev_ebitda=txn.get("ev_ebitda"),
                            evidence=_evidence(source_file, page.block.page_number, f"{buyer} -> {target}"),
                        ))

    return chapter


def extract_transaction_llm(
    client: OllamaClient,
    classified: list[ClassifiedPage],
    source_file: str,
    verbose: bool = False,
) -> TransactionChapter:
    """Extract transaction data using LLM."""
    chapter = TransactionChapter()

    tx_pages = _get_pages_for_chapter(classified, "transaction")
    content_pages = _get_all_content_pages(classified)

    # Broader search: transaction pages + first 8 + last 5 content pages
    candidate_pages = tx_pages + content_pages[:8] + content_pages[-5:]
    seen = set()
    unique_pages = []
    for p in candidate_pages:
        if p.block.page_number not in seen:
            seen.add(p.block.page_number)
            unique_pages.append(p)

    if verbose:
        print(f"  [LLM] Extracting transaction from {len(unique_pages)} pages...")

    chunks = _combine_texts(unique_pages)

    for text, page_nums in chunks:
        system, prompt = prompts.prompt_transaction(text)
        data = client.extract_json(prompt, system)

        if data and isinstance(data, dict):
            pg = page_nums[0] if page_nums else 0

            field_map = {
                "transaction_type": "transaction_type",
                "target_stake_range": "target_stake_range",
                "advisor": "advisor",
                "context": "context",
                "perimeter": "perimeter",
                "use_of_proceeds": "use_of_proceeds",
            }

            for json_key, attr_name in field_map.items():
                val = data.get(json_key)
                if val and getattr(chapter, attr_name).is_empty:
                    setattr(chapter, attr_name, _tracked(val, source_file, pg, json_key))

            if data.get("capital_needed") and chapter.capital_needed.is_empty:
                chapter.capital_needed = _tracked(data["capital_needed"], source_file, pg)

    return chapter