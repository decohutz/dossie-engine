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
# SPATIAL OCR — COMPETITOR LOGOS
# ═══════════════════════════════════════════════════════════════
def ocr_competitor_logos(
    pdf_path: str, page_num: int, lang: str = "por+eng", verbose: bool = False,
) -> list[dict] | None:
    """Extract text from embedded images (logos) on a competitor page.

    Strategy:
    1. Find all embedded image objects on the page via pdfplumber
    2. Render the page at high DPI
    3. Crop each image region, preprocess (contrast + threshold), and OCR
    4. Return list of {x_center, y_center, text, bbox} sorted left-to-right

    Returns None if OCR is unavailable or no images found.
    """
    if not HAS_OCR or not HAS_PDFPLUMBER:
        return None

    try:
        with pdfplumber.open(pdf_path) as pdf:
            if page_num < 1 or page_num > len(pdf.pages):
                return None

            page = pdf.pages[page_num - 1]
            pw, ph = float(page.width), float(page.height)

            # Find embedded images on the page
            images = page.images or []
            if not images and verbose:
                print(f"    ⚠️  No embedded images on page {page_num}")
                return None

            # Render page at high resolution for better OCR
            dpi = 300
            page_img = page.to_image(resolution=dpi)
            pil_page = page_img.original
            img_w, img_h = pil_page.size
            scale_x = img_w / pw
            scale_y = img_h / ph

            # Get existing text to filter out already-extracted content
            existing_text = (page.extract_text() or "").lower()

            results = []
            for im in images:
                # Image bbox in PDF coordinates
                x0 = float(im.get("x0", 0))
                y0 = float(im.get("top", 0))
                x1 = float(im.get("x1", x0 + 50))
                y1 = float(im.get("bottom", y0 + 50))

                # Skip tiny images (decorations, icons, dividers)
                w = x1 - x0
                h = y1 - y0
                if w < 20 or h < 15:
                    continue
                # Skip images that span most of the page width (backgrounds)
                if w > pw * 0.8:
                    continue

                # Convert to pixel coordinates with padding
                pad = 10  # pixels
                px0 = max(0, int(x0 * scale_x) - pad)
                py0 = max(0, int(y0 * scale_y) - pad)
                px1 = min(img_w, int(x1 * scale_x) + pad)
                py1 = min(img_h, int(y1 * scale_y) + pad)

                if px1 - px0 < 20 or py1 - py0 < 15:
                    continue

                # Crop the image region
                crop = pil_page.crop((px0, py0, px1, py1))

                # Preprocess for better OCR: grayscale → contrast → threshold
                crop = _preprocess_logo(crop)

                # OCR the cropped region
                try:
                    text = pytesseract.image_to_string(
                        crop, lang=lang,
                        config="--psm 7 --oem 3",  # PSM 7 = single line
                    ).strip()
                except Exception:
                    try:
                        # Fallback: try PSM 6 (block of text)
                        text = pytesseract.image_to_string(
                            crop, lang=lang,
                            config="--psm 6 --oem 3",
                        ).strip()
                    except Exception:
                        continue

                # Clean up OCR result
                text = _clean_ocr_text(text)

                if text and len(text) >= 2:
                    # Skip if this text was already in pdfplumber extraction
                    if text.lower() in existing_text:
                        continue
                    if _fuzzy_match(text.lower(), existing_text, threshold=0.8):
                        continue

                    results.append({
                        "x_center": (x0 + x1) / 2,
                        "y_center": (y0 + y1) / 2,
                        "text": text,
                        "bbox": (x0, y0, x1, y1),
                        "width": w,
                        "height": h,
                    })

            # Sort left-to-right
            results.sort(key=lambda r: r["x_center"])

            # Deduplicate overlapping results
            results = _dedup_ocr_results(results)

            if verbose and results:
                print(f"    🏷️  OCR logos page {page_num}: {[r['text'] for r in results]}")

            return results if results else None

    except Exception as e:
        if verbose:
            print(f"    ⚠️  Logo OCR failed for page {page_num}: {e}")
        return None


def ocr_column_strips(
    pdf_path: str, page_num: int, n_columns: int = 5,
    logo_y_fraction: float = 0.45, lang: str = "por+eng",
    verbose: bool = False,
) -> list[dict] | None:
    """Fallback: divide the top portion of the page into N equal vertical strips and OCR each.

    Useful when embedded images aren't detected (e.g. the logo is part of a larger
    background image or is a vector graphic).

    Args:
        pdf_path: Path to PDF
        page_num: 1-based page number
        n_columns: Expected number of competitor columns
        logo_y_fraction: How much of the page height to scan for logos (top portion)
        lang: Tesseract language
        verbose: Print debug info

    Returns:
        List of {column_index, x_center, text} or None
    """
    if not HAS_OCR or not HAS_PDFPLUMBER:
        return None

    try:
        with pdfplumber.open(pdf_path) as pdf:
            if page_num < 1 or page_num > len(pdf.pages):
                return None

            page = pdf.pages[page_num - 1]

            # Render page
            dpi = 300
            page_img = page.to_image(resolution=dpi)
            pil_page = page_img.original
            img_w, img_h = pil_page.size

            # Existing text for filtering
            existing_text = (page.extract_text() or "").lower()

            # Scan the top portion of the page in vertical strips
            logo_h = int(img_h * logo_y_fraction)
            strip_w = img_w // n_columns

            results = []
            for i in range(n_columns):
                x0 = i * strip_w
                x1 = min((i + 1) * strip_w, img_w)

                crop = pil_page.crop((x0, 0, x1, logo_h))
                crop = _preprocess_logo(crop)

                try:
                    text = pytesseract.image_to_string(
                        crop, lang=lang,
                        config="--psm 6 --oem 3",
                    ).strip()
                except Exception:
                    continue

                # Extract candidate company names from OCR text
                names = _extract_company_names(text, existing_text)

                if names:
                    results.append({
                        "column_index": i,
                        "x_center": (x0 + x1) / 2 / (img_w / float(page.width)),
                        "text": names[0],  # Best candidate
                        "all_candidates": names,
                    })

            if verbose and results:
                print(f"    🏷️  Column strip OCR page {page_num}: {[r['text'] for r in results]}")

            return results if results else None

    except Exception as e:
        if verbose:
            print(f"    ⚠️  Column OCR failed for page {page_num}: {e}")
        return None


def _preprocess_logo(img) -> "Image":
    """Preprocess a cropped logo image for better OCR.

    Steps: convert to grayscale, enhance contrast, apply threshold.
    """
    try:
        from PIL import ImageEnhance, ImageFilter

        # Convert to grayscale
        gray = img.convert("L")

        # Enhance contrast
        enhancer = ImageEnhance.Contrast(gray)
        gray = enhancer.enhance(2.0)

        # Sharpen
        gray = gray.filter(ImageFilter.SHARPEN)

        # Binarize: threshold at 128
        gray = gray.point(lambda x: 255 if x > 128 else 0, mode="1")

        return gray

    except ImportError:
        return img.convert("L") if hasattr(img, "convert") else img


def _clean_ocr_text(text: str) -> str:
    """Clean up raw OCR text from a logo region."""
    if not text:
        return ""

    # Remove common OCR noise
    text = re.sub(r"[|\\/_{}()\[\]<>~`@#$^&*+=]", "", text)
    # Remove lines that are just numbers or single chars
    lines = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Skip pure numbers (these are the data values, not names)
        if re.match(r"^[\d.,\s%BRL]+$", line):
            continue
        # Skip very short fragments
        if len(line) < 2:
            continue
        lines.append(line)

    return " ".join(lines).strip()


def _extract_company_names(ocr_text: str, existing_text: str) -> list[str]:
    """Extract potential company names from OCR text block.

    Filters out noise, numbers, and text already in the PDF text layer.
    Returns candidate names sorted by likelihood.
    """
    if not ocr_text:
        return []

    candidates = []
    for line in ocr_text.split("\n"):
        line = line.strip()
        if not line or len(line) < 3:
            continue

        # Clean
        line = re.sub(r"[|\\/_{}()\[\]<>~`@#$^&*+=]", "", line).strip()

        # Skip pure numbers
        if re.match(r"^[\d.,\s%BRLMmKk]+$", line):
            continue
        # Skip common noise words
        noise = {"investidor", "lojas", "faturamento", "receita", "companhia",
                 "empresa", "grupo", "rede", "top", "fonte", "unidades",
                 "varejo", "óptico", "loja", "mil", "bilhão", "milhão",
                 "posição", "mercado", "setor"}
        if line.lower() in noise:
            continue
        # Skip if already in PDF text
        if line.lower() in existing_text:
            continue

        # Prefer capitalized words (brand names are usually Title Case or ALL CAPS)
        if line[0].isupper() or line.isupper():
            candidates.insert(0, line)
        else:
            candidates.append(line)

    return candidates


def _dedup_ocr_results(results: list[dict], x_tolerance: float = 30) -> list[dict]:
    """Remove duplicate OCR results that are too close together spatially."""
    if len(results) <= 1:
        return results

    deduped = [results[0]]
    for r in results[1:]:
        # Check if too close to any existing result
        too_close = False
        for existing in deduped:
            if abs(r["x_center"] - existing["x_center"]) < x_tolerance:
                # Keep the one with longer text (more likely to be correct)
                if len(r["text"]) > len(existing["text"]):
                    deduped.remove(existing)
                    deduped.append(r)
                too_close = True
                break
        if not too_close:
            deduped.append(r)

    deduped.sort(key=lambda r: r["x_center"])
    return deduped


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