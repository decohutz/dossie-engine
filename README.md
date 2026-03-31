# Dossiê Engine

Sistema de geração inteligente de dossiês a partir de documentos de transação (CIM, teasers, apresentações institucionais).

## Setup

```bash
# 1. Clone o repo
git clone https://github.com/SEU-USER/dossie-engine.git
cd dossie-engine

# 2. Crie o ambiente virtual
python3 -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows

# 3. Instale as dependências
pip install -e ".[dev]"

# 4. Configure a API key (necessário para etapas com LLM)
cp .env.example .env
# Edite o .env com sua ANTHROPIC_API_KEY

# 5. Coloque seu PDF em data/inputs/
cp ~/Downloads/Projeto_Frank_CIM.pdf data/inputs/

# 6. Teste o parsing
python -m tests.test_financial_parser
```

## Estrutura

```
src/
├── models/          # Schemas do dossiê (o contrato central)
├── parsers/         # Parsers por formato (PDF, PPTX, DOCX)
├── pipeline/        # Estágios do pipeline (orquestração)
├── llm/             # Integração com LLM (classificação, extração)
├── storage/         # Persistência e versionamento
└── validators/      # Validação de dados extraídos
```

## Status do MVP

- [x] Models core (Evidence, TrackedField, Gap)
- [x] Models por capítulo (Company, Financials, Market, Transaction)
- [x] Parser financeiro (DRE e Balanço via texto)
- [ ] Parser de texto geral (PDF pages → blocos)
- [ ] Classificação de blocos (LLM)
- [ ] Extração de dados estruturados (LLM)
- [ ] Gap analysis
- [ ] Assembly (JSON + Markdown)
- [ ] Versionamento
- [ ] CLI
