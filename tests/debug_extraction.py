"""
Debug: check what text pdfplumber actually extracts for key pages.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.parsers.pdf_parser import parse_pdf

blocks = parse_pdf("data/inputs/Projeto_Frank_CIM.pdf")

# Page 30 = Team page (executives)
print("=" * 60)
print("PAGE 30 — TEAM (looking for executive names)")
print("=" * 60)
b30 = blocks[29]
print(b30.clean_text[:2000])

names = ["celso silva", "gustavo freitas", "luis oliveira", "fábio nadruz", "cesar lucchesi",
         "fabio nadruz", "césar lucchesi"]
for name in names:
    found_clean = name in b30.clean_text.lower()
    found_raw = name in b30.raw_text.lower()
    print(f"  '{name}': clean={found_clean}, raw={found_raw}")

# Page 24 = Competitors
print("\n" + "=" * 60)
print("PAGE 24 — COMPETITORS")
print("=" * 60)
b24 = blocks[23]
print(b24.clean_text[:2000])

keywords = ["top 5", "companhias no varejo", "faturamento", "óticas carol", "oticas carol",
            "chilli beans", "óticas diniz", "qóculos"]
for kw in keywords:
    found_clean = kw in b24.clean_text.lower()
    found_raw = kw in b24.raw_text.lower()
    print(f"  '{kw}': clean={found_clean}, raw={found_raw}")


if __name__ == "__main__":
    pass