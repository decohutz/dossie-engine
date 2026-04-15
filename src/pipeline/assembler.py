"""
Dossier assembler.
Generates Markdown and JSON output from a populated Dossier.
"""
from __future__ import annotations
import json
import re
from ..models.dossier import Dossier


def _clean_label(label: str) -> str:
    """Clean DRE/balance labels that merged two lines from the PDF parser.

    Fixes cases like:
    - "(+/-) Outras Receitas/Despesas -- -- -- 2 31 -- -- -- -- -- Operacionais (=) EBITDA"
    - "(+/-) Outras Receitas/Despesas Não -- -- -- 75 10 -- -- -- -- -- Operacionais"
    """
    # Remove embedded numeric values that leaked from data columns
    label = re.sub(r'\s+--(\s+--)*\s*', ' ', label)
    label = re.sub(r'\s+\d+(\s+\d+)*\s+', ' ', label)

    # If label contains (=) or (+/-) and is too long, it merged two lines
    if len(label) > 50:
        match = re.search(r'(\([=+/-]+\)\s*\w[\w\s&]*?)$', label)
        if match:
            label = match.group(1).strip()

    # Remove stray fragments
    label = re.sub(r'\bOpera\w*\s*', '', label)
    label = re.sub(r'\bNão\s*$', '', label)

    return re.sub(r'\s+', ' ', label).strip()


def _fmt_cagr(cagr) -> str:
    """Format CAGR auto-detecting decimal vs percentage.

    0.033 → '3.3%'  (decimal, multiply by 100)
    3.3   → '3.3%'  (already percentage, keep as is)
    """
    if cagr is None:
        return ""
    cagr_pct = cagr * 100 if cagr < 1 else cagr
    return f" (CAGR {cagr_pct:.1f}%)"


def to_json(dossier: Dossier, indent: int = 2) -> str:
    return json.dumps(dossier.to_dict(), ensure_ascii=False, indent=indent, default=str)


def to_markdown(dossier: Dossier) -> str:
    lines: list[str] = []
    m = dossier.metadata
    lines.append(f"# Dossiê: {m.project_name}")
    lines.append(f"**Empresa:** {m.target_company}")
    lines.append(f"**Versão:** {m.version} | **Gerado em:** {m.created_at[:10]}")
    lines.append(f"**Fontes:** {', '.join(m.source_files)}")
    lines.append("")

    # --- Summary ---
    s = dossier.summary()
    lines.append("## Resumo de completude")
    lines.append("")
    lines.append(f"| Indicador | Valor |")
    lines.append(f"|-----------|-------|")
    for key, val in s.items():
        if key in ("project", "company", "version"):
            continue
        label = key.replace("_", " ").title()
        lines.append(f"| {label} | {val} |")
    lines.append("")

    # --- Company ---
    lines.append("## 1. Sobre a empresa")
    lines.append("")
    p = dossier.company.profile
    for field_name in p.__dataclass_fields__:
        tf = getattr(p, field_name)
        if tf.is_filled:
            label = field_name.replace("_", " ").title()
            lines.append(f"- **{label}:** {tf.value}")
    lines.append("")

    if dossier.company.timeline:
        lines.append("### Histórico")
        lines.append("")
        for event in sorted(dossier.company.timeline, key=lambda e: e.year):
            lines.append(f"- **{event.year}:** {event.description}")
        lines.append("")

    if dossier.company.shareholders:
        lines.append("### Sócios / Acionistas")
        lines.append("")
        lines.append("| Nome | Cargo | Participação |")
        lines.append("|------|-------|-------------|")
        for sh in dossier.company.shareholders:
            pct = f"{sh.ownership_pct:.0f}%" if sh.ownership_pct else "N/D"
            lines.append(f"| {sh.name} | {sh.role or 'N/D'} | {pct} |")
        lines.append("")

    if dossier.company.executives:
        lines.append("### Diretoria")
        lines.append("")
        lines.append("| Nome | Cargo | Anos no grupo | Participação |")
        lines.append("|------|-------|--------------|-------------|")
        for ex in dossier.company.executives:
            pct = f"{ex.ownership_pct:.0f}%" if ex.ownership_pct else "-"
            tenure = f"{ex.tenure_years}+" if ex.tenure_years else "N/D"
            lines.append(f"| {ex.name} | {ex.role or '—'} | {tenure} | {pct} |")
        lines.append("")

    if dossier.company.products:
        lines.append("### Produtos e marcas")
        lines.append("")
        for prod in dossier.company.products:
            prop = " *(marca própria)*" if prod.is_proprietary else ""
            share = f" — {prod.revenue_share_pct:.1f}% do sell-out" if prod.revenue_share_pct else ""
            lines.append(f"- **{prod.name}** ({prod.category}){share}{prop}")
        lines.append("")

    # --- Financials ---
    lines.append("## 2. Informações financeiras")
    lines.append("")

    for attr_name, label in [
        ("dre_franqueadora", "DRE Franqueadora"),
        ("dre_distribuidora", "DRE Distribuidora"),
        ("dre_lojas_proprias", "DRE Lojas Próprias"),
    ]:
        stmt = getattr(dossier.financials, attr_name)
        if stmt and stmt.lines:
            lines.append(f"### {label}")
            lines.append("")
            # Header
            years = stmt.years
            lines.append("| Linha | " + " | ".join(years) + " |")
            lines.append("|-------|" + "|".join(["-------:" for _ in years]) + "|")
            for fl in stmt.lines:
                clean = _clean_label(fl.label)
                vals = []
                for y in years:
                    v = fl.values.get(y)
                    if v is None:
                        vals.append("-")
                    elif fl.unit == "%":
                        vals.append(f"{v*100:.1f}%")
                    else:
                        vals.append(f"{v:,.0f}")
                lines.append(f"| {clean[:45]} | " + " | ".join(vals) + " |")
            lines.append("")

    # --- Market ---
    lines.append("## 3. Mercado")
    lines.append("")

    if dossier.market.market_sizes:
        lines.append("### Tamanho de mercado")
        lines.append("")
        for ms in dossier.market.market_sizes:
            cagr = _fmt_cagr(ms.cagr)
            value_str = f"{ms.value:.1f}" if ms.value is not None else "N/A"
            unit_str = ms.unit or ""
            geo_str = ms.geography or "N/A"
            lines.append(f"- **{geo_str}:** {unit_str} {value_str} ({ms.year}){cagr}")
        lines.append("")

    if dossier.market.market_fragmentation.is_filled:
        lines.append("### Fragmentação")
        lines.append("")
        lines.append(f"{dossier.market.market_fragmentation.value}")
        lines.append("")

    if dossier.market.competitors:
        lines.append("### Principais concorrentes")
        lines.append("")
        lines.append("| Empresa | Lojas | Faturamento (BRL MM) | Investidor |")
        lines.append("|---------|------:|--------------------:|-----------|")
        for c in dossier.market.competitors:
            inv = c.investor or "-"
            rev = f"{c.revenue:,.0f}" if c.revenue else "-"
            stores = f"{c.stores:,}" if c.stores else "-"
            lines.append(f"| {c.name} | {stores} | {rev} | {inv} |")
        lines.append("")

    if dossier.market.global_multiples_median.is_filled:
        mult = dossier.market.global_multiples_median.value
        lines.append("### Múltiplos de referência")
        lines.append("")
        lines.append(f"- **EV/Receita (mediana):** {mult['ev_revenue_median']}x")
        lines.append(f"- **EV/EBITDA (mediana):** {mult['ev_ebitda_median']}x")
        lines.append("")

    # --- Transaction ---
    lines.append("## 4. Transação")
    lines.append("")
    t = dossier.transaction
    for field_name in t.__dataclass_fields__:
        tf = getattr(t, field_name)
        if tf.is_filled:
            label = field_name.replace("_", " ").title()
            lines.append(f"- **{label}:** {tf.value}")
    lines.append("")

    # --- Gaps ---
    lines.append("## 5. Lacunas identificadas")
    lines.append("")

    critical = [g for g in dossier.gaps if g.severity == "critical"]
    important = [g for g in dossier.gaps if g.severity == "important"]
    internet = [g for g in dossier.gaps if g.requires_internet]

    if critical:
        lines.append("### Críticas")
        lines.append("")
        for g in critical:
            source = f" *(fonte sugerida: {g.suggested_source})*" if g.suggested_source else ""
            web = " 🌐" if g.requires_internet else ""
            lines.append(f"- {g.description}{source}{web}")
        lines.append("")

    if important:
        lines.append("### Importantes")
        lines.append("")
        for g in important:
            source = f" *(fonte sugerida: {g.suggested_source})*" if g.suggested_source else ""
            web = " 🌐" if g.requires_internet else ""
            lines.append(f"- {g.description}{source}{web}")
        lines.append("")

    if internet:
        lines.append(f"*🌐 = requer pesquisa na internet ({len(internet)} itens)*")
        lines.append("")

    return "\n".join(lines)