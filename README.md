# Dossiê Engine

Sistema de geração inteligente de dossiês a partir de documentos de transação (CIM, teasers, apresentações institucionais).

Recebe um PDF como entrada, extrai informações estruturadas, identifica lacunas, e gera um dossiê completo em Markdown e JSON — com rastreabilidade de cada dado até a página e trecho de origem.

## Status atual: MVP (v0.1.0)

Pipeline ponta a ponta funcional com o Projeto Frank (Mercadão dos Óculos) como caso de teste.

| Indicador | Resultado |
|-----------|-----------|
| Executivos extraídos | 5 |
| Shareholders | 2 |
| Timeline events | 9 |
| Produtos/marcas | 8 |
| Concorrentes | 5 |
| Demonstrativos financeiros | 6 (DRE + Balanço × 3 entidades) |
| Market sizes | 3 |
| Gaps identificados | 6 (2 críticas, 4 importantes) |

## Setup

```bash
# Clone o repositório
git clone https://github.com/SEU-USER/dossie-engine.git
cd dossie-engine

# Crie e ative o ambiente virtual
python -m venv .venv
source .venv/bin/activate    # Linux/Mac
.venv\Scripts\activate       # Windows

# Instale as dependências
pip install -e ".[dev]"

# Coloque o PDF de entrada
cp ~/Downloads/Projeto_Frank_CIM.pdf data/inputs/
```

## Uso

### Processar um CIM e gerar o dossiê

```bash
python -m src.cli process data/inputs/Projeto_Frank_CIM.pdf -p "Projeto Frank" -f both
```

Gera três outputs:
- `data/outputs/dossie_projeto_frank.md` — dossiê legível
- `data/outputs/dossie_projeto_frank.json` — dossiê estruturado
- `data/versions/projeto_frank/v001_*.json` — snapshot versionado

### Ver as lacunas

```bash
python -m src.cli gaps -p "Projeto Frank"
```

### Listar versões salvas

```bash
python -m src.cli versions -p "Projeto Frank"
```

### Comparar duas versões

```bash
python -m src.cli diff -p "Projeto Frank" --old v001
```

### Ver resumo de uma versão

```bash
python -m src.cli show -p "Projeto Frank" -f summary
```

## Testes

```bash
# Parser financeiro (DRE e Balanço)
python -m tests.test_financial_parser

# Parser de PDF + classificação de páginas
python -m tests.test_pdf_pipeline

# Pipeline ponta a ponta
python -m tests.test_full_dossier
```

## Arquitetura

```
PDF de entrada
     │
     ▼
┌──────────┐    ┌────────────┐    ┌────────────────┐    ┌──────────────┐
│ Parsing  │───▶│ Classific. │───▶│   Extração     │───▶│ Gap Analysis │
│ (texto + │    │ (página →  │    │ (dados estru-  │    │ (o que       │
│ tabelas) │    │  capítulo) │    │  turados)      │    │  falta?)     │
└──────────┘    └────────────┘    └────────────────┘    └──────────────┘
                                                               │
                                                               ▼
                                                        ┌──────────────┐
                                                        │  Assembly +  │
                                                        │ Versionamento│
                                                        │ (MD/JSON)    │
                                                        └──────────────┘
```

### Estrutura do código

```
src/
├── models/                  # Schemas do dossiê (o contrato central)
│   ├── evidence.py          #   TrackedField, Evidence, Gap
│   ├── company.py           #   Profile, Timeline, Executives, Products
│   ├── financials.py        #   DRE, Balanço, Métricas financeiras
│   ├── market.py            #   MarketSize, Competitors, Transactions
│   └── dossier.py           #   Dossiê raiz (junta todos os capítulos)
│
├── parsers/                 # Extração de conteúdo dos arquivos
│   ├── pdf_parser.py        #   PDF → blocos de texto limpos por página
│   └── financial_parser.py  #   Texto de DRE/Balanço → dados estruturados
│
├── pipeline/                # Orquestração do pipeline
│   ├── classifier.py        #   Classifica páginas por capítulo (rules-based)
│   ├── orchestrator.py      #   Coordena todo o pipeline ponta a ponta
│   └── assembler.py         #   Gera output em Markdown e JSON
│
├── storage/                 # Persistência
│   └── versioning.py        #   Snapshots, listagem e diff de versões
│
└── cli.py                   # Interface de linha de comando (typer)

tests/
├── test_financial_parser.py # Valida extração de DRE/Balanço
├── test_pdf_pipeline.py     # Valida parsing + classificação de páginas
├── test_full_dossier.py     # Valida pipeline completo
└── debug_extraction.py      # Debug: inspeciona texto bruto de páginas

data/
├── inputs/                  # PDFs de entrada (gitignored)
├── outputs/                 # Dossiês gerados (gitignored)
└── versions/                # Snapshots versionados (gitignored)
```

## Como funciona cada módulo

### Models (`src/models/`)

Definem a **forma** do dossiê. Cada dado extraído é um `TrackedField` que carrega o valor junto com sua evidência (arquivo de origem, página, trecho de texto, confiança, método de extração). O `Gap` representa uma informação esperada que não foi encontrada.

O `Dossier` é o objeto raiz que contém 4 capítulos: `CompanyChapter`, `FinancialChapter`, `MarketChapter`, `TransactionChapter`, mais uma lista de `Gap`.

### Parsers (`src/parsers/`)

**`pdf_parser.py`** — Abre o PDF com `pdfplumber`, extrai texto de cada página, remove ruído (watermarks, color codes, headers repetidos), e classifica cada página como `content`, `financial_table`, `separator`, ou `title`.

**`financial_parser.py`** — Recebe texto bruto de uma página de DRE ou Balanço e parseia em linhas estruturadas. Lida com formato financeiro brasileiro: pontos como separador de milhar (`22.575` = 22575), parênteses como negativos (`(1.896)` = -1896), vírgula como decimal em percentuais (`37,9%`), e traço duplo como zero (`--`).

### Pipeline (`src/pipeline/`)

**`classifier.py`** — Mapeia cada página a um capítulo do dossiê usando regras de keywords. Cada página recebe um `chapter` (company, market, financials, transaction, meta, skip) e um `sub_chapter` mais específico.

**`orchestrator.py`** — Coordena o pipeline: parse → classify → extract → gaps → assemble. A extração atual usa regras (hardcoded para o Projeto Frank). Na próxima versão, será substituída por extração via LLM.

**`assembler.py`** — Converte o objeto `Dossier` em Markdown (legível por humanos) e JSON (consumível por código).

### Storage (`src/storage/`)

**`versioning.py`** — Cada execução do pipeline salva um snapshot JSON com timestamp em `data/versions/{projeto}/`. Suporta listagem de versões, carregamento de versão específica, e diff entre versões.

### CLI (`src/cli.py`)

Interface via `typer` com 5 comandos: `process`, `show`, `gaps`, `versions`, `diff`. Usa `rich` para tabelas formatadas no terminal.

## Limitações atuais

- **Extração hardcoded**: nomes de executivos, concorrentes, e produtos estão definidos no código. Funciona com o CIM do Projeto Frank, mas não generaliza para outros CIMs automaticamente.
- **Sem enriquecimento web**: gaps que requerem pesquisa na internet (reputação, contencioso, quadro de funcionários) não são preenchidos.
- **Sem valuation**: modelo financeiro, DCF, múltiplos e cenários ainda não estão implementados.
- **Gráficos do PDF não são extraídos**: dados visuais (charts) que não têm tabela adjacente ficam como lacunas.

## Roadmap

| Marco | Descrição | Status |
|-------|-----------|--------|
| MVP | Pipeline ponta a ponta, CLI, versionamento | ✅ Concluído |
| Marco 2 | LLM na extração (generaliza para qualquer CIM) | Próximo |
| Marco 3 | Enriquecimento web (preenche gaps via internet) | Planejado |
| Marco 4 | Output profissional (PPT + Excel) | Planejado |
| Marco 5 | Valuation e cenários | Planejado |
| v1.0 | Sistema completo com interface desktop | Futuro |

## Dependências principais

- `pdfplumber` — extração de texto e tabelas de PDFs
- `typer` + `rich` — CLI com tabelas formatadas
- `python-pptx` / `python-docx` — parsing de PPTX e DOCX (preparado, ainda não usado)
- `anthropic` — integração com Claude API (preparado para Marco 2)
