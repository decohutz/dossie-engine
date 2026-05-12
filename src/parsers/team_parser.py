"""
Structural parser for executive team pages.

Many CIMs present the leadership team as a grid: one column per person,
with rows for tenure, first name, surname, ownership, role, and background
bullets. Linear text extraction loses the column-to-row alignment and
causes LLMs to misassign roles to names (often with a cyclic shift when
role text doesn't fit in the same column width as the name).

This parser uses pdfplumber's word coordinates to:
  1. Detect the "first-name row" (3-10 capitalized words, evenly spaced).
  2. Anchor columns to each name's x-center.
  3. Bucket every other word on the page into its nearest column by x-distance.
  4. Use the global first-bullet y as the cutoff between "role" and "background".

Returns None when the page doesn't match the expected layout, so callers
can fall back to LLM extraction for atypical designs.
"""
from __future__ import annotations
import re
from collections import defaultdict

try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False


# A first name: starts with uppercase (including accented), rest lowercase.
# 3+ chars to avoid catching "Sr", "Dr" etc.
NAME_TOKEN = re.compile(r"^[A-ZÀ-Ú][a-zà-ú]{2,}$")
# Ownership percentage: "(48%)", "(48%)¹", "(1.5%)"
PCT_TOKEN = re.compile(r"\((\d+(?:[.,]\d+)?)%\)")
# Tenure marker: "14+", "5+"
TENURE_TOKEN = re.compile(r"^(\d+)\+$")
BULLET_CHARS = {"▪", "•", "■", "●", "◦"}


def _rows_by_y(words, tol: int = 3):
    """Group words into rows by y-coordinate within a tolerance."""
    buckets = defaultdict(list)
    for w in words:
        y = round(w["top"] / tol) * tol
        buckets[y].append(w)
    return [(y, sorted(buckets[y], key=lambda w: w["x0"])) for y in sorted(buckets)]


def _find_name_row(rows):
    """Find the row that introduces the exec columns.

    Heuristic: 3-10 capitalized single words, roughly evenly spaced in x.
    Returns (y, list-of-word-dicts) or (None, None).
    """
    for y, ws in rows:
        candidates = [w for w in ws if NAME_TOKEN.match(w["text"])]
        if not (3 <= len(candidates) <= 10):
            continue
        xs = sorted(w["x0"] for w in candidates)
        gaps = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
        if not gaps:
            continue
        # Spacing must be fairly uniform (largest gap <= 2.5x smallest)
        if max(gaps) / max(min(gaps), 1) < 2.5:
            return y, sorted(candidates, key=lambda w: w["x0"])
    return None, None


def _bucket_by_column(rows, anchors):
    """Assign every word to its nearest column anchor by x-center."""
    n = len(anchors)
    cols = defaultdict(list)
    for y, ws in rows:
        for w in ws:
            xc = (w["x0"] + w["x1"]) / 2
            col = min(range(n), key=lambda i: abs(xc - anchors[i]))
            cols[col].append((y, w["text"]))
    return cols


def _find_first_bullet_y(rows, after_y: float) -> float | None:
    """Global y-cutoff where bios start (any column's first bullet)."""
    for y, ws in rows:
        if y > after_y and any(w["text"] in BULLET_CHARS for w in ws):
            return y
    return None


def _extract_column_fields(col_words, first_name: str, name_y: float,
                           bullet_y: float | None) -> dict:
    """Extract structured fields from one column's word list."""
    col_words = sorted(col_words, key=lambda yw: yw[0])

    ownership = None
    pct_y = None
    tenure = None
    surname_parts: list[str] = []
    role_parts: list[str] = []
    bg_parts: list[str] = []

    for y, t in col_words:
        # Tenure is above the name row
        if tenure is None:
            m = TENURE_TOKEN.match(t)
            if m and y < name_y:
                tenure = int(m.group(1))
                continue

        # Ownership
        m_pct = PCT_TOKEN.search(t)
        if m_pct and pct_y is None:
            ownership = float(m_pct.group(1).replace(",", "."))
            pct_y = y
            continue

        # Surname: capitalized words between name_y and pct_y
        if y > name_y and (pct_y is None or y < pct_y):
            if NAME_TOKEN.match(t) and t != first_name:
                surname_parts.append(t)
                continue
            # Connectors like "da", "de", "dos" between capitalized surnames
            if t.lower() in {"da", "de", "do", "das", "dos"} and surname_parts:
                surname_parts.append(t)
                continue

        # Role section: between ownership and first bullet on page
        if pct_y is not None and y > pct_y:
            if bullet_y is not None and y >= bullet_y:
                # Background section
                if t not in BULLET_CHARS:
                    bg_parts.append(t)
                continue
            # Skip noise that re-matches tenure/pct tokens
            if PCT_TOKEN.search(t) or TENURE_TOKEN.match(t):
                continue
            role_parts.append(t)

    full_name = first_name
    if surname_parts:
        full_name = f"{first_name} " + " ".join(surname_parts)

    role = " ".join(role_parts).strip() or None
    # Clean stray punctuation artifacts in role
    if role:
        role = re.sub(r"\s+", " ", role).strip(" ,;:")

    background = " ".join(bg_parts).strip() or None
    if background:
        # Truncate to a reasonable length; bios can be long
        background = re.sub(r"\s+", " ", background)
        if len(background) > 500:
            background = background[:497] + "..."

    return {
        "name": full_name,
        "role": role,
        "tenure_years": tenure,
        "ownership_pct": ownership,
        "background": background,
    }


def parse_team_page(pdf_path: str, page_num: int, verbose: bool = False) -> list[dict] | None:
    """Extract executives from a team page using column-anchored parsing.

    Returns a list of dicts with keys matching the LLM output schema:
      {name, role, tenure_years, ownership_pct, background}

    Returns None if the page does not match the expected grid layout
    (e.g., single-person profile, free-form bio, or no detectable name row).
    Callers should fall back to LLM extraction in that case.
    """
    if not HAS_PDFPLUMBER:
        return None

    try:
        with pdfplumber.open(pdf_path) as pdf:
            if page_num < 1 or page_num > len(pdf.pages):
                return None
            page = pdf.pages[page_num - 1]
            words = page.extract_words(x_tolerance=3, y_tolerance=3)
    except Exception as e:
        if verbose:
            print(f"    ⚠️  team_parser: failed to read page {page_num}: {e}")
        return None

    if not words:
        return None

    rows = _rows_by_y(words)
    name_y, name_row = _find_name_row(rows)
    if name_row is None:
        if verbose:
            print(f"    ⚠️  team_parser: no name row on page {page_num}")
        return None

    anchors = [(w["x0"] + w["x1"]) / 2 for w in name_row]
    first_names = [w["text"] for w in name_row]

    cols = _bucket_by_column(rows, anchors)
    bullet_y = _find_first_bullet_y(rows, after_y=name_y)

    execs = []
    for i, first_name in enumerate(first_names):
        fields = _extract_column_fields(cols[i], first_name, name_y, bullet_y)
        # Only keep if we got at least name + one other signal
        if fields["role"] or fields["ownership_pct"] is not None or fields["tenure_years"] is not None:
            execs.append(fields)

    if verbose:
        print(f"    ✅ team_parser: extracted {len(execs)} executives from page {page_num}")

    return execs or None