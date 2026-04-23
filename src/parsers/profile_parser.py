"""
Regex-based extractors for profile fields that are typically printed
verbatim in the CIM (legal name in CAPS in the disclaimer, headquarters
with city+state near entity descriptions).

These fields frequently slip past the LLM prompt either because the
disclaimer text is tokenized oddly, because the city appears in a
non-contextual sentence ("Armatti, empresa em São José do Rio Preto"),
or because the LLM defaults to the advisor's address when both are
present on the same page.

A targeted regex pass runs BEFORE the LLM profile call so the LLM only
works on fields that genuinely couldn't be matched deterministically.
"""
from __future__ import annotations
import re

# ── LEGAL NAME ──────────────────────────────────────────────
# Brazilian company suffixes. Matches "LTDA", "LTDA.", "S.A.", "S/A",
# "EIRELI", "ME", "EPP", "MEI". Uses word-boundary assertion at the
# end so "ME" doesn't match inside "MERCADO".
_LEGAL_SUFFIX = r"(?:LTDA\.?|S\.?\s*A\.?|S/A|EIRELI|EPP|MEI|ME)(?=[\s,.\-;:)\]/]|$)"

# Legal name body: sequence of CAPS words with optional lowercase
# connectors ("DOS", "DA", "DE", "E"). The whole run must end with a
# legal suffix. All-caps matches the way CIM disclaimers typically
# bold these names.
LEGAL_NAME_RE = re.compile(
    r"(?:[A-ZÀ-Ú][A-ZÀ-Ú'\-]+"
    r"(?:\s+(?:[A-ZÀ-Ú][A-ZÀ-Ú'\-]+|(?:DE|DO|DA|DOS|DAS|E)))*)"
    rf"\s+{_LEGAL_SUFFIX}",
    re.UNICODE,
)

# Advisor/consultant names we don't want to mistake for the target.
_ADVISOR_LEGAL_HINTS = (
    "VALUE CAPITAL", "ASSESSORIA FINANCEIRA", "ADVISORS",
    "CONSULTORES", "INVESTIMENTOS", "CAPITAL PARTNERS",
)


def extract_legal_name(text: str) -> str | None:
    """Find a Brazilian legal entity name (LTDA/S.A./etc) in the text.

    Returns the first plausible match, preferring longer names. Filters
    out advisor/consultant entities.
    """
    best = None
    best_len = 0
    for m in LEGAL_NAME_RE.finditer(text):
        cand = m.group(0).strip().rstrip(",.")
        cand_upper = cand.upper()
        # Skip advisor/consultant self-references
        if any(hint in cand_upper for hint in _ADVISOR_LEGAL_HINTS):
            continue
        # Require at least 3 words — single-word "LTDA" matches are noise
        if len(cand.split()) < 3:
            continue
        # Longest match wins (usually the most complete name)
        if len(cand) > best_len:
            best = cand
            best_len = len(cand)
    return best


# ── HEADQUARTERS ────────────────────────────────────────────
# City: first token starts with a capital (possibly accented); may be
# followed by up to 4 more capitalized tokens or lowercase connectors
# (for names like "São José do Rio Preto")
_CITY_TOKEN = r"[A-ZÀ-Ú][a-zà-ú]+"
_CITY_CONN = r"(?:de|do|da|dos|das|e)"
_CITY = rf"(?:{_CITY_TOKEN}(?:\s+(?:{_CITY_TOKEN}|{_CITY_CONN})){{0,4}})"

# State: full name or 2-letter UF
_STATE = (
    r"(?:São Paulo|Rio de Janeiro|Minas Gerais|Rio Grande do Sul|"
    r"Rio Grande do Norte|Santa Catarina|Espírito Santo|"
    r"Mato Grosso do Sul|Mato Grosso|"
    r"SP|RJ|MG|RS|PR|SC|BA|CE|GO|DF|PE|AM|PA|MA|MT|MS|ES|PB|RN|AL|PI|SE|AP|RO|RR|AC|TO)"
)
HQ_RE = re.compile(rf"({_CITY})[,\s]+({_STATE})\b", re.UNICODE)

# Context hints that suggest the city belongs to the advisor, not the target
_ADVISOR_CONTEXT = (
    "value capital", "assessoria", "advisors", "consultores",
    "presidente juscelino", "cj.", "conj.",
)

# First-tokens that are NOT cities (titles, street prefixes, generic words)
_NON_CITY_FIRST = {
    "Grupo", "Rede", "Empresa", "Companhia", "Presidente", "Associação",
    "Vice", "Diretor", "Sócio", "Fundador", "Av", "Avenida", "Rua",
    "Alameda", "Praça", "Rodovia",
}

# Brazilian state names (lowercase, accent-normalized for comparison).
# When the "city" matched by the regex is actually a state name, it means
# we matched an enumeration of states ("... em Paraná, Santa Catarina ...")
# rather than a true "City, State" pair.
_BR_STATE_NAMES_NORMALIZED = {
    "sao paulo", "rio de janeiro", "minas gerais", "rio grande do sul",
    "rio grande do norte", "santa catarina", "espirito santo",
    "mato grosso do sul", "mato grosso", "parana", "bahia", "ceara",
    "goias", "pernambuco", "amazonas", "para", "maranhao", "piaui",
    "paraiba", "alagoas", "sergipe", "amapa", "rondonia", "roraima",
    "acre", "tocantins", "distrito federal",
}


def _normalize_for_state_check(s: str) -> str:
    """Lowercase + strip accents for comparison against state-name set."""
    import unicodedata
    s = s.strip().lower()
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def extract_headquarters(text: str, context_window: int = 120,
                         company_trade_name: str | None = None) -> str | None:
    """Find a "City, State" pattern that is plausibly the target's HQ.

    A City-State match alone is too weak a signal — CIMs mention cities
    constantly (store maps, market analyses, subsidiary descriptions).
    To reduce false-positives, this function requires an HQ-keyword or
    the target company's name in the context window preceding the match.

    Filters out:
      - advisor-address contexts (via keyword window)
      - street addresses ("Av.", "Rua", etc.)
      - cases where the "city" matched is actually a state name, indicating
        we matched an enumeration of states
      - matches without any HQ-keyword or company reference nearby

    Args:
        text: Page text to scan.
        context_window: How many characters back from the match to look for
            HQ keywords and advisor/street hints.
        company_trade_name: Optional. If provided, the company's name in
            the context window also counts as an HQ-keyword hit.

    Returns:
        "City, State" (state as matched, either full name or UF), or None.
    """
    # Pattern to detect street-address context
    street_address_pat = re.compile(
        r"\b(?:Av(?:\.|enida)?|Rua|R\.|Al(?:\.|ameda)?|Praça|Rodovia|Travessa|Estrada)\s",
        re.IGNORECASE,
    )

    # Keywords that signal "here's the HQ". Portuguese-first with English
    # fallbacks for bilingual CIMs.
    hq_keywords = (
        "sede", "matriz", "sediad", "headquart", "head office",
        "localizada em", "localizado em", "baseada em", "baseado em",
        "fundada em", "fundado em",  # often precedes the founding city
        "com sede em", "empresa em",  # "X, empresa em <City>"
    )

    for m in HQ_RE.finditer(text):
        city = m.group(1).strip()
        state = m.group(2).strip()

        # Context check: look backwards up to `context_window` chars
        start = max(0, m.start() - context_window)
        context_before = text[start:m.start()]
        context_lower = context_before.lower()
        if any(kw in context_lower for kw in _ADVISOR_CONTEXT):
            continue
        # Skip if preceded by a street-prefix in the same window
        if street_address_pat.search(context_before):
            continue

        first_word = city.split()[0] if city else ""
        if first_word in _NON_CITY_FIRST:
            continue
        if len(city) < 3 or len(city) > 80:
            continue

        # Reject state-name-as-city (enumeration artifacts)
        city_norm = _normalize_for_state_check(city)
        if city_norm in _BR_STATE_NAMES_NORMALIZED:
            continue
        # Also reject partial state-name matches ("Mato" from "Mato Grosso",
        # "Rio" from "Rio Grande do Sul", etc.)
        for state_name in _BR_STATE_NAMES_NORMALIZED:
            if state_name.startswith(city_norm + " ") and len(city_norm) <= 6:
                # city_norm is the prefix of a state name, likely truncation
                break
        else:
            # No truncation match — pass through to next check
            pass
        # Above loop breaks on match; use a flag:
        if any(state_name.startswith(city_norm + " ") and len(city_norm) <= 6
               for state_name in _BR_STATE_NAMES_NORMALIZED):
            continue

        # Reject "Value Capital Advisors" etc. that appear inside the city match
        city_lower_full = city.lower()
        if any(hint in city_lower_full for hint in
               ("value capital", "advisors", "consultores", "assessoria")):
            continue

        # REQUIRED: context must contain an HQ keyword OR the company's name
        context_has_hq_keyword = any(kw in context_lower for kw in hq_keywords)
        context_has_company_name = (
            company_trade_name is not None
            and company_trade_name.lower() in context_lower
        )
        if not (context_has_hq_keyword or context_has_company_name):
            continue

        return f"{city}, {state}"

    return None