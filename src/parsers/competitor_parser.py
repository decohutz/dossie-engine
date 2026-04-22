"""
Structural parser for competitor/Top-N ranking tables.

CIMs routinely present the competitive landscape as a table where:
  - Company names are rendered as LOGOS (images), not text
  - Each row has numeric data: stores, revenue, revenue-per-store
  - A rightmost column sometimes carries investor logos

Linear text extraction loses the row structure (you get a flat stream
of numbers with no row anchor) and the LLM must guess which name goes
with which number row — frequently pulling fabricantes/manufacturers
mentioned elsewhere on the same page (e.g. the industry-pyramid on the
left of page 24 of the Projeto Frank CIM).

This parser:
  1. Finds numeric data rows by x-alignment of numeric tokens.
  2. Runs OCR on embedded logo images (via ocr_competitor_logos).
  3. Matches each logo to a row by y-coordinate proximity.
  4. Splits logos by x-center into "name column" vs "investor column"
     using the observed distribution of data-column x-centers.
  5. Emits one record per data row. Rows with no matched logo get a
     placeholder name ("Empresa não identificada #N") rather than an
     LLM-invented one — keeping the structural integrity (stores/revenue
     are still usable) without polluting the dossier with wrong names.

Returns None when page shape doesn't match (no data rows, or < 3 rows).
"""
from __future__ import annotations
import re
from collections import defaultdict

try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False

try:
    from .ocr_helper import ocr_competitor_logos, is_ocr_available
    HAS_OCR = True
except ImportError:
    HAS_OCR = False


# Matches integers with dot/comma thousand separators: 1.408, 1,251, 604
NUMERIC = re.compile(r"^[\d][\d.,]*$")
DASH = {"--", "—", "–", "-"}


def _rows_by_y(words, tol: int = 3):
    buckets = defaultdict(list)
    for w in words:
        buckets[round(w["top"] / tol) * tol].append(w)
    return [(y, sorted(buckets[y], key=lambda w: w["x0"])) for y in sorted(buckets)]


def _parse_number(text: str) -> float | None:
    """Convert '1.408', '1,251', '--', '0,6' to float (or None)."""
    t = text.strip().rstrip("³²¹⁴⁵").strip()
    if t in DASH or not t:
        return None
    # Brazilian format: dot thousand, comma decimal
    if "," in t and "." in t:
        # 1.408,50
        t = t.replace(".", "").replace(",", ".")
    elif "," in t:
        # 0,6 (decimal) or 1,251 (thousand). Heuristic: if fractional digits <= 2, decimal
        parts = t.split(",")
        if len(parts) == 2 and len(parts[1]) <= 2:
            t = t.replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "." in t:
        # 1.408 (thousand)
        parts = t.split(".")
        if len(parts) == 2 and len(parts[1]) == 3:
            t = t.replace(".", "")
    try:
        return float(t)
    except ValueError:
        return None


def _find_data_rows(rows, min_cols: int = 3):
    """Find rows that contain at least `min_cols` numeric/dash tokens aligned
    in the right half of the page (typical table layout).

    Returns list of (y, [word_dicts_of_numeric_cells_sorted_by_x]).
    """
    data_rows = []
    for y, ws in rows:
        numeric_words = [w for w in ws if NUMERIC.match(w["text"].rstrip("³²¹⁴⁵")) or w["text"] in DASH]
        if len(numeric_words) >= min_cols:
            # Filter out rows that are only small numbers on the left (e.g. page
            # numbers, small callouts) — require at least one token with x > 400.
            if any(w["x0"] > 400 for w in numeric_words):
                data_rows.append((y, sorted(numeric_words, key=lambda w: w["x0"])))
    return data_rows


def _match_logo_to_row(logo_y: float, data_rows, tol: float = 20.0) -> int | None:
    """Return the index of the data row closest in y to this logo, or None."""
    best = None
    best_dist = float("inf")
    for i, (y, _) in enumerate(data_rows):
        d = abs(y - logo_y)
        if d < best_dist:
            best_dist = d
            best = i
    return best if best_dist <= tol else None


def _clean_logo_text(text: str) -> str:
    """Clean common OCR artifacts in logo text."""
    t = text.strip()
    # All-caps runs without spaces → Title Case with spaces heuristic
    if t.isupper() and " " not in t and len(t) > 6:
        # OTICASCAROL → Óticas Carol-ish; too lossy to guess, keep as-is
        # but title-case so it reads as a name
        t = t.title()
    return t


def _split_logos_by_column(logos, data_rows):
    """Separate logos into (name_logos, investor_logos) by x-center.

    Strategy: look at the x-centers of data-row cells. The investor column
    is typically the rightmost column AND frequently has "--" dashes (many
    rows have no investor). We identify the investor-column x by finding
    the rightmost column x-center and using it as the investor anchor.
    Any logo whose x_center is at or beyond `primary_data_end + 30` is
    treated as an investor marker.
    """
    if not data_rows or not logos:
        return logos, []

    # All x-starts of data cells
    all_xs = sorted(w["x0"] for _, nums in data_rows for w in nums)
    if not all_xs:
        return logos, []

    # Find the transition point between primary metrics (stores/revenue/rev-per-store)
    # and the investor column. We cluster x-starts by 25px gaps and take the
    # LAST cluster as the investor column; the boundary sits halfway between
    # the last two clusters.
    clusters: list[list[float]] = [[all_xs[0]]]
    for x in all_xs[1:]:
        if x - clusters[-1][-1] <= 25.0:
            clusters[-1].append(x)
        else:
            clusters.append([x])

    if len(clusters) < 2:
        # No clear investor column on the page
        return logos, []

    last_col_x = sum(clusters[-1]) / len(clusters[-1])
    prev_col_x = sum(clusters[-2]) / len(clusters[-2])
    # Boundary: midpoint between last two column centers
    boundary = (last_col_x + prev_col_x) / 2

    name_logos, investor_logos = [], []
    for lg in logos:
        if lg["x_center"] >= boundary:
            investor_logos.append(lg)
        else:
            name_logos.append(lg)
    return name_logos, investor_logos


def parse_competitor_page(pdf_path: str, page_num: int, verbose: bool = False) -> list[dict] | None:
    """Extract a Top-N competitor table from a page.

    Returns a list of dicts: {name, stores, revenue, revenue_unit, investor}
    Row names come from OCR'd logos matched by y-position. Unmatched rows
    get a placeholder name rather than an invented one.

    Returns None if the page doesn't look like a ranking table.
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
            print(f"    ⚠️  competitor_parser: failed to read page {page_num}: {e}")
        return None

    if not words:
        return None

    rows = _rows_by_y(words)
    data_rows = _find_data_rows(rows)

    if len(data_rows) < 3:
        if verbose:
            print(f"    ⚠️  competitor_parser: only {len(data_rows)} data rows on page {page_num}")
        return None

    # OCR logos (optional)
    logos = []
    if HAS_OCR and is_ocr_available():
        try:
            logos = ocr_competitor_logos(pdf_path, page_num, verbose=False) or []
        except Exception as e:
            if verbose:
                print(f"    ⚠️  competitor_parser: OCR failed: {e}")

    name_logos, investor_logos = _split_logos_by_column(logos, data_rows)

    # Build one record per data row.
    # Step A: detect GLOBAL column x-centers by clustering x-positions across
    # all data rows. This is more robust than positional indexing per-row
    # because rows may have dashes ("--") that shift positional index.
    col_xs = sorted(w["x0"] for _, nums in data_rows for w in nums)

    def _cluster_xs(xs: list[float], gap: float = 25.0) -> list[float]:
        """Cluster nearby x-starts into column centers."""
        if not xs:
            return []
        clusters = [[xs[0]]]
        for x in xs[1:]:
            if x - clusters[-1][-1] <= gap:
                clusters[-1].append(x)
            else:
                clusters.append([x])
        return [sum(c) / len(c) for c in clusters]

    col_centers = _cluster_xs(col_xs, gap=25.0)
    if verbose:
        print(f"    competitor_parser: detected {len(col_centers)} columns at x={[round(c) for c in col_centers]}")

    def _cell_for_col(row_words, col_x: float, tol: float = 20.0):
        """Return the word in row_words closest to col_x, or None."""
        best, best_d = None, float("inf")
        for w in row_words:
            d = abs(w["x0"] - col_x)
            if d < best_d and d <= tol:
                best_d, best = d, w
        return best

    competitors = []
    for i, (y, nums) in enumerate(data_rows, start=1):
        # Attach name logo by y proximity
        matched_name = None
        for lg in name_logos:
            if abs(lg["y_center"] - y) <= 15.0:
                matched_name = _clean_logo_text(lg["text"])
                break

        matched_investor = None
        for lg in investor_logos:
            if abs(lg["y_center"] - y) <= 15.0:
                matched_investor = _clean_logo_text(lg["text"])
                break

        # Map each column center to the nearest word in this row, then parse.
        # Standard Top-N layout: [franqueados, proprias, TOTAL, REVENUE, rev/loja, (investor marker)]
        cells = [_cell_for_col(nums, cx) for cx in col_centers]
        parsed = [_parse_number(c["text"]) if c is not None else None for c in cells]

        stores = None
        revenue = None
        # Convention: col 2 (0-indexed) = total stores, col 3 = revenue (BRL MM)
        if len(parsed) >= 4:
            stores = int(parsed[2]) if parsed[2] is not None else None
            revenue = parsed[3]
        elif len(parsed) >= 2:
            # Fallback: first non-None is stores, next is revenue
            non_none = [p for p in parsed if p is not None]
            if non_none:
                stores = int(non_none[0])
                revenue = non_none[1] if len(non_none) > 1 else None

        competitors.append({
            "name": matched_name or f"Empresa não identificada #{i}",
            "stores": stores,
            "revenue": revenue,
            "revenue_unit": "BRL MM" if revenue else None,
            "investor": matched_investor,
            "_identified": matched_name is not None,
        })

    if verbose:
        identified = sum(1 for c in competitors if c["_identified"])
        print(f"    ✅ competitor_parser: {len(competitors)} rows on page {page_num} "
              f"({identified} identified via OCR logos)")

    return competitors