"""
Dossier Engine CLI.

Usage:
    dossie process data/inputs/Projeto_Frank_CIM.pdf --project "Projeto Frank"
    dossie show --project "Projeto Frank"
    dossie show --project "Projeto Frank" --format json
    dossie gaps --project "Projeto Frank"
    dossie versions --project "Projeto Frank"
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

try:
    import typer
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

app = typer.Typer(
    name="dossie",
    help="Dossier Engine — geração inteligente de dossiês a partir de documentos de transação.",
    add_completion=False,
)
console = Console() if HAS_RICH else None


def _print(msg: str):
    if console:
        console.print(msg)
    else:
        print(msg)


@app.command()
def process(
    file: str = typer.Argument(..., help="Caminho para o arquivo PDF de entrada"),
    project: str = typer.Option("", "--project", "-p", help="Nome do projeto (ex: 'Projeto Frank')"),
    output_format: str = typer.Option("md", "--format", "-f", help="Formato de saída: md, json, both"),
    no_llm: bool = typer.Option(False, "--no-llm", help="Desabilitar LLM e usar extração por regras"),
    enrich: bool = typer.Option(False, "--enrich", "-e", help="Enriquecer com dados da web (busca pública)"),
    xlsx: bool = typer.Option(False, "--xlsx", help="Gerar planilha Excel (.xlsx)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Mostrar progresso detalhado"),
    pptx: bool = typer.Option(False, "--pptx", help="Gerar apresentação PowerPoint (.pptx)"),
):
    """Processa um CIM/PDF e gera o dossiê completo."""
    from .pipeline.orchestrator import run_pipeline
    from .pipeline.assembler import to_markdown, to_json
    from .storage.versioning import save_version, get_next_version_number

    if not os.path.exists(file):
        _print(f"[red]Erro:[/red] Arquivo não encontrado: {file}")
        raise typer.Exit(1)

    project_name = project or Path(file).stem.replace("_", " ")

    mode = "regras (sem LLM)" if no_llm else "LLM (Ollama)"
    _print(f"\n[bold]Processando:[/bold] {file}")
    _print(f"[bold]Projeto:[/bold] {project_name}")
    _print(f"[bold]Extração:[/bold] {mode}")
    if enrich:
        _print(f"[bold]Enriquecimento:[/bold] Web (Reclame Aqui, Jusbrasil, Google)")
    if xlsx:
        _print(f"[bold]Excel:[/bold] Sim")
    if pptx:
        _print(f"[bold]PPT:[/bold] Sim")
    _print("")

    version = get_next_version_number(project_name)

    dossier = run_pipeline(
        file, project_name=project_name,
        use_llm=not no_llm, enrich=enrich, verbose=verbose
    )
    dossier.metadata.version = version

    os.makedirs("data/outputs", exist_ok=True)
    safe_name = project_name.lower().replace(" ", "_")
    files_saved = []

    if output_format in ("md", "both"):
        md = to_markdown(dossier)
        md_path = f"data/outputs/dossie_{safe_name}.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md)
        files_saved.append(("Markdown", md_path, f"{len(md):,} chars"))

    if output_format in ("json", "both"):
        js = to_json(dossier)
        json_path = f"data/outputs/dossie_{safe_name}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            f.write(js)
        files_saved.append(("JSON", json_path, f"{len(js):,} chars"))

    if xlsx:
        from .exporters.xlsx_exporter import export_xlsx
        xlsx_path = f"data/outputs/dossie_{safe_name}.xlsx"
        export_xlsx(dossier, xlsx_path, verbose=verbose)
        files_saved.append(("Excel", xlsx_path, "10 abas"))

    if pptx:
        from .exporters.pptx_exporter import export_pptx
        pptx_path = f"data/outputs/dossie_{safe_name}.pptx"
        export_pptx(dossier, pptx_path, verbose=verbose)
        files_saved.append(("PPT", pptx_path, "13 slides"))

    full_json = to_json(dossier)
    dossier_dict = json.loads(full_json)
    version_path = save_version(project_name, dossier_dict)
    files_saved.append(("Versão", version_path, version))

    _print_summary(dossier, files_saved)


@app.command()
def show(
    project: str = typer.Option(..., "--project", "-p", help="Nome do projeto"),
    version: str = typer.Option(None, "--version", "-v", help="Versão específica (ex: v001)"),
    format: str = typer.Option("md", "--format", "-f", help="Formato: md, json, summary"),
):
    """Exibe o dossiê mais recente (ou uma versão específica)."""
    from .storage.versioning import load_version

    data = load_version(project, version)
    if data is None:
        _print(f"[red]Nenhuma versão encontrada para o projeto '{project}'[/red]")
        raise typer.Exit(1)

    meta = data.get("metadata", {})
    _print(f"\n[bold]{meta.get('project_name', project)}[/bold] — {meta.get('version', '?')}")
    _print(f"Empresa: {meta.get('target_company', '?')}")
    _print(f"Gerado em: {meta.get('created_at', '?')[:19]}\n")

    if format == "json":
        _print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    elif format == "summary":
        _print_summary_from_dict(data)
    else:
        _print_summary_from_dict(data)
        _print(f"\n[dim]Para ver o Markdown completo: data/outputs/dossie_*.md[/dim]")


@app.command()
def gaps(
    project: str = typer.Option(..., "--project", "-p", help="Nome do projeto"),
):
    """Lista as lacunas identificadas no dossiê."""
    from .storage.versioning import load_version

    data = load_version(project)
    if data is None:
        _print(f"[red]Nenhuma versão encontrada para o projeto '{project}'[/red]")
        raise typer.Exit(1)

    gap_list = data.get("gaps", [])
    if not gap_list:
        _print("[green]Nenhuma lacuna identificada![/green]")
        return

    if console and HAS_RICH:
        table = Table(title=f"Lacunas — {data['metadata'].get('project_name', project)}")
        table.add_column("Sev.", style="bold", width=6)
        table.add_column("Capítulo", width=12)
        table.add_column("Descrição")
        table.add_column("Fonte sugerida", style="dim")
        table.add_column("Web", width=4)

        for g in gap_list:
            sev_icon = "[red]CRIT[/red]" if g["severity"] == "critical" else "[yellow]IMP[/yellow]"
            web = "🌐" if g.get("requires_internet") else ""
            table.add_row(
                sev_icon,
                g["chapter"],
                g["description"],
                g.get("suggested_source") or "-",
                web,
            )
        console.print(table)
    else:
        _print(f"\nGaps ({len(gap_list)}):")
        for g in gap_list:
            sev = "CRIT" if g["severity"] == "critical" else "IMP "
            web = " 🌐" if g.get("requires_internet") else ""
            _print(f"  [{sev}] {g['chapter']:12s} | {g['description']}{web}")

    critical = sum(1 for g in gap_list if g["severity"] == "critical")
    important = sum(1 for g in gap_list if g["severity"] == "important")
    internet = sum(1 for g in gap_list if g.get("requires_internet"))
    _print(f"\n  Total: {len(gap_list)} | Críticas: {critical} | Importantes: {important} | Requerem internet: {internet}")


@app.command()
def versions(
    project: str = typer.Option(..., "--project", "-p", help="Nome do projeto"),
):
    """Lista todas as versões salvas de um projeto."""
    from .storage.versioning import list_versions

    vers = list_versions(project)
    if not vers:
        _print(f"[yellow]Nenhuma versão encontrada para '{project}'[/yellow]")
        return

    if console and HAS_RICH:
        table = Table(title=f"Versões — {project}")
        table.add_column("Versão", style="bold")
        table.add_column("Data")
        table.add_column("Empresa")
        table.add_column("Arquivo", style="dim")

        for v in vers:
            table.add_row(
                v["version"],
                v["created_at"][:19] if v["created_at"] else "-",
                v["target_company"],
                v["file"],
            )
        console.print(table)
    else:
        _print(f"\nVersões de '{project}':")
        for v in vers:
            _print(f"  {v['version']} | {v['created_at'][:19]} | {v['file']}")


@app.command()
def diff(
    project: str = typer.Option(..., "--project", "-p", help="Nome do projeto"),
    old: str = typer.Option(..., "--old", help="Versão antiga (ex: v001)"),
    new: str = typer.Option(None, "--new", help="Versão nova (default: última)"),
):
    """Compara duas versões do dossiê."""
    from .storage.versioning import load_version, compute_diff

    old_data = load_version(project, old)
    new_data = load_version(project, new)

    if old_data is None:
        _print(f"[red]Versão '{old}' não encontrada[/red]")
        raise typer.Exit(1)
    if new_data is None:
        _print(f"[red]Versão '{new or 'latest'}' não encontrada[/red]")
        raise typer.Exit(1)

    changes = compute_diff(old_data, new_data)

    old_ver = old_data["metadata"].get("version", old)
    new_ver = new_data["metadata"].get("version", new or "latest")
    _print(f"\n[bold]Diff: {old_ver} → {new_ver}[/bold]\n")

    for change in changes:
        _print(f"  • {change}")


def _print_summary(dossier, files_saved: list[tuple]):
    """Print summary after processing."""
    s = dossier.summary()

    if console and HAS_RICH:
        table = Table(title=f"Dossiê: {s['project']} — {s['company']}")
        table.add_column("Indicador", style="bold")
        table.add_column("Valor", justify="right")

        rows = [
            ("Versão", s["version"]),
            ("Executivos", s["executives"]),
            ("Shareholders", s["shareholders"]),
            ("Timeline", s["timeline_events"]),
            ("Produtos", s["products"]),
            ("Demonstrativos", s["financial_statements"]),
            ("Concorrentes", s["competitors"]),
            ("Market sizes", s["market_sizes"]),
            ("Gaps totais", s["gaps_total"]),
            ("Gaps críticos", s["gaps_critical"]),
        ]

        for label, val in rows:
            table.add_row(label, str(val))

        console.print(table)
        console.print()

        for fmt, path, info in files_saved:
            console.print(f"  [green]✓[/green] {fmt}: {path} ({info})")

        console.print(f"\n  [bold green]Pipeline concluído![/bold green]")
    else:
        _print(f"\n=== {s['project']} — {s['company']} ===")
        for key, val in s.items():
            if key not in ("project", "company"):
                _print(f"  {key}: {val}")
        for fmt, path, info in files_saved:
            _print(f"  ✓ {fmt}: {path} ({info})")


def _print_summary_from_dict(data: dict):
    """Print summary from a raw dossier dict."""
    from .storage.versioning import _count_summary

    counts = _count_summary(data)
    gaps = data.get("gaps", [])

    if console and HAS_RICH:
        table = Table()
        table.add_column("Indicador", style="bold")
        table.add_column("Valor", justify="right")

        for key, val in counts.items():
            table.add_row(key.replace("_", " ").title(), str(val))
        table.add_row("Gaps", str(len(gaps)))
        table.add_row("Gaps críticos", str(sum(1 for g in gaps if g["severity"] == "critical")))

        console.print(table)
    else:
        for key, val in counts.items():
            _print(f"  {key}: {val}")
        _print(f"  gaps: {len(gaps)}")


if __name__ == "__main__":
    app()