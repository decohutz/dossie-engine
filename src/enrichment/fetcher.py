"""
HTTP fetcher with retry, rate limiting, and proper headers.
Only sends the company name to the internet — never CIM content.
"""
from __future__ import annotations
import time
import urllib.request
import urllib.error
import urllib.parse


DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
}

# Rate limiting: minimum seconds between requests
_last_request_time = 0.0
MIN_DELAY = 2.0


def fetch_url(url: str, timeout: int = 15) -> str | None:
    """Fetch a URL and return the HTML content.

    Returns None if the request fails.
    """
    global _last_request_time

    # Rate limiting
    elapsed = time.time() - _last_request_time
    if elapsed < MIN_DELAY:
        time.sleep(MIN_DELAY - elapsed)

    try:
        req = urllib.request.Request(url, headers=DEFAULT_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            _last_request_time = time.time()
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, Exception) as e:
        print(f"    ⚠️  Fetch failed for {url[:80]}: {e}")
        _last_request_time = time.time()
        return None


def search_duckduckgo(query: str, max_results: int = 5) -> list[dict]:
    """Search DuckDuckGo and return results with title, snippet, url."""
    encoded = urllib.parse.quote_plus(query)
    url = f"https://html.duckduckgo.com/html/?q={encoded}"
    html = fetch_url(url)

    if not html:
        return []

    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        results = []

        for item in soup.select(".result")[:max_results]:
            title_el = item.select_one(".result__title a")
            snippet_el = item.select_one(".result__snippet")

            if title_el:
                raw_url = title_el.get("href", "")
                # DuckDuckGo wraps URLs in redirects: //duckduckgo.com/l/?uddg=ENCODED_URL
                actual_url = _extract_ddg_url(raw_url)
                results.append({
                    "title": title_el.get_text(strip=True),
                    "snippet": snippet_el.get_text(strip=True) if snippet_el else "",
                    "url": actual_url,
                })

        return results
    except Exception as e:
        print(f"    ⚠️  DuckDuckGo parse error: {e}")
        return []


def _extract_ddg_url(raw_url: str) -> str:
    """Extract the actual URL from a DuckDuckGo redirect URL."""
    if "uddg=" in raw_url:
        try:
            parsed = urllib.parse.urlparse(raw_url)
            params = urllib.parse.parse_qs(parsed.query)
            if "uddg" in params:
                return urllib.parse.unquote(params["uddg"][0])
        except Exception:
            pass
    # If it's a relative URL starting with //, add https:
    if raw_url.startswith("//"):
        return "https:" + raw_url
    return raw_url