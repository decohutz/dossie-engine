"""
Test: PDF parsing + page classification on Projeto Frank CIM.

Usage:
    python -m tests.test_pdf_pipeline
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.parsers.pdf_parser import parse_pdf, print_blocks_summary
from src.pipeline.classifier import classify_pages, print_classification_summary


def test_full_pipeline():
    pdf_path = "data/inputs/Projeto_Frank_CIM.pdf"

    if not os.path.exists(pdf_path):
        print(f"⚠️  PDF não encontrado em {pdf_path}")
        return

    # Step 1: Parse all pages
    print("Step 1: Parsing PDF...")
    blocks = parse_pdf(pdf_path)
    print_blocks_summary(blocks)

    # Step 2: Classify pages
    print("\n\nStep 2: Classifying pages...")
    classified = classify_pages(blocks)
    print_classification_summary(classified)

    # Step 3: Validation
    print("\n\n" + "=" * 50)
    print("  VALIDAÇÕES")
    print("=" * 50)

    # Check total pages
    status = "✅" if len(blocks) == 53 else "❌"
    print(f"  {status} Total de páginas: {len(blocks)} (esperado: 53)")

    # Check financial pages detected
    financial_pages = [p for p in classified if p.chapter == "financials"]
    status = "✅" if len(financial_pages) >= 6 else "❌"
    print(f"  {status} Páginas financeiras: {len(financial_pages)} (esperado: ≥6)")

    # Check market pages detected
    market_pages = [p for p in classified if p.chapter == "market"]
    status = "✅" if len(market_pages) >= 5 else "❌"
    print(f"  {status} Páginas de mercado: {len(market_pages)} (esperado: ≥5)")

    # Check company pages detected
    company_pages = [p for p in classified if p.chapter == "company"]
    status = "✅" if len(company_pages) >= 5 else "❌"
    print(f"  {status} Páginas da empresa: {len(company_pages)} (esperado: ≥5)")

    # Check unknown pages
    unknown_pages = [p for p in classified if p.chapter == "unknown"]
    print(f"  ℹ️  Páginas não classificadas: {len(unknown_pages)}")
    for p in unknown_pages:
        print(f"      Pág {p.block.page_number}: {p.block.first_heading[:60]}")

    # Check no content pages classified as skip
    content_skipped = [p for p in classified
                       if p.chapter == "skip" and p.block.line_count > 5]
    if content_skipped:
        print(f"  ⚠️  Páginas com conteúdo classificadas como skip:")
        for p in content_skipped:
            print(f"      Pág {p.block.page_number}: {p.block.line_count} lines")
    else:
        print(f"  ✅ Nenhuma página com conteúdo foi ignorada")

    print("\n✅ Teste concluído!")


if __name__ == "__main__":
    test_full_pipeline()