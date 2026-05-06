"""
Site-specific scrapers for web enrichment.
Each function returns raw text that the LLM will process.
"""
from __future__ import annotations
from .fetcher import fetch_url, search_duckduckgo


def scrape_reclame_aqui(company_name: str, verbose: bool = False) -> dict | None:
    """Scrape Reclame Aqui for company reputation data.

    Returns dict with raw text and URL, or None if failed.
    """
    if verbose:
        print(f"    🌐 Buscando Reclame Aqui: {company_name}")

    # First, search for the company page
    results = search_duckduckgo(f"{company_name} site:reclameaqui.com.br")

    ra_url = None
    for r in results:
        if "reclameaqui.com.br" in r.get("url", ""):
            ra_url = r["url"]
            break

    if not ra_url:
        # Try direct URL pattern
        slug = company_name.lower().replace(" ", "-").replace("ã", "a").replace("ó", "o")
        ra_url = f"https://www.reclameaqui.com.br/empresa/{slug}/"

    html = fetch_url(ra_url)
    if not html:
        # Return search snippets as fallback
        if results:
            snippets = " | ".join(r["snippet"] for r in results if r["snippet"])
            return {"text": snippets, "url": ra_url, "source": "reclame_aqui_search"}
        return None

    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")

        # Extract key metrics from the page
        texts = []

        # Try to get the company score and stats
        for selector in [
            "span[class*='score']", "div[class*='score']",
            "div[class*='index']", "span[class*='index']",
            "div[class*='reputation']", "span[class*='reputation']",
        ]:
            for el in soup.select(selector):
                t = el.get_text(strip=True)
                if t:
                    texts.append(t)

        # Get complaint summary stats
        for selector in [
            "div[class*='complaint']", "div[class*='status']",
            "span[class*='total']", "div[class*='stats']",
            "div[class*='info']", "li[class*='info']",
        ]:
            for el in soup.select(selector)[:10]:
                t = el.get_text(strip=True)
                if t and len(t) < 500:
                    texts.append(t)

        # Fallback: get all visible text from main content area
        if not texts:
            main = soup.select_one("main") or soup.select_one("body")
            if main:
                texts.append(main.get_text(separator=" ", strip=True)[:3000])

        if texts:
            return {"text": " | ".join(texts[:20]), "url": ra_url, "source": "reclame_aqui"}

    except Exception as e:
        if verbose:
            print(f"    ⚠️  Reclame Aqui parse error: {e}")

    return None


def search_jusbrasil(company_name: str, verbose: bool = False) -> dict | None:
    """Search Jusbrasil for litigation involving the company.

    Returns dict with raw text and URL, or None if failed.
    """
    if verbose:
        print(f"    🌐 Buscando Jusbrasil: {company_name}")

    # Search DuckDuckGo for Jusbrasil results about the company
    results = search_duckduckgo(f"{company_name} processos site:jusbrasil.com.br")

    if not results:
        # Try broader search
        results = search_duckduckgo(f"{company_name} processos judiciais contencioso")

    if results:
        snippets = []
        urls = []
        for r in results[:5]:
            if r["snippet"]:
                snippets.append(f"{r['title']}: {r['snippet']}")
            if r["url"]:
                urls.append(r["url"])

        if snippets:
            return {
                "text": " | ".join(snippets),
                "url": urls[0] if urls else "",
                "source": "jusbrasil_search",
            }

    return None


def search_company_info(company_name: str, fields: list[str], verbose: bool = False) -> dict | None:
    """Search for general company info (CNPJ, headquarters, employees).

    Args:
        company_name: Company name to search
        fields: List of fields to search for (e.g. ["cnpj", "sede", "funcionarios"])
    """
    if verbose:
        print(f"    🌐 Buscando info geral: {company_name}")

    queries = []
    if "legal_name" in fields or "headquarters" in fields:
        queries.append(f"{company_name} CNPJ razão social sede endereço")
    if "number_of_employees" in fields:
        queries.append(f"{company_name} número funcionários colaboradores")

    all_snippets = []
    all_urls = []

    for query in queries:
        results = search_duckduckgo(query)
        for r in results[:3]:
            if r["snippet"]:
                all_snippets.append(f"{r['title']}: {r['snippet']}")
            if r["url"]:
                all_urls.append(r["url"])

    if all_snippets:
        return {
            "text": " | ".join(all_snippets),
            "url": all_urls[0] if all_urls else "",
            "source": "web_search",
        }

    return None


def search_google_reviews(company_name: str, verbose: bool = False) -> dict | None:
    """Search for Google Reviews / reputation data."""
    if verbose:
        print(f"    🌐 Buscando Google Reviews: {company_name}")

    results = search_duckduckgo(f"{company_name} avaliações google reviews nota")

    if results:
        snippets = [f"{r['title']}: {r['snippet']}" for r in results[:3] if r["snippet"]]
        urls = [r["url"] for r in results if r["url"]]

        if snippets:
            return {
                "text": " | ".join(snippets),
                "url": urls[0] if urls else "",
                "source": "google_reviews_search",
            }

    return None


def search_market_size(
    sector: str,
    geography: str = "Brasil",
    verbose: bool = False,
) -> dict | None:
    """Search for total addressable market data for a sector.

    Two-query strategy: one for the local market (sized in BRL) and one
    for the global market (sized in USD). Combines snippets from both
    so the LLM can extract whichever it can pin down.

    The company name is intentionally NOT included — sector-level
    queries return better market-sizing snippets ("brazilian beauty
    market 12 billion 2024 cagr") than company-specific ones, which
    typically return the company's own revenue.
    """
    if verbose:
        print(f"    🌐 Buscando tamanho de mercado: {sector}")

    if not sector:
        return None

    queries = [
        f"tamanho mercado {sector} {geography} bilhões CAGR",
        f"{sector} market size {geography} CAGR 2024",
    ]
    snippets, urls = [], []
    for q in queries:
        for r in search_duckduckgo(q)[:3]:
            if r.get("snippet"):
                snippets.append(f"{r['title']}: {r['snippet']}")
            if r.get("url"):
                urls.append(r["url"])

    if not snippets:
        return None

    return {
        "text": " | ".join(snippets[:8]),
        "url": urls[0] if urls else "",
        "source": "market_size_search",
    }


def search_competitors(
    company_name: str,
    sector: str,
    verbose: bool = False,
) -> dict | None:
    """Search for likely competitors of a company.

    Combines two angles: a "who competes with X" query, and a "top
    players in <sector>" query. Both return snippet-level results
    that the LLM is expected to consolidate into a competitor list.
    """
    if verbose:
        print(f"    🌐 Buscando concorrentes: {company_name} ({sector})")

    queries = [
        f"{company_name} concorrentes {sector}",
        f"principais empresas {sector} Brasil ranking",
    ]
    if sector:
        queries.append(f"top players {sector} brazil market share")

    snippets, urls = [], []
    for q in queries:
        for r in search_duckduckgo(q)[:3]:
            if r.get("snippet"):
                snippets.append(f"{r['title']}: {r['snippet']}")
            if r.get("url"):
                urls.append(r["url"])

    if not snippets:
        return None

    return {
        "text": " | ".join(snippets[:10]),
        "url": urls[0] if urls else "",
        "source": "competitors_search",
    }


def search_sector_multiples(sector: str, verbose: bool = False) -> dict | None:
    """Search for trading-multiples references for a sector.

    The query targets EV/EBITDA and EV/Revenue medians as commonly
    reported by sell-side equity research and M&A databases. Brazilian
    multiples (PT-BR queries) often fall back to global ones since
    sector multiples are aggregated globally — that's fine, both are
    legitimate references for valuation triangulation.
    """
    if verbose:
        print(f"    🌐 Buscando múltiplos do setor: {sector}")

    if not sector:
        return None

    queries = [
        f"{sector} EV/EBITDA múltiplo setor mediana",
        f"{sector} sector EV/EBITDA EV/Revenue trading multiples",
    ]
    snippets, urls = [], []
    for q in queries:
        for r in search_duckduckgo(q)[:3]:
            if r.get("snippet"):
                snippets.append(f"{r['title']}: {r['snippet']}")
            if r.get("url"):
                urls.append(r["url"])

    if not snippets:
        return None

    return {
        "text": " | ".join(snippets[:8]),
        "url": urls[0] if urls else "",
        "source": "sector_multiples_search",
    }