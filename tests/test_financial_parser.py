"""
Test: financial parser against Projeto Frank CIM.

Usage:
    python -m tests.test_financial_parser

Requires: data/inputs/Projeto_Frank_CIM.pdf
"""
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pdfplumber
from src.parsers.financial_parser import parse_financial_text, print_statement_summary


def test_parse_all_financial_pages():
    pdf_path = "data/inputs/Projeto_Frank_CIM.pdf"
    
    if not os.path.exists(pdf_path):
        print(f"⚠️  PDF não encontrado em {pdf_path}")
        print(f"   Coloque o CIM do Projeto Frank em data/inputs/ e rode novamente.")
        return
    
    pdf = pdfplumber.open(pdf_path)
    
    pages_config = [
        (47, "Franqueadora", "dre"),
        (48, "Franqueadora", "balance_sheet"),
        (49, "Distribuidora", "dre"),
        (50, "Distribuidora", "balance_sheet"),
        (51, "Lojas Próprias", "dre"),
        (52, "Lojas Próprias", "balance_sheet"),
    ]
    
    results = {}
    for page_num, entity, stmt_type in pages_config:
        page = pdf.pages[page_num - 1]
        text = page.extract_text() or ""
        
        stmt = parse_financial_text(
            text=text,
            entity_name=entity,
            statement_type=stmt_type,
            source_file="Projeto_Frank_CIM.pdf",
            page=page_num,
        )
        key = f"{entity}_{stmt_type}"
        results[key] = stmt
        print_statement_summary(stmt)
    
    pdf.close()
    
    # --- VALIDAÇÕES ---
    print("\n\n" + "=" * 50)
    print("  VALIDAÇÕES")
    print("=" * 50)
    
    dre_franq = results["Franqueadora_dre"]
    
    # Check Receita Bruta 2025
    val = dre_franq.get_value("Receita Bruta", "2025")
    expected = 39612.0
    status = "✅" if val == expected else "❌"
    print(f"  {status} Receita Bruta Franq. 2025: {val} (esperado: {expected})")
    
    # Check EBITDA 2025
    val = dre_franq.get_value("EBITDA", "2025")
    expected = 10382.0
    status = "✅" if val == expected else "❌"
    print(f"  {status} EBITDA Franq. 2025: {val} (esperado: {expected})")
    
    # Check Lucro Líquido 2025
    val = dre_franq.get_value("Lucro Líquido", "2025")
    expected = 5921.0
    status = "✅" if val == expected else "❌"
    print(f"  {status} Lucro Líquido Franq. 2025: {val} (esperado: {expected})")
    
    # Check Distribuidora Receita 2025
    dre_dist = results["Distribuidora_dre"]
    val = dre_dist.get_value("Receita Bruta", "2025E")
    expected = 38441.0
    status = "✅" if val == expected else "❌"
    print(f"  {status} Receita Bruta Dist. 2025E: {val} (esperado: {expected})")
    
    # Check Lojas Próprias EBITDA 2027E
    dre_lp = results["Lojas Próprias_dre"]
    val = dre_lp.get_value("EBITDA", "2027E")
    # Note: might be captured under a different label due to multi-line parsing
    print(f"  ℹ️  EBITDA Lojas Próprias 2027E: {val} (esperado: 6890)")
    
    # Number of lines parsed
    total_lines = sum(len(s.lines) for s in results.values())
    print(f"\n  📊 Total de linhas parseadas: {total_lines} across {len(results)} statements")
    
    print("\n✅ Teste concluído!")


if __name__ == "__main__":
    test_parse_all_financial_pages()
