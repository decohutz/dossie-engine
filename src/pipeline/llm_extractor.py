"""
LLM-based data extraction.
Replaces hardcoded rules with LLM calls for generic extraction from any CIM.

Consistency features:
- Retry with validation: if results are below threshold, retries and merges
- Post-extraction validation: warns about suspicious data
- Deduplication: products, timeline events
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

# Enhanced extraction (optional dependencies)
try:
    from ..parsers.ocr_helper import (
        extract_layout_text, ocr_page, is_ocr_available,
        ocr_competitor_logos, ocr_column_strips,
    )
    HAS_ENHANCED = True
except ImportError:
    HAS_ENHANCED = False


# ── Minimum thresholds for retry ──────────────────────────────
MIN_TIMELINE_EVENTS = 5
MIN_PRODUCTS = 3
MIN_EXECUTIVES = 3


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
    """Check if a product name is a duplicate of an existing one."""
    name_lower = name.lower().strip()
    for p in existing:
        existing_lower = p.name.lower().strip()
        if name_lower == existing_lower:
            return True
        if name_lower.startswith(existing_lower) or existing_lower.startswith(name_lower):
            return True
    return False


def _merge_timeline(existing: list[TimelineEvent], new_events: list[dict], source_file: str, page: int):
    """Merge new timeline events into existing list, avoiding duplicates."""
    for ev in new_events:
        if not isinstance(ev, dict):
            continue
        year = ev.get("year")
        desc = _safe_str(ev.get("description"))
        if year and desc:
            is_dup = False
            for t in existing:
                if t.year == year:
                    if (t.description.lower()[:30] == desc.lower()[:30] or
                            desc.lower() in t.description.lower() or
                            t.description.lower() in desc.lower()):
                        is_dup = True
                        break
            if not is_dup:
                existing.append(TimelineEvent(
                    year=year, description=desc,
                    evidence=_evidence(source_file, page, f"{year}: {desc}"),
                ))


def _deduplicate_timeline(events: list[TimelineEvent]) -> list[TimelineEvent]:
    """Deduplicate and sort timeline events."""
    seen = set()
    unique = []
    for t in sorted(events, key=lambda x: x.year):
        key = (t.year, t.description.lower().strip()[:40])
        if key not in seen:
            seen.add(key)
            unique.append(t)
    return unique


def _validate_timeline(events: list[TimelineEvent], verbose: bool = False) -> list[str]:
    """Validate timeline and return warnings."""
    warnings = []
    if len(events) < MIN_TIMELINE_EVENTS:
        warnings.append(f"Timeline tem apenas {len(events)} eventos (mínimo esperado: {MIN_TIMELINE_EVENTS})")
    for ev in events:
        if ev.year < 1900 or ev.year > 2035:
            warnings.append(f"Ano suspeito na timeline: {ev.year}")
    return warnings


def _validate_executives(executives: list[Executive], verbose: bool = False) -> list[str]:
    """Validate executives and return warnings."""
    warnings = []
    if len(executives) < MIN_EXECUTIVES:
        warnings.append(f"Apenas {len(executives)} executivos encontrados (mínimo esperado: {MIN_EXECUTIVES})")
    for ex in executives:
        if not ex.name or len(ex.name) < 3:
            warnings.append(f"Nome de executivo suspeito: '{ex.name}'")
        if not ex.role:
            warnings.append(f"Executivo sem cargo: {ex.name}")
        if ex.ownership_pct is not None and ex.ownership_pct > 100:
            warnings.append(f"Participação > 100% para {ex.name}: {ex.ownership_pct}%")
    total_pct = sum(ex.ownership_pct or 0 for ex in executives)
    if total_pct > 105:
        warnings.append(f"Soma das participações = {total_pct}% (> 100%)")
    return warnings


def _validate_products(products: list[Product], verbose: bool = False) -> list[str]:
    """Validate products and return warnings."""
    warnings = []
    if len(products) < MIN_PRODUCTS:
        warnings.append(f"Apenas {len(products)} produtos encontrados (mínimo esperado: {MIN_PRODUCTS})")
    total_rev = sum(p.revenue_share_pct or 0 for p in products)
    if total_rev > 105:
        warnings.append(f"Soma dos % de receita = {total_rev}% (> 100%)")
    return warnings


def extract_company_llm(
    client: OllamaClient,
    classified: list[ClassifiedPage],
    source_file: str,
    verbose: bool = False,
    pdf_path: str = "",
) -> CompanyChapter:
    """Extract company data using LLM with retry and validation."""
    chapter = CompanyChapter()
    profile = CompanyProfile()

    content_pages = _get_all_content_pages(classified)
    company_pages = _get_pages_for_chapter(classified, "company")

    # ── PROFILE ──────────────────────────────────────────────
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

    # Second pass for missing key fields
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

    # ── EXECUTIVES (regular first, layout as retry) ────────────
    if verbose:
        print(f"  [LLM] Extracting executives...")

    exec_pages = [p for p in classified if p.sub_chapter == "team"] or company_pages

    # First pass: regular text (stable)
    for page in exec_pages:
        system, prompt = prompts.prompt_executives(page.block.clean_text)
        data = client.extract_json(prompt, system)

        if data and isinstance(data, dict):
            for ex in (data.get("executives") or []):
                if not isinstance(ex, dict):
                    continue
                name = _safe_str(ex.get("name"))
                if name and not any(e.name == name for e in chapter.executives):
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

    # Retry pass: if any executive has empty role, retry with layout text
    execs_without_role = [e for e in chapter.executives if not e.role]
    if execs_without_role and HAS_ENHANCED and pdf_path:
        if verbose:
            names = ", ".join(e.name for e in execs_without_role)
            print(f"    🔄 Retrying with layout text for: {names}")

        for page in exec_pages:
            layout_text = extract_layout_text(pdf_path, page.block.page_number, verbose)
            if not layout_text or len(layout_text) < 50:
                continue

            if verbose:
                print(f"    📐 Layout retry on page {page.block.page_number}")

            page_text = (
                f"LAYOUT COLUMNAR (cada linha separada por | é uma coluna):\n{layout_text}\n\n"
                f"TEXTO ORIGINAL:\n{page.block.clean_text}"
            )
            system, prompt = prompts.prompt_executives(page_text)
            data = client.extract_json(prompt, system, temperature=0.05)

            if data and isinstance(data, dict):
                for ex in (data.get("executives") or []):
                    if not isinstance(ex, dict):
                        continue
                    name = _safe_str(ex.get("name"))
                    role = _safe_str(ex.get("role"))
                    if not name or not role:
                        continue
                    # Update only executives that had missing roles
                    for existing in chapter.executives:
                        if existing.name == name and not existing.role:
                            existing.role = role
                            entity = _safe_str(ex.get("entity"))
                            if entity and entity.lower() not in role.lower():
                                existing.role = f"{role} ({entity})"
                            if verbose:
                                print(f"    ✅ {name} → {existing.role}")
                            break

    # Validate executives
    exec_warnings = _validate_executives(chapter.executives, verbose)
    if verbose and exec_warnings:
        for w in exec_warnings:
            print(f"    ⚠️  {w}")

    # ── SHAREHOLDERS ─────────────────────────────────────────
    for ex in chapter.executives:
        if ex.ownership_pct and ex.ownership_pct > 5.0:
            chapter.shareholders.append(Shareholder(
                name=ex.name, role=ex.role,
                ownership_pct=ex.ownership_pct,
                evidence=ex.evidence,
            ))

    # ── TIMELINE with retry ──────────────────────────────────
    if verbose:
        print(f"  [LLM] Extracting timeline from {len(company_pages)} company pages...")

    chunks = _combine_texts(company_pages)
    for text, page_nums in chunks:
        system, prompt = prompts.prompt_timeline(text)
        data = client.extract_json(prompt, system)

        if data and isinstance(data, dict):
            pg = page_nums[0] if page_nums else 0
            _merge_timeline(chapter.timeline, data.get("events") or [], source_file, pg)

    chapter.timeline = _deduplicate_timeline(chapter.timeline)

    # Retry if below threshold
    if len(chapter.timeline) < MIN_TIMELINE_EVENTS:
        if verbose:
            print(f"    🔄 Timeline has {len(chapter.timeline)} events (< {MIN_TIMELINE_EVENTS}), retrying...")

        all_chunks = _combine_texts(content_pages)
        for text, page_nums in all_chunks:
            system, prompt = prompts.prompt_timeline(text)
            data = client.extract_json(prompt, system, temperature=0.05)

            if data and isinstance(data, dict):
                pg = page_nums[0] if page_nums else 0
                _merge_timeline(chapter.timeline, data.get("events") or [], source_file, pg)

        chapter.timeline = _deduplicate_timeline(chapter.timeline)

        if verbose:
            print(f"    → After retry: {len(chapter.timeline)} events")

    # Validate timeline
    tl_warnings = _validate_timeline(chapter.timeline, verbose)
    if verbose and tl_warnings:
        for w in tl_warnings:
            print(f"    ⚠️  {w}")

    # ── PRODUCTS with retry ──────────────────────────────────
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

    # Retry if below threshold
    if len(chapter.products) < MIN_PRODUCTS:
        if verbose:
            print(f"    🔄 Products has {len(chapter.products)} items (< {MIN_PRODUCTS}), retrying...")

        all_chunks = _combine_texts(content_pages)
        for text, page_nums in all_chunks:
            system, prompt = prompts.prompt_products(text)
            data = client.extract_json(prompt, system, temperature=0.05)

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

        if verbose:
            print(f"    → After retry: {len(chapter.products)} products")

    # Validate products
    prod_warnings = _validate_products(chapter.products, verbose)
    if verbose and prod_warnings:
        for w in prod_warnings:
            print(f"    ⚠️  {w}")

    chapter.profile = profile
    return chapter


def extract_market_llm(
    client: OllamaClient,
    classified: list[ClassifiedPage],
    source_file: str,
    verbose: bool = False,
    pdf_path: str = "",
) -> MarketChapter:
    """Extract market data using LLM."""
    chapter = MarketChapter()

    market_pages = _get_pages_for_chapter(classified, "market")
    if not market_pages:
        return chapter

    # ── MARKET SIZE & FRAGMENTATION ──────────────────────────
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

    # Retry market sizes if empty
    if len(chapter.market_sizes) == 0:
        if verbose:
            print(f"    🔄 No market sizes found, retrying with broader search...")
        content_pages = _get_all_content_pages(classified)
        for page in content_pages:
            text_lower = page.block.clean_text.lower()
            if any(kw in text_lower for kw in ["mercado", "market", "usd", "brl", "bn", "bilh"]):
                system, prompt = prompts.prompt_market(page.block.clean_text)
                data = client.extract_json(prompt, system, temperature=0.05)
                if data and isinstance(data, dict):
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
                                evidence=_evidence(source_file, page.block.page_number, f"{geo} {year}"),
                            ))
        if verbose:
            print(f"    → After retry: {len(chapter.market_sizes)} market sizes")

    # ── COMPETITORS (with spatial OCR for logos) ───────────────────────
    if verbose:
        print(f"  [LLM] Extracting competitors...")

    for page in market_pages:
        text_lower = page.block.clean_text.lower()
        if not ("top 5" in text_lower or "companhias" in text_lower
                or "concorrent" in text_lower or "ranking" in text_lower
                or "player" in text_lower or "landscape" in text_lower):
            continue

        page_text = page.block.clean_text
        page_num = page.block.page_number

        # ── Strategy 1: Spatial OCR on embedded logo images ──
        logo_labels = []
        strips = None
        if HAS_ENHANCED and pdf_path and is_ocr_available():
            logos = ocr_competitor_logos(pdf_path, page_num, verbose=verbose)
            if logos:
                logo_labels = logos
                logo_section = "\n\nLOGOS/MARCAS IDENTIFICADOS POR OCR (da esquerda para a direita):\n"
                for idx, logo in enumerate(logos, 1):
                    logo_section += f"  LOGO_{idx}: {logo['text']}\n"
                page_text += logo_section

            # ── Strategy 2: Fallback column strips if no logos found ──
            if not logos:
                strips = ocr_column_strips(pdf_path, page_num, n_columns=5, verbose=verbose)
                if strips:
                    logo_section = "\n\nTEXTO IDENTIFICADO POR OCR (colunas da esquerda para a direita):\n"
                    for s in strips:
                        logo_section += f"  COLUNA_{s['column_index']+1}: {s['text']}\n"
                    page_text += logo_section

            # ── Strategy 3: Full page OCR as last resort ──
            if not logos and not strips:
                ocr_text = ocr_page(pdf_path, page_num, verbose=verbose)
                if ocr_text:
                    if verbose:
                        print(f"    🔍 OCR enriching competitor page {page_num}")
                    page_text += f"\n\nTEXTO ADICIONAL EXTRAÍDO DE IMAGENS/LOGOS:\n{ocr_text}"

        system, prompt = prompts.prompt_competitors(page_text)
        data = client.extract_json(prompt, system)

        if data and isinstance(data, dict):
            for comp in (data.get("competitors") or []):
                if not isinstance(comp, dict):
                    continue
                name = _safe_str(comp.get("name"))
                if not name:
                    continue

                # ── Post-processing: filter out non-retailers ──
                name_lower = name.lower()

                # Skip if it's clearly a manufacturer, not a retailer
                manufacturer_names = {
                    "hoya", "carl zeiss", "zeiss", "rodenstock",
                    "transitions", "varilux", "crizal", "nikon",
                }
                if name_lower in manufacturer_names:
                    if verbose:
                        print(f"    ⚠️  Skipping manufacturer: {name}")
                    continue

                # Skip if it's a fund/investor, not a company
                investor_keywords = {"investimentos", "capital", "partners",
                                     "ventures", "fund", "gestão", "asset"}
                if any(kw in name_lower for kw in investor_keywords):
                    if verbose:
                        print(f"    ⚠️  Skipping investor entity: {name}")
                    continue

                # Deduplicate
                if any(c.name.lower() == name_lower for c in chapter.competitors):
                    continue

                chapter.competitors.append(Competitor(
                    name=name,
                    stores=comp.get("stores"),
                    revenue=comp.get("revenue"),
                    revenue_unit=_safe_str(comp.get("revenue_unit")) or None,
                    investor=_safe_str(comp.get("investor")) or None,
                    evidence=_evidence(source_file, page_num, name),
                ))

    # ── MULTIPLES / PRECEDENT TRANSACTIONS ───────────────────
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
                        ev_ebitda = txn.get("ev_ebitda")
                        if ev_ebitda is not None and ev_ebitda > 50:
                            if verbose:
                                print(f"    ⚠️  EV/EBITDA suspeito: {ev_ebitda}x para {buyer}/{target}")
                            ev_ebitda = None

                        chapter.precedent_transactions.append(PrecedentTransaction(
                            date=_safe_str(txn.get("date")),
                            buyer=buyer, target=target,
                            stake_pct=txn.get("stake_pct"),
                            value=_safe_str(txn.get("value")),
                            ev_revenue=txn.get("ev_revenue"),
                            ev_ebitda=ev_ebitda,
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