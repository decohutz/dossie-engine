"""
PDF page parser.
Extracts clean text content from each page, removes noise,
and produces ContentBlock objects ready for classification.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import re
import pdfplumber


@dataclass
class ContentBlock:
    """A block of content extracted from a single PDF page."""
    page_number: int
    raw_text: str
    clean_text: str
    tables_found: int = 0
    char_count: int = 0
    line_count: int = 0
    page_type: str = "content"  # "title" | "content" | "financial_table" | "separator" | "cover"
    first_heading: str = ""     # First meaningful line (used for quick identification)
    source_file: str = ""

    def to_dict(self) -> dict:
        return {
            "page_number": self.page_number,
            "clean_text": self.clean_text,
            "tables_found": self.tables_found,
            "char_count": self.char_count,
            "line_count": self.line_count,
            "page_type": self.page_type,
            "first_heading": self.first_heading,
            "source_file": self.source_file,
        }


# Patterns to remove from extracted text
_NOISE_PATTERNS = [
    r'#[0-9A-Fa-f]{6}',                    # Color codes: #009AD0
    r'PRIVATE AND CONFIDENTIAL',            # Header
    r'Cópia para Trigger.*',                # Watermark
    r'[<>]?heitor.*?divulgação',            # Watermark variant
    r'arthur\s*Hutzler.*',                  # Watermark variant
    r'@trigger\.com\.br\??',               # Watermark email
    r'proibida\s*divulgação',              # Watermark fragment
    r'Fontes:.*$',                          # Source references (keep on same line)
    r'Notas:.*$',                           # Notes
]

# Compiled for performance
_NOISE_COMPILED = [re.compile(p, re.IGNORECASE) for p in _NOISE_PATTERNS]


def _clean_text(raw_text: str) -> str:
    """Remove noise patterns and clean up whitespace."""
    lines = raw_text.split("\n")
    clean_lines = []

    for line in lines:
        cleaned = line.strip()
        if not cleaned:
            continue

        # Apply noise removal
        for pattern in _NOISE_COMPILED:
            cleaned = pattern.sub("", cleaned).strip()

        # Skip if line became empty or is just a page number
        if not cleaned:
            continue
        if re.match(r'^\d{1,3}$', cleaned):
            continue

        clean_lines.append(cleaned)

    return "\n".join(clean_lines)


def _classify_page(clean_text: str, tables_found: int, line_count: int) -> str:
    """Classify a page into a type based on its content.

    The page_type drives a downstream decision in the classifier: pages
    typed as ``separator`` or ``title`` are skipped from chapter routing
    (they exist only to mark section boundaries in the source PDF).

    The rules below intentionally bias toward ``content`` when in doubt.
    Earlier versions of this function aggressively promoted any page with
    ≤5 lines of extracted text to ``title``, which worked for text-rich
    CIMs (Frank's deck has 5+ lines on every substantive page) but was
    catastrophic for visually-dense pitch decks (Bioma's product pages
    have 1-5 lines of text on top of large images, and were all silently
    discarded). Only pages with no extractable text, or with a one-word
    title-card vibe, should bypass the classifier.
    """
    text = clean_text.strip()

    # Empty page → separator (probably a divider slide rendered as image).
    if line_count == 0:
        return "separator"

    # Numbered section page like "2. Mercado Óptico" with at most 2 lines.
    if line_count <= 2 and not tables_found and re.match(r'^[\d.]+\s', text):
        return "separator"

    # Closing/agenda dividers — pages whose entire content is a single
    # navigational marker. Match exact tokens, not substrings, to avoid
    # eating real content that mentions these words.
    if line_count <= 2 and not tables_found:
        first_word = re.sub(r"[^a-zA-ZÀ-ÿ]", "", text.split()[0]) if text.split() else ""
        if first_word.lower() in {"obrigado", "thanks", "agenda", "índice", "sumário"}:
            return "separator"

    # Financial table pages — preserved exactly as before.
    if re.search(r'Demonstração de Resultados|Balanço Patrimonial', clean_text):
        if re.search(r'20\d{2}E?\s+20\d{2}', clean_text):
            return "financial_table"

    # Genuinely title-card pages: a single line, very short, no table.
    # Keep this conservative — better to over-classify as content and
    # let the classifier filter than to silently discard text we have.
    if line_count == 1 and tables_found == 0 and len(text) < 30:
        return "title"

    return "content"


def _extract_first_heading(clean_text: str) -> str:
    """Extract the first meaningful heading from clean text."""
    for line in clean_text.split("\n"):
        line = line.strip()
        # Skip very short lines
        if len(line) < 5:
            continue
        # Skip lines that are mostly numbers
        if re.match(r'^[\d\s.,%()\-]+$', line):
            continue
        return line[:120]
    return ""


def parse_pdf(file_path: str) -> list[ContentBlock]:
    """Parse all pages of a PDF into ContentBlocks.

    Args:
        file_path: Path to the PDF file

    Returns:
        List of ContentBlock, one per page
    """
    pdf = pdfplumber.open(file_path)
    blocks: list[ContentBlock] = []
    source_name = file_path.split("/")[-1].split("\\")[-1]

    for i, page in enumerate(pdf.pages):
        page_num = i + 1
        raw_text = page.extract_text() or ""
        tables = page.extract_tables() or []
        clean = _clean_text(raw_text)
        lines = [l for l in clean.split("\n") if l.strip()]

        page_type = _classify_page(clean, len(tables), len(lines))
        heading = _extract_first_heading(clean)

        blocks.append(ContentBlock(
            page_number=page_num,
            raw_text=raw_text,
            clean_text=clean,
            tables_found=len(tables),
            char_count=len(clean),
            line_count=len(lines),
            page_type=page_type,
            first_heading=heading,
            source_file=source_name,
        ))

    pdf.close()
    return blocks


def print_blocks_summary(blocks: list[ContentBlock]) -> None:
    """Print a summary of all parsed blocks."""
    type_counts = {}
    for b in blocks:
        type_counts[b.page_type] = type_counts.get(b.page_type, 0) + 1

    print(f"\n{'=' * 70}")
    print(f"  PDF PARSING REPORT: {blocks[0].source_file if blocks else 'N/A'}")
    print(f"  {len(blocks)} pages | Types: {type_counts}")
    print(f"{'=' * 70}")

    icons = {
        "content": "📝",
        "financial_table": "📊",
        "title": "📌",
        "separator": "➖",
        "cover": "🔒",
    }

    for b in blocks:
        icon = icons.get(b.page_type, "❓")
        heading = b.first_heading[:65] if b.first_heading else "(empty)"
        print(f"  {icon} Pág {b.page_number:2d} [{b.page_type:16s}] {b.line_count:3d} lines | {heading}")