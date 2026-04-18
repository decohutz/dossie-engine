"""
Enhanced PDF extraction helpers.

- Layout-aware text extraction: preserves columnar structure (for executives page)
- OCR on page images: captures text from logos/images (for competitors page)

Requires: pytesseract, Pillow, pdfplumber
Optional: tesseract-ocr system binary
"""
from __future__ import annotations
import os
import re
from collections import defaultdict

try:
    import pdfplumber
    HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False

try:
    import pytesseract
    from PIL import Image
    HAS_OCR = True
except ImportError:
    HAS_OCR = False


# ── Tesseract path auto-detection (Windows) ──────────────────
_TESSERACT_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    r"C:\Users\win\AppData\Local\Programs\Tesseract-OCR\tesseract.exe",
]

def _configure_tesseract():
    """Auto-detect Tesseract binary on Windows."""
    if not HAS_OCR:
        return
    # Check if tesseract is already in PATH
    try:
        pytesseract.get_tesseract_version()
        return
    except Exception:
        pass
    # Try common Windows paths
    for path in _TESSERACT_PATHS:
        if os.path.exists(path):
            pytesseract.pytesseract.tesseract_cmd = path
            return

_configure_tesseract()


# ═══════════════════════════════════════════════════════════════
# LAYOUT-AWARE EXTRACTION (for columnar pages like executives)
# ═══════════════════════════════════════════════════════════════
def extract_layout_text(pdf_path: str, page_num: int, verbose: bool = False) -> str | None:
    """Extract text from a PDF page preserving columnar layout.

    Uses pdfplumber's word-level extraction with coordinates to reconstruct
    the spatial arrangement of text on the page.

    Args:
        pdf_path: Path to the PDF file
        page_num: 1-based page number
        verbose: Print debug info

    Returns:
        Structured text showing columns, or None if extraction fails
    """
    if not HAS_PDFPLUMBER:
        return None

    try:
        with pdfplumber.open(pdf_path) as pdf:
            if page_num < 1 or page_num > len(pdf.pages):
                return None

            page = pdf.pages[page_num - 1]
            words = page.extract_words(
                keep_blank_chars=True,
                x_tolerance=3,
                y_tolerance=3,
                extra_attrs=["fontname", "size"],
            )

            if not words:
                return None

            return _reconstruct_columns(words, verbose)

    except Exception as e:
        if verbose:
            print(f"    ⚠️  Layout extraction failed for page {page_num}: {e}")
        return None


def _reconstruct_columns(words: list[dict], verbose: bool = False) -> str:
    """Reconstruct columnar text from word positions.

    Groups words into rows by y-coordinate, then detects column boundaries
    based on x-coordinate gaps.
    """
    if not words:
        return ""

    # Step 1: Group words into rows by y-coordinate (top)
    row_tolerance = 5  # pixels
    rows = defaultdict(list)

    for w in words:
        y = round(w["top"] / row_tolerance) * row_tolerance
        rows[y].append(w)

    # Sort rows by y, words within each row by x
    sorted_rows = []
    for y in sorted(rows.keys()):
        row_words = sorted(rows[y], key=lambda w: w["x0"])
        sorted_rows.append((y, row_words))

    # Step 2: Detect column boundaries
    # Look for consistent x-gaps across multiple rows
    all_x_starts = []
    for _, row_words in sorted_rows:
        for w in row_words:
            all_x_starts.append(round(w["x0"]))

    # Find column boundaries: cluster x-starts
    col_boundaries = _detect_columns(all_x_starts, min_gap=50)

    if verbose:
        print(f"    Detected {len(col_boundaries)} columns at x={col_boundaries}")

    # Step 3: Format output
    output_lines = []

    for y, row_words in sorted_rows:
        if len(col_boundaries) > 1:
            # Multi-column: assign each word to a column
            columns = {b: [] for b in col_boundaries}
            for w in row_words:
                # Find closest column boundary
                closest = min(col_boundaries, key=lambda b: abs(w["x0"] - b))
                columns[closest].append(w["text"])

            # Format as "COL1 | COL2 | COL3"
            col_texts = []
            for b in sorted(col_boundaries):
                text = " ".join(columns[b]).strip()
                if text:
                    col_texts.append(text)
            if col_texts:
                output_lines.append(" | ".join(col_texts))
        else:
            # Single column: just join words
            text = " ".join(w["text"] for w in row_words).strip()
            if text:
                output_lines.append(text)

    return "\n".join(output_lines)


def _detect_columns(x_starts: list[int], min_gap: int = 50) -> list[int]:
    """Detect column boundaries from a list of x-coordinates.

    Clusters x-coordinates and returns the representative x for each cluster.
    """
    if not x_starts:
        return [0]

    # Sort and find gaps
    sorted_x = sorted(set(x_starts))
    if len(sorted_x) < 2:
        return sorted_x

    clusters = [[sorted_x[0]]]
    for x in sorted_x[1:]:
        if x - clusters[-1][-1] > min_gap:
            clusters.append([x])
        else:
            clusters[-1].append(x)

    # Return median of each cluster
    boundaries = []
    for cluster in clusters:
        # Only keep clusters that appear in multiple rows (real columns)
        freq = sum(1 for xs in x_starts if any(abs(xs - c) < 10 for c in cluster))
        if freq >= 3:  # Appears in at least 3 rows
            boundaries.append(int(sum(cluster) / len(cluster)))

    return boundaries if boundaries else [0]


# ═══════════════════════════════════════════════════════════════
# OCR ON PAGE IMAGES (for logos, watermarks, image-based text)
# ═══════════════════════════════════════════════════════════════
def ocr_page(pdf_path: str, page_num: int, lang: str = "por+eng", verbose: bool = False) -> str | None:
    """Run OCR on a PDF page to extract text from images.

    Renders the page to an image, runs Tesseract OCR, and returns
    text that was NOT already in the pdfplumber text extraction.

    Args:
        pdf_path: Path to the PDF file
        page_num: 1-based page number
        lang: Tesseract language(s) for OCR
        verbose: Print debug info

    Returns:
        OCR-extracted text (only new text not in pdfplumber), or None
    """
    if not HAS_OCR or not HAS_PDFPLUMBER:
        if verbose:
            if not HAS_OCR:
                print("    ⚠️  pytesseract not installed, skipping OCR")
            if not HAS_PDFPLUMBER:
                print("    ⚠️  pdfplumber not installed, skipping OCR")
        return None

    try:
        with pdfplumber.open(pdf_path) as pdf:
            if page_num < 1 or page_num > len(pdf.pages):
                return None

            page = pdf.pages[page_num - 1]

            # Get text that pdfplumber already extracted
            existing_text = (page.extract_text() or "").lower()

            # Render page to image
            img = page.to_image(resolution=200)
            pil_image = img.original

            # Run OCR
            try:
                ocr_text = pytesseract.image_to_string(pil_image, lang=lang)
            except pytesseract.TesseractNotFoundError:
                if verbose:
                    print("    ⚠️  Tesseract binary not found. Install: https://github.com/UB-Mannheim/tesseract/wiki")
                return None

            if not ocr_text or not ocr_text.strip():
                return None

            # Filter: only keep lines that are NEW (not already in pdfplumber text)
            new_lines = []
            for line in ocr_text.split("\n"):
                line = line.strip()
                if not line or len(line) < 3:
                    continue
                # Check if this line (or a close match) exists in pdfplumber text
                line_lower = line.lower()
                if line_lower not in existing_text and not _fuzzy_match(line_lower, existing_text):
                    new_lines.append(line)

            if new_lines:
                result = "\n".join(new_lines)
                if verbose:
                    print(f"    🔍 OCR found {len(new_lines)} new text lines on page {page_num}")
                return result

    except Exception as e:
        if verbose:
            print(f"    ⚠️  OCR failed for page {page_num}: {e}")

    return None


def ocr_pages(pdf_path: str, page_nums: list[int], lang: str = "por+eng", verbose: bool = False) -> dict[int, str]:
    """Run OCR on multiple pages. Returns {page_num: ocr_text}."""
    results = {}
    for pn in page_nums:
        text = ocr_page(pdf_path, pn, lang, verbose)
        if text:
            results[pn] = text
    return results


def _fuzzy_match(needle: str, haystack: str, threshold: float = 0.7) -> bool:
    """Check if needle appears approximately in haystack.

    Uses word overlap instead of exact substring match.
    """
    if len(needle) < 4:
        return needle in haystack

    needle_words = set(needle.split())
    if not needle_words:
        return False

    matches = sum(1 for w in needle_words if w in haystack)
    return (matches / len(needle_words)) >= threshold


# ═══════════════════════════════════════════════════════════════
# CONVENIENCE: check availability
# ═══════════════════════════════════════════════════════════════
def is_ocr_available() -> bool:
    """Check if OCR is available (pytesseract + tesseract binary)."""
    if not HAS_OCR:
        return False
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def is_layout_available() -> bool:
    """Check if layout extraction is available (pdfplumber)."""
    return HAS_PDFPLUMBER