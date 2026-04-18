# Dossiê Engine

Sistema de geração inteligente de dossiês a partir de documentos de transação (CIM, teasers, apresentações institucionais).

Recebe um PDF como entrada, extrai informações estruturadas via LLM local (Ollama), enriquece com dados públicos da web, identifica lacunas, e gera um dossiê completo em Markdown, JSON e Excel — com rastreabilidade de cada dado até a página e trecho de origem.

## Status atual: v0.4.0

Pipeline ponta a ponta funcional com extração por LLM local, enriquecimento web e exportação Excel.

| Indicador | Resultado |
|-----------|-----------|
| Executivos extraídos | 5 (com background e participação) |
| Shareholders | 2 |
| Timeline events | 8-9 |
| Produtos/marcas | 4-6 |
| Concorrentes | 5 |
| Demonstrativos financeiros | 6 (DRE + Balanço × 3 entidades, 10 anos) |
| Market sizes | 3 |
| Gaps preenchidos pela web | 4 (reputação, contencioso, razão social, sede) |
| Gaps restantes | 4 (1 crítica, 3 importantes) |
| Output Excel | 10 abas formatadas |

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
pip install pdfplumber typer rich openpyxl requests beautifulsoup4

# Instale o Ollama e o modelo (para extração por LLM)
# https://ollama.ai
ollama pull qwen2.5:14b

# Coloque o PDF de entrada
cp ~/Downloads/Projeto_Frank_CIM.pdf data/inputs/
```

## Uso

### Processar um CIM e gerar o dossiê

```bash
# Básico: extrai com LLM, gera MD + JSON
python -m src.cli process data/inputs/Projeto_Frank_CIM.pdf -p "Projeto Frank" -f both -v

# Completo: com enriquecimento web e Excel
python -m src.cli process data/inputs/Projeto_Frank_CIM.pdf -p "Projeto Frank" -f both --enrich --xlsx -v

# Sem LLM (fallback para regras, só funciona com Projeto Frank)
python -m src.cli process data/inputs/Projeto_Frank_CIM.pdf -p "Projeto Frank" --no-llm -v
```

Outputs gerados:
- `data/outputs/dossie_projeto_frank.md` — dossiê legível em Markdown
- `data/outputs/dossie_projeto_frank.json` — dossiê estruturado em JSON
- `data/outputs/dossie_projeto_frank.xlsx` — planilha Excel com 10 abas
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

## Flags do CLI

| Flag | Descrição |
|------|-----------|
| `-p`, `--project` | Nome do projeto |
| `-f`, `--format` | Formato de saída: `md`, `json`, `both` |
| `--no-llm` | Desabilitar LLM, usar extração por regras |
| `-e`, `--enrich` | Enriquecer com dados da web (Reclame Aqui, Jusbrasil, Google) |
| `--xlsx` | Gerar planilha Excel (.xlsx) |
| `-v`, `--verbose` | Mostrar progresso detalhado |

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
│ (texto + │    │ (página →  │    │ LLM (Ollama)   │    │ (o que       │
│ tabelas) │    │  capítulo) │    │ ou regras      │    │  falta?)     │
└──────────┘    └────────────┘    └────────────────┘    └──────────────┘
                                                               │
                                                               ▼
                                                        ┌──────────────┐
                                                        │ Enriquecim.  │
                                                        │ Web (opcion.)│
                                                        │ Reclame Aqui │
                                                        │ Jusbrasil    │
                                                        └──────┬───────┘
                                                               │
                                                               ▼
                                                        ┌──────────────┐
                                                        │  Assembly +  │
                                                        │ Versionamento│
                                                        │ MD/JSON/XLSX │
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
│   ├── classifier.py        #   Classifica páginas por capítulo
│   ├── orchestrator.py      #   Coordena pipeline ponta a ponta
│   ├── assembler.py         #   Gera output em Markdown e JSON
│   ├── llm_extractor.py     #   Extração via Ollama (company, market, transaction)
│   └── rules_extractor.py   #   Extração via regras (fallback)
│
├── llm/                     # Integração com LLM local
│   ├── client.py            #   OllamaClient (generate, extract_json)
│   └── prompts.py           #   8 templates de prompt em português
│
├── enrichment/              # Enriquecimento via web
│   ├── fetcher.py           #   HTTP client com rate limiting
│   ├── sources.py           #   Scrapers: Reclame Aqui, Jusbrasil, Google
│   └── enricher.py          #   Orquestrador de enriquecimento
│
├── exporters/               # Exportação para formatos profissionais
│   └── xlsx_exporter.py     #   Excel com 10 abas formatadas
│
├── storage/                 # Persistência
│   └── versioning.py        #   Snapshots, listagem e diff de versões
│
└── cli.py                   # Interface de linha de comando (typer)

tests/
├── test_financial_parser.py
├── test_pdf_pipeline.py
├── test_full_dossier.py
└── debug_extraction.py
```

## Como funciona cada módulo

### Models (`src/models/`)

Definem a **forma** do dossiê. Cada dado extraído é um `TrackedField` que carrega o valor junto com sua evidência (arquivo de origem, página, trecho de texto, confiança, método de extração). O `Gap` representa uma informação esperada que não foi encontrada.

O `Dossier` é o objeto raiz que contém 4 capítulos: `CompanyChapter`, `FinancialChapter`, `MarketChapter`, `TransactionChapter`, mais uma lista de `Gap`.

### Parsers (`src/parsers/`)

**`pdf_parser.py`** — Abre o PDF com `pdfplumber`, extrai texto de cada página, remove ruído (watermarks, color codes, headers repetidos), e classifica cada página como `content`, `financial_table`, `separator`, ou `title`.

**`financial_parser.py`** — Recebe texto bruto de uma página de DRE ou Balanço e parseia em linhas estruturadas. Lida com formato financeiro brasileiro: pontos como separador de milhar (`22.575` = 22575), parênteses como negativos (`(1.896)` = -1896), vírgula como decimal em percentuais (`37,9%`), e traço duplo como zero (`--`).

### LLM (`src/llm/`)

**`client.py`** — Cliente para Ollama local. Envia texto ao modelo Qwen 2.5 14B e extrai JSON estruturado das respostas. Parsing robusto com fallback para regex quando o JSON vem com texto extra.

**`prompts.py`** — 8 templates de prompt em português para extração de: perfil da empresa, executivos, timeline, produtos, mercado, concorrentes, múltiplos e transação. Cada prompt especifica o formato JSON esperado.

### Pipeline (`src/pipeline/`)

**`classifier.py`** — Mapeia cada página a um capítulo do dossiê usando regras de keywords.

**`orchestrator.py`** — Coordena: parse → classify → extract (LLM ou regras) → gaps → enrich → assemble.

**`llm_extractor.py`** — Extrai dados estruturados via LLM local. Genérico — funciona com qualquer CIM, não apenas Projeto Frank.

**`rules_extractor.py`** — Fallback com regras hardcoded (só funciona com Projeto Frank).

**`assembler.py`** — Converte o objeto `Dossier` em Markdown e JSON.

### Enrichment (`src/enrichment/`)

**`fetcher.py`** — HTTP client com rate limiting (2s entre requests), User-Agent realista, e decodificação de URLs de redirect do DuckDuckGo.

**`sources.py`** — Scrapers para Reclame Aqui (nota, reclamações), Jusbrasil (processos), Google Reviews, e busca geral (CNPJ, sede, funcionários).

**`enricher.py`** — Orquestra as buscas, envia resultados ao LLM local para extração estruturada, e preenche gaps no dossiê. **Privacidade**: apenas o nome da empresa é enviado à internet. O conteúdo do CIM nunca sai da máquina.

### Exporters (`src/exporters/`)

**`xlsx_exporter.py`** — Gera planilha Excel com 10 abas: Visão Geral, 3× DRE, 3× Balanço, Mercado, Transação, Gaps. Formatação profissional: projetados em itálico azul, negativos em vermelho, totais em verde, auto-width.

### Storage (`src/storage/`)

**`versioning.py`** — Cada execução salva um snapshot JSON com timestamp. Suporta listagem, carregamento, e diff entre versões.

### CLI (`src/cli.py`)

Interface via `typer` com 5 comandos: `process`, `show`, `gaps`, `versions`, `diff`. Usa `rich` para tabelas formatadas no terminal.

## Privacidade e segurança

O sistema foi projetado para lidar com CIMs confidenciais:

| Componente | Dados enviados à internet | Risco |
|------------|---------------------------|-------|
| LLM (Ollama local) | Nenhum — roda na máquina | Zero |
| Enriquecimento web | Apenas o nome da empresa | Mínimo (equivale a googlar) |
| Exportação Excel/JSON | Nenhum — arquivos locais | Zero |

O conteúdo do CIM (financeiros, termos, cap table) **nunca** sai da sua máquina.

## Limitações atuais

- **Concorrentes não identificados**: nomes dos concorrentes no CIM estão em logos (imagens), não em texto. O LLM os lista como "Empresa não identificada (posição N)".
- **Marcas próprias parciais**: marcas como Armatti, Cloté, Rizz aparecem apenas em imagens do PDF.
- **Enriquecimento web instável**: DuckDuckGo pode não retornar resultados em algumas execuções. Dados como sede e razão social podem ser de franquias individuais, não da holding.
- **Sem valuation**: modelo financeiro, DCF, múltiplos e cenários ainda não implementados.
- **Sem PPT**: apresentação executiva em PowerPoint ainda não implementada.

## Roadmap

| Marco | Descrição | Status |
|-------|-----------|--------|
| Marco 0 | MVP: pipeline ponta a ponta, CLI, versionamento | ✅ Concluído |
| Marco 1 | CLI com versionamento (process, show, gaps, versions, diff) | ✅ Concluído |
| Marco 2 | LLM local na extração (Ollama + Qwen 2.5 14B) | ✅ Concluído |
| Marco 3 | Enriquecimento web (Reclame Aqui, Jusbrasil, Google) | ✅ Concluído |
| Marco 4a | Exportação Excel (10 abas formatadas) | ✅ Concluído (90%) |
| Marco 4b | Exportação PPT (apresentação executiva) | ✅ Concluído (90%) |
| Marco 5 | Valuation e cenários (DCF, múltiplos, IRR, cenários) | ✅ Concluído |
| v1.0 | Sistema completo com interface desktop | Futuro |

## Dependências

- `pdfplumber` — extração de texto e tabelas de PDFs
- `typer` + `rich` — CLI com tabelas formatadas
- `openpyxl` — geração de planilhas Excel
- `requests` + `beautifulsoup4` — scraping web para enriquecimento
- `ollama` — LLM local (Qwen 2.5 14B) para extração inteligente

## Hardware recomendado

- **RAM**: 32GB (mínimo 16GB)
- **GPU**: NVIDIA com 16GB VRAM (ex: RTX 5080) para Qwen 2.5 14B
- **Alternativa**: modelos menores (qwen2.5:7b) rodam com 8GB VRAM
