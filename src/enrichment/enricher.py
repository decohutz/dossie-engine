"""
Web enrichment orchestrator.
Takes a dossier with gaps, searches the web, and fills what it can.
"""
from __future__ import annotations
from ..models.evidence import Evidence, TrackedField, Gap
from ..models.dossier import Dossier
from ..llm.client import OllamaClient
from .sources import (
    scrape_reclame_aqui,
    search_jusbrasil,
    search_company_info,
    search_google_reviews,
    search_market_size,
    search_competitors,
    search_sector_multiples,
)


SYSTEM_ENRICH = (
    "Você é um analista financeiro. Extraia dados estruturados do texto de pesquisa web abaixo. "
    "Responda APENAS com JSON válido, sem explicações. "
    "Se a informação não estiver presente, use null."
)


def _evidence(source: str, url: str = "", confidence: float = 0.6) -> Evidence:
    return Evidence(
        source_file=source, page=0, excerpt=url[:300],
        confidence=confidence, extraction_method="web_enrichment",
    )


def enrich_dossier(
    dossier: Dossier,
    use_llm: bool = True,
    verbose: bool = False,
) -> Dossier:
    """Enrich a dossier by searching the web for gaps marked with requires_internet.

    Args:
        dossier: The dossier to enrich
        use_llm: Whether to use LLM to process web results
        verbose: Print progress
    """
    import re
    company_name = dossier.company.profile.trade_name.value or dossier.metadata.target_company
    if company_name:
        # Strip common prefixes and parenthetical suffixes
        for prefix in ["Grupo ", "Rede ", "Holding "]:
            if company_name.startswith(prefix):
                company_name = company_name[len(prefix):]
                break
        company_name = re.sub(r'\s*\(.*?\)\s*$', '', company_name).strip()
    # Strip common prefixes that mess up search queries
    for prefix in ["Grupo ", "Rede ", "Holding "]:
        if company_name and company_name.startswith(prefix):
            company_name = company_name[len(prefix):]
            break
    if not company_name:
        if verbose:
            print("  ⚠️  No company name available for web enrichment")
        return dossier

    if verbose:
        print(f"\n  🌐 Enriquecimento web para: {company_name}")

    # Initialize LLM client if needed
    client = None
    if use_llm:
        client = OllamaClient()
        if not client.is_available():
            if verbose:
                print("  ⚠️  Ollama não disponível. Salvando dados brutos.")
            client = None

    # --- REPUTATION (Reclame Aqui + Google Reviews) ---
    _enrich_reputation(dossier, company_name, client, verbose)

    # --- LITIGATION (Jusbrasil) ---
    _enrich_litigation(dossier, company_name, client, verbose)

    # --- COMPANY INFO (legal name, HQ, employees) ---
    _enrich_company_info(dossier, company_name, client, verbose)

    # --- MARKET (size, CAGR) ---
    _enrich_market(dossier, company_name, client, verbose)

    # --- COMPETITORS ---
    _enrich_competitors(dossier, company_name, client, verbose)

    # --- SECTOR MULTIPLES (EV/EBITDA, EV/Revenue) ---
    _enrich_multiples(dossier, client, verbose)

    # Update gaps: remove filled ones, update remaining
    _update_gaps(dossier, verbose)

    return dossier


def _enrich_reputation(
    dossier: Dossier, company_name: str,
    client: OllamaClient | None, verbose: bool,
):
    """Enrich reputation data from Reclame Aqui and Google Reviews."""
    if verbose:
        print(f"\n  [Web] Buscando reputação...")

    ra_data = scrape_reclame_aqui(company_name, verbose)
    google_data = search_google_reviews(company_name, verbose)

    combined_text = ""
    sources = []
    if ra_data:
        combined_text += f"RECLAME AQUI: {ra_data['text']}\n"
        sources.append(ra_data["source"])
    if google_data:
        combined_text += f"GOOGLE REVIEWS: {google_data['text']}\n"
        sources.append(google_data["source"])

    if not combined_text:
        if verbose:
            print("    ❌ Nenhum dado de reputação encontrado")
        return

    if client:
        prompt = f"""Extraia a reputação da empresa a partir dos dados de pesquisa web abaixo.

Retorne JSON no formato:
{{
  "reclame_aqui_score": 7.8,
  "reclame_aqui_complaints": 342,
  "reclame_aqui_resolution_rate": 0.89,
  "reclame_aqui_status": "Bom",
  "google_rating": 4.2,
  "google_reviews_count": 150,
  "summary": "breve resumo da reputação (1-2 frases)"
}}

DADOS:
{combined_text[:4000]}"""

        data = client.extract_json(prompt, SYSTEM_ENRICH)
        if data and isinstance(data, dict):
            source_name = " + ".join(sources)
            url = (ra_data or google_data or {}).get("url", "")

            # Store as a structured reputation field
            dossier.company.profile.reputation = TrackedField.filled(
                data, _evidence(source_name, url)
            )
            if verbose:
                summary = data.get("summary", "dados extraídos")
                print(f"    ✅ Reputação: {summary}")
            return

    # Fallback: store raw text without LLM processing
    source_name = " + ".join(sources)
    url = (ra_data or google_data or {}).get("url", "")
    dossier.company.profile.reputation = TrackedField.filled(
        {"raw_text": combined_text[:2000]},
        _evidence(source_name, url, confidence=0.4)
    )
    if verbose:
        print(f"    ✅ Reputação: dados brutos salvos (sem LLM)")


def _enrich_litigation(
    dossier: Dossier, company_name: str,
    client: OllamaClient | None, verbose: bool,
):
    """Enrich litigation data from Jusbrasil."""
    if verbose:
        print(f"\n  [Web] Buscando contencioso...")

    jus_data = search_jusbrasil(company_name, verbose)

    if not jus_data:
        if verbose:
            print("    ❌ Nenhum dado de contencioso encontrado")
        return

    if client:
        prompt = f"""Extraia informações sobre processos judiciais e contencioso da empresa a partir dos dados de pesquisa abaixo.

Retorne JSON no formato:
{{
  "total_lawsuits_found": 15,
  "lawsuit_types": ["trabalhista", "cível", "tributário"],
  "notable_cases": ["descrição breve caso 1", "descrição breve caso 2"],
  "risk_level": "baixo/médio/alto",
  "summary": "breve resumo do contencioso (1-2 frases)"
}}

DADOS:
{jus_data['text'][:4000]}"""

        data = client.extract_json(prompt, SYSTEM_ENRICH)
        if data and isinstance(data, dict):
            dossier.company.profile.litigation = TrackedField.filled(
                data, _evidence("jusbrasil", jus_data.get("url", ""))
            )
            if verbose:
                summary = data.get("summary", "dados extraídos")
                print(f"    ✅ Contencioso: {summary}")
            return

    # Fallback
    dossier.company.profile.litigation = TrackedField.filled(
        {"raw_text": jus_data["text"][:2000]},
        _evidence("jusbrasil", jus_data.get("url", ""), confidence=0.4)
    )
    if verbose:
        print(f"    ✅ Contencioso: dados brutos salvos")


def _enrich_company_info(
    dossier: Dossier, company_name: str,
    client: OllamaClient | None, verbose: bool,
):
    """Enrich basic company info (legal name, HQ, employees)."""
    profile = dossier.company.profile

    # Check which fields need enrichment
    fields_needed = []
    if profile.legal_name.is_empty:
        fields_needed.append("legal_name")
    if profile.headquarters.is_empty:
        fields_needed.append("headquarters")
    if profile.number_of_employees.is_empty:
        fields_needed.append("number_of_employees")

    if not fields_needed:
        return

    if verbose:
        print(f"\n  [Web] Buscando info da empresa: {fields_needed}")

    info_data = search_company_info(company_name, fields_needed, verbose)

    if not info_data:
        if verbose:
            print("    ❌ Nenhum dado de empresa encontrado")
        return

    if client:
        prompt = f"""Extraia informações cadastrais da empresa "{company_name}" a partir dos dados de pesquisa abaixo.

Retorne JSON no formato:
{{
  "legal_name": "razão social completa ou null",
  "headquarters": "cidade, estado ou null",
  "number_of_employees": 500,
  "cnpj": "XX.XXX.XXX/XXXX-XX ou null"
}}

DADOS:
{info_data['text'][:4000]}"""

        data = client.extract_json(prompt, SYSTEM_ENRICH)
        if data and isinstance(data, dict):
            ev = _evidence("web_search", info_data.get("url", ""))

            if data.get("legal_name") and profile.legal_name.is_empty:
                profile.legal_name = TrackedField.filled(data["legal_name"], ev)
                if verbose:
                    print(f"    ✅ Razão social: {data['legal_name']}")

            if data.get("headquarters") and profile.headquarters.is_empty:
                profile.headquarters = TrackedField.filled(data["headquarters"], ev)
                if verbose:
                    print(f"    ✅ Sede: {data['headquarters']}")

            if data.get("number_of_employees") and profile.number_of_employees.is_empty:
                profile.number_of_employees = TrackedField.filled(data["number_of_employees"], ev)
                if verbose:
                    print(f"    ✅ Funcionários: {data['number_of_employees']}")


def _enrich_market(
    dossier: Dossier, company_name: str,
    client: OllamaClient | None, verbose: bool,
):
    """Enrich market-size data when the dossier didn't extract any.

    We use the company's sector as the primary search anchor — sector-
    level queries return better TAM-shaped snippets than company-name
    queries. Skip silently when no sector is known: searching for a
    market without a sector tag would just return generic "Brazilian
    economy" snippets that aren't useful.
    """
    market = dossier.market
    if market.market_sizes:
        return  # already populated by PDF extractor

    sector = (dossier.company.profile.sector.value or "").strip()
    if not sector:
        if verbose:
            print(f"\n  [Web] Mercado: pulando (setor não identificado)")
        return

    if verbose:
        print(f"\n  [Web] Buscando tamanho de mercado...")

    data = search_market_size(sector, geography="Brasil", verbose=verbose)
    if not data:
        if verbose:
            print("    ❌ Nenhum dado de mercado encontrado")
        return

    if not client:
        if verbose:
            print("    ⚠️  Sem LLM: dados brutos descartados (mercado precisa de extração estruturada)")
        return

    prompt = f"""Extraia tamanho de mercado do setor "{sector}" a partir dos dados de pesquisa abaixo.

Retorne JSON no formato:
{{
  "market_sizes": [
    {{"geography": "Brasil", "value": 12.5, "unit": "BRL Bn", "year": 2024, "cagr": 0.08}},
    {{"geography": "Global", "value": 200.0, "unit": "USD Bn", "year": 2024, "cagr": 0.05}}
  ],
  "summary": "1-2 frases sobre o mercado e drivers"
}}

Inclua apenas entradas onde valor numérico está presente nos snippets. NUNCA invente valores.
Se nenhum tamanho for confiável, retorne {{"market_sizes": [], "summary": "..."}}.

DADOS:
{data['text'][:4000]}"""

    parsed = client.extract_json(prompt, SYSTEM_ENRICH)
    if not parsed or not isinstance(parsed, dict):
        if verbose:
            print("    ⚠️  LLM falhou em extrair mercado")
        return

    sizes = parsed.get("market_sizes") or []
    if not sizes:
        if verbose:
            print(f"    ❌ Mercado: {parsed.get('summary', 'sem dados extraíveis')}")
        return

    from ..models.market import MarketSize
    ev = _evidence("web_search:market", data.get("url", ""), confidence=0.5)
    for s in sizes:
        if not isinstance(s, dict):
            continue
        try:
            value = float(s.get("value") or 0)
        except (TypeError, ValueError):
            continue
        if value <= 0:
            continue
        try:
            year = int(s.get("year") or 0)
        except (TypeError, ValueError):
            year = 0
        cagr = s.get("cagr")
        try:
            cagr = float(cagr) if cagr is not None else None
        except (TypeError, ValueError):
            cagr = None
        market.market_sizes.append(MarketSize(
            geography=str(s.get("geography") or ""),
            value=value,
            unit=str(s.get("unit") or "BRL Bn"),
            year=year,
            cagr=cagr,
            evidence=ev,
        ))

    if verbose:
        print(f"    ✅ Mercado: {len(sizes)} entrada(s) extraída(s) — {parsed.get('summary', '')}")


def _enrich_competitors(
    dossier: Dossier, company_name: str,
    client: OllamaClient | None, verbose: bool,
):
    """Enrich the competitor list when the dossier didn't extract any."""
    market = dossier.market
    if market.competitors:
        return  # already populated

    sector = (dossier.company.profile.sector.value or "").strip()

    if verbose:
        print(f"\n  [Web] Buscando concorrentes...")

    data = search_competitors(company_name, sector, verbose=verbose)
    if not data:
        if verbose:
            print("    ❌ Nenhum concorrente encontrado")
        return

    if not client:
        if verbose:
            print("    ⚠️  Sem LLM: dados brutos descartados")
        return

    prompt = f"""Extraia uma lista de concorrentes da empresa "{company_name}" (setor: {sector}) a partir dos dados de pesquisa abaixo.

Retorne JSON no formato:
{{
  "competitors": [
    {{"name": "Nome Concorrente 1", "stores": 100, "revenue": 500.0, "revenue_unit": "BRL MM", "investor": "ABC Capital"}},
    {{"name": "Nome Concorrente 2", "stores": null, "revenue": null, "revenue_unit": null, "investor": null}}
  ]
}}

Regras:
- Inclua apenas concorrentes claramente nomeados nos snippets, NÃO invente nomes.
- Use null para campos não identificados.
- NÃO inclua a própria "{company_name}" na lista.
- Limite a 8 concorrentes mais relevantes.

DADOS:
{data['text'][:4000]}"""

    parsed = client.extract_json(prompt, SYSTEM_ENRICH)
    if not parsed or not isinstance(parsed, dict):
        if verbose:
            print("    ⚠️  LLM falhou em extrair concorrentes")
        return

    comps = parsed.get("competitors") or []
    if not comps:
        if verbose:
            print("    ❌ Concorrentes: lista vazia")
        return

    from ..models.market import Competitor
    ev = _evidence("web_search:competitors", data.get("url", ""), confidence=0.5)
    target_lower = company_name.lower()
    added = 0
    for c in comps:
        if not isinstance(c, dict):
            continue
        name = (c.get("name") or "").strip()
        if not name or target_lower in name.lower():
            continue
        revenue = c.get("revenue")
        try:
            revenue = float(revenue) if revenue is not None else None
        except (TypeError, ValueError):
            revenue = None
        stores = c.get("stores")
        try:
            stores = int(stores) if stores is not None else None
        except (TypeError, ValueError):
            stores = None
        market.competitors.append(Competitor(
            name=name, stores=stores, revenue=revenue,
            revenue_unit=c.get("revenue_unit"),
            investor=c.get("investor"),
            evidence=ev,
        ))
        added += 1

    if verbose:
        print(f"    ✅ Concorrentes: {added} adicionados")


def _enrich_multiples(
    dossier: Dossier,
    client: OllamaClient | None, verbose: bool,
):
    """Enrich sector trading multiples (EV/EBITDA, EV/Revenue medians).

    These flow into the valuation engine via
    ``dossier.market.global_multiples_median`` — once populated, the
    multiples-comparables and DCF-exit methods stop returning None,
    and IRR/MOIC compute properly across scenarios.
    """
    market = dossier.market
    if market.global_multiples_median.is_filled:
        return  # already populated by PDF extractor

    sector = (dossier.company.profile.sector.value or "").strip()
    if not sector:
        if verbose:
            print(f"\n  [Web] Múltiplos: pulando (setor não identificado)")
        return

    if verbose:
        print(f"\n  [Web] Buscando múltiplos do setor...")

    data = search_sector_multiples(sector, verbose=verbose)
    if not data:
        if verbose:
            print("    ❌ Nenhum múltiplo encontrado")
        return

    if not client:
        if verbose:
            print("    ⚠️  Sem LLM: múltiplos requerem extração estruturada")
        return

    prompt = f"""Extraia múltiplos de trading do setor "{sector}" a partir dos dados de pesquisa abaixo.

Retorne JSON no formato:
{{
  "ev_ebitda_median": 11.0,
  "ev_revenue_median": 1.8,
  "source_note": "fonte dos múltiplos (e.g. Damodaran, Capital IQ, etc)"
}}

Regras:
- Use null se o múltiplo não estiver claramente reportado.
- Aceite ranges (e.g. "8x-12x" → use o ponto médio).
- NÃO invente valores: se nada confiável estiver nos snippets, retorne null em ambos.

DADOS:
{data['text'][:3000]}"""

    parsed = client.extract_json(prompt, SYSTEM_ENRICH)
    if not parsed or not isinstance(parsed, dict):
        if verbose:
            print("    ⚠️  LLM falhou em extrair múltiplos")
        return

    ev_ebitda = parsed.get("ev_ebitda_median")
    ev_rev = parsed.get("ev_revenue_median")
    try:
        ev_ebitda = float(ev_ebitda) if ev_ebitda is not None else None
    except (TypeError, ValueError):
        ev_ebitda = None
    try:
        ev_rev = float(ev_rev) if ev_rev is not None else None
    except (TypeError, ValueError):
        ev_rev = None

    if ev_ebitda is None and ev_rev is None:
        if verbose:
            print("    ❌ Múltiplos: nenhum valor confiável")
        return

    multiples_data = {
        "ev_ebitda_median": ev_ebitda,
        "ev_revenue_median": ev_rev,
        "source_note": parsed.get("source_note") or "",
    }
    market.global_multiples_median = TrackedField.filled(
        multiples_data,
        _evidence("web_search:multiples", data.get("url", ""), confidence=0.4),
    )

    if verbose:
        bits = []
        if ev_ebitda is not None:
            bits.append(f"EV/EBITDA={ev_ebitda:.1f}x")
        if ev_rev is not None:
            bits.append(f"EV/Revenue={ev_rev:.1f}x")
        print(f"    ✅ Múltiplos: {', '.join(bits)}")


def _update_gaps(dossier: Dossier, verbose: bool):
    """Remove gaps that were filled by enrichment."""
    profile = dossier.company.profile
    filled_paths = set()

    # Check which fields were filled
    if hasattr(profile, 'reputation') and isinstance(getattr(profile, 'reputation', None), TrackedField):
        if profile.reputation.is_filled:
            filled_paths.add("company.reputation")

    if hasattr(profile, 'litigation') and isinstance(getattr(profile, 'litigation', None), TrackedField):
        if profile.litigation.is_filled:
            filled_paths.add("company.litigation")

    if profile.legal_name.is_filled:
        filled_paths.add("company.profile.legal_name")
    if profile.headquarters.is_filled:
        filled_paths.add("company.profile.headquarters")
    if profile.number_of_employees.is_filled:
        filled_paths.add("company.profile.number_of_employees")
        filled_paths.add("company.employee_count")

    # Market enrichment may have populated these chapter fields.
    market = dossier.market
    if market.market_sizes:
        # gap_analyzer registers market gaps under several aliases — cover
        # all known field paths that the gap analyzer might use.
        filled_paths.add("market.market_sizes")
        filled_paths.add("market.size")
    if market.competitors:
        filled_paths.add("market.competitors")
    if market.global_multiples_median.is_filled:
        filled_paths.add("market.global_multiples_median")
        filled_paths.add("market.multiples")

    # Remove filled gaps
    original_count = len(dossier.gaps)
    dossier.gaps = [g for g in dossier.gaps if g.field_path not in filled_paths]
    removed = original_count - len(dossier.gaps)

    if verbose and removed:
        print(f"\n  ✅ {removed} gaps preenchidos pelo enriquecimento web")