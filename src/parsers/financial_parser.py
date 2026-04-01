"""
Financial statement parser.
Parses the raw text extracted from PDF financial pages (DRE, Balance Sheet)
into structured FinancialStatement objects.

Strategy: The text extraction from pdfplumber gives us well-structured lines like:
    Receita Bruta 22.575 30.838 39.512 38.561 ...
    (-) Impostos e Devoluções (1.896) (2.201) ...

We parse these with regex, handling:
- Brazilian number format: 22.575 = 22575 (dot as thousands separator)
- Negative values in parentheses: (1.896) = -1896
- Percentages: 37,9% (comma as decimal separator)
- Multi-line labels that get split by pdfplumber
- "--" as zero/not-applicable
"""
from __future__ import annotations
import re
from ..models.evidence import Evidence
from ..models.financials import FinancialLine, FinancialStatement


# Regex for extracting numbers from financial text
_NUM_PATTERN = re.compile(
    r'\([\d.]+\)'        # negative: (1.896)
    r'|[\d]+[.,][\d]+%'  # percentage: 37,9%
    r'|[\d]+\.[\d.]+'    # thousands: 22.575 or 8.140.000
    r'|[\d]+'            # plain integer
    r'|--'               # not applicable
    r'|n\.a\.'           # not applicable variant
)

# Lines to skip
_SKIP_PATTERNS = [
    r'^#[0-9A-Fa-f]{6}',     # Color codes (#009AD0)
    r'^Fontes:',              # Source references
    r'^Notas:',               # Notes
    r'^\d+$',                 # Page numbers
    r'^PRIVATE AND',          # Header
    r'^Cópia para',           # Watermark
    r'^Análise Value',        # Source label
]


def parse_brazilian_number(text: str) -> float | None:
    """Parse a number in Brazilian financial format."""
    text = text.strip()

    if text in ("--", "—", "-", ""):
        return 0.0

    if text in ("n.a.", "n/a", "N/A"):
        return None

    # Percentage: 37,9%
    if text.endswith("%"):
        num_str = text[:-1].replace(",", ".")
        try:
            return float(num_str) / 100.0
        except ValueError:
            return None

    # Negative in parentheses: (1.896)
    is_negative = False
    if text.startswith("(") and text.endswith(")"):
        is_negative = True
        text = text[1:-1]

    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(".", "")

    try:
        value = float(text)
        return -value if is_negative else value
    except ValueError:
        return None


def _should_skip_line(line: str) -> bool:
    for pattern in _SKIP_PATTERNS:
        if re.match(pattern, line.strip()):
            return True
    return False


def _extract_header_years(line: str) -> list[str]:
    year_pattern = re.compile(r'20\d{2}E?')
    return year_pattern.findall(line)


def _is_percentage_line(values: list[float | None]) -> bool:
    """Detect if a line contains percentages (all values between -1 and 1, non-zero)."""
    non_zero = [v for v in values if v is not None and v != 0.0]
    if not non_zero:
        return False
    return all(-1.0 <= v <= 1.0 for v in non_zero)


def _parse_financial_line(line: str, num_years: int) -> tuple[str, list[float | None], bool] | None:
    """Parse a single financial line into (label, values, is_percentage).

    Returns None if the line doesn't have enough numbers.
    """
    numbers = _NUM_PATTERN.findall(line)

    if len(numbers) < num_years:
        return None

    # Take the last num_years numbers as values
    value_strings = numbers[-num_years:]
    values = [parse_brazilian_number(n) for n in value_strings]
    is_pct = _is_percentage_line(values)

    # Everything before the first matched value is the label
    first_value = value_strings[0]
    idx = line.find(first_value)
    if idx <= 0:
        return None

    label = line[:idx].strip()
    if not label:
        return None

    return (label, values, is_pct)


def parse_financial_text(
    text: str,
    entity_name: str,
    statement_type: str,
    source_file: str = "",
    page: int | None = None,
) -> FinancialStatement:
    """Parse raw text from a financial page into a FinancialStatement."""
    lines_raw = text.split("\n")
    lines = [l.strip() for l in lines_raw if l.strip() and not _should_skip_line(l)]

    # Step 1: Find the header line with years
    years: list[str] = []
    header_idx = -1
    for i, line in enumerate(lines):
        found_years = _extract_header_years(line)
        if len(found_years) >= 5:
            years = found_years
            header_idx = i
            break

    if not years:
        return FinancialStatement(
            entity_name=entity_name,
            statement_type=statement_type,
            evidence=Evidence(source_file=source_file, page=page,
                              excerpt="No year header found", confidence=0.0),
        )

    num_years = len(years)
    is_projected = {y: y.endswith("E") for y in years}

    # Step 2: Parse each line after the header
    parsed_lines: list[FinancialLine] = []
    pending_label = ""

    for line in lines[header_idx + 1:]:
        result = _parse_financial_line(line, num_years)

        if result:
            label, values, is_pct = result

            # If we had a pending partial label, prepend it
            if pending_label:
                label = f"{pending_label} {label}".strip()
                pending_label = ""

            # Build the values dict
            values_dict: dict[str, float] = {}
            proj_dict: dict[str, bool] = {}
            for year, val in zip(years, values):
                if val is not None:
                    values_dict[year] = val
                    proj_dict[year] = is_projected[year]

            if values_dict:
                parsed_lines.append(FinancialLine(
                    label=label,
                    values=values_dict,
                    is_projected=proj_dict,
                    unit="%" if is_pct else "BRL k",
                    evidence=Evidence(
                        source_file=source_file,
                        page=page,
                        excerpt=line[:200],
                        confidence=0.9,
                        extraction_method="text_parse",
                    ),
                ))
        else:
            # Line with no numbers — might be a continuation of a label
            cleaned = line.strip()
            if cleaned and not _should_skip_line(cleaned) and len(cleaned) > 2:
                # Don't accumulate lines that look like they contain stray values
                # from a broken multi-line parse
                if not re.search(r'\(\d', cleaned):
                    if pending_label:
                        pending_label = f"{pending_label} {cleaned}"
                    else:
                        pending_label = cleaned

    return FinancialStatement(
        entity_name=entity_name,
        statement_type=statement_type,
        lines=parsed_lines,
        years=years,
        evidence=Evidence(
            source_file=source_file,
            page=page,
            excerpt=f"Parsed {len(parsed_lines)} lines for {entity_name} {statement_type}",
            confidence=0.85,
            extraction_method="text_parse",
        ),
    )


def print_statement_summary(stmt: FinancialStatement) -> None:
    """Print a human-readable summary of a financial statement."""
    print(f"\n{'=' * 70}")
    print(f"  {stmt.entity_name} — {stmt.statement_type.upper()}")
    print(f"  {len(stmt.lines)} lines | Years: {', '.join(stmt.years)}")
    print(f"{'=' * 70}")

    for line in stmt.lines:
        label = line.label[:40].ljust(42)
        vals = []
        for year in stmt.years:
            v = line.values.get(year)
            if v is None:
                vals.append("     ---")
            elif line.unit == "%":
                vals.append(f"{v * 100:7.1f}%")
            else:
                vals.append(f"{v:>8,.0f}")
        print(f"  {label} {'  '.join(vals)}")