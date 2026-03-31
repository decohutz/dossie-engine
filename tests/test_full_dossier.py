"""
Test: full pipeline — PDF in, Dossier out.

Usage:
    python -m tests.test_full_dossier
"""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.pipeline.orchestrator import run_pipeline
from src.pipeline.assembler import to_markdown, to_json


def test_full_dossier():
    pdf_path = "data/inputs/Projeto_Frank_CIM.pdf"

    if not os.path.exists(pdf_path):
        print(f"⚠️  PDF não encontrado em {pdf_path}")
        return

    print("Running full pipeline...")
    dossier = run_pipeline(pdf_path, project_name="Projeto Frank")

    # Print summary
    print("\n" + "=" * 50)
    print("  DOSSIER SUMMARY")
    print("=" * 50)
    for key, val in dossier.summary().items():
        print(f"  {key:30s}: {val}")

    # Generate outputs
    os.makedirs("data/outputs", exist_ok=True)

    # Markdown
    md = to_markdown(dossier)
    md_path = "data/outputs/dossie_projeto_frank.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"\n✅ Markdown salvo: {md_path} ({len(md):,} chars)")

    # JSON
    js = to_json(dossier)
    json_path = "data/outputs/dossie_projeto_frank.json"
    with open(json_path, "w", encoding="utf-8") as f:
        f.write(js)
    print(f"✅ JSON salvo: {json_path} ({len(js):,} chars)")

    # Validations
    print("\n" + "=" * 50)
    print("  VALIDAÇÕES")
    print("=" * 50)

    s = dossier.summary()

    checks = [
        (s["financial_statements"] >= 4, f"Financial statements: {s['financial_statements']} (≥4)"),
        (s["executives"] >= 3, f"Executives: {s['executives']} (≥3)"),
        (s["timeline_events"] >= 5, f"Timeline events: {s['timeline_events']} (≥5)"),
        (s["competitors"] >= 3, f"Competitors: {s['competitors']} (≥3)"),
        (s["market_sizes"] >= 2, f"Market sizes: {s['market_sizes']} (≥2)"),
        (s["products"] >= 3, f"Products: {s['products']} (≥3)"),
        (s["gaps_total"] > 0, f"Gaps detected: {s['gaps_total']}"),
        (s["gaps_critical"] > 0, f"Critical gaps: {s['gaps_critical']}"),
    ]

    for passed, desc in checks:
        icon = "✅" if passed else "❌"
        print(f"  {icon} {desc}")

    # Show gaps
    print(f"\n  📋 Gaps ({len(dossier.gaps)} total):")
    for g in dossier.gaps:
        icon = "🔴" if g.severity == "critical" else "🟡"
        web = " 🌐" if g.requires_internet else ""
        print(f"     {icon} [{g.chapter}] {g.description}{web}")

    print(f"\n✅ Pipeline concluído! Abra {md_path} para ver o dossiê.")


if __name__ == "__main__":
    test_full_dossier()