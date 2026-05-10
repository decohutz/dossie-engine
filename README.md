# Dossiê Engine

Sistema de geração inteligente de dossiês a partir de documentos de transação (CIMs, infopacks, apresentações institucionais).

Recebe **PDFs e/ou XLSX** como entrada, extrai informações estruturadas via LLM local (Ollama), enriquece com dados públicos da web, identifica lacunas, e gera dossiê completo em **Markdown, JSON, Excel e PowerPoint** — com valuation completo (DCF + Múltiplos + IRR/MOIC em 3 cenários), análise de gaps, e rastreabilidade de cada dado até a página/sheet de origem.

## Status atual: v0.6.0 (pós E6)

Pipeline ponta a ponta funcional, com **dois golden cases** travados por regression byte-identical: Frank (PDF, óptica, 3 entidades) e Regenera-shape (XLSX, beleza, 6 entidades incluindo CSC non-operating).

| Indicador | Frank (PDF) | Regenera (XLSX + PDF) |
|-----------|-------------|----------------------|
| Entidades financeiras | 3 (todas operating) | 6 (5 ops + 1 CSC non-op) |
| DREs extraídos (anos) | 10 | 7 (1 histórico + 6 projetados) |
| Balanços extraídos | 3 | 0 (CIM não tem) |
| Cenários valuation | Pess/Base/Otim divergem | Pess/Base/Otim divergem |
| IRR / MOIC | calcula e diverge | calcula e diverge |
| Slides PPT gerados | 15 | 14 (Balanço suprimido) |
| Abas Excel geradas | 11 | 11 |
| Suite de testes | 142 passed em ~3min | (mesma suite, ambos cobertos) |

Veja `CHANGELOG.md` para detalhes de cada fase (E1 → E6) e bugs corrigidos (B1 → B8).

## Setup

```bash
git clone https://github.com/decohutz/dossie-engine.git
cd dossie-engine

python -m venv .venv
source .venv/bin/activate    # Linux/Mac
.venv\Scripts\activate       # Windows

pip install pdfplumber typer rich openpyxl requests beautifulsoup4 \
            python-pptx matplotlib pytest

# Ollama + modelo (extração por LLM)
# https://ollama.ai
ollama pull qwen2.5:14b

# Coloque os arquivos de entrada
cp ~/Downloads/Projeto_Frank_CIM.pdf data/inputs/
# ou XLSX
cp ~/Downloads/Infopack.xlsx data/inputs/
```

## Uso

### Processar um CIM

```bash
# PDF only (caso Frank)
python -m src.cli process data/inputs/Projeto_Frank_CIM.pdf -p "Projeto Frank" \
    --valuation --xlsx --pptx -v

# XLSX only (financial pack puro)
python -m src.cli process data/inputs/Infopack.xlsx -p "Projeto X" \
    --valuation --xlsx --pptx -v

# Misto: PDF (institucional) + XLSX (financeiros) — formato Regenera
python -m src.cli process data/inputs/pitch.pdf data/inputs/financial_pack.xlsx \
    -p "Projeto X" --valuation --xlsx --pptx -v

# Com manual overrides para múltiplos e mercado (recomendado quando
# DDG está bloqueado ou quando você já sabe os números do setor)
python -m src.cli process data/inputs/financial_pack.xlsx \
    -p "Projeto X" --valuation --xlsx --pptx -v \
    --ev-ebitda 11.0 --ev-revenue 1.8 \
    --market-size-brl-bn 12.5 --market-cagr 0.08
```

Outputs gerados em `data/outputs/`:
- `dossie_<projeto>.md` — dossiê legível em Markdown
- `dossie_<projeto>.json` — dossiê estruturado em JSON
- `dossie_<projeto>.xlsx` — planilha Excel (11 abas)
- `dossie_<projeto>.pptx` — apresentação PowerPoint (14-15 slides)
- `valuation_<projeto>.json` — resultado completo de valuation (3 cenários × 4 métodos)
- `data/versions/<projeto>/v00X_*.json` — snapshot versionado

### Outros comandos

```bash
python -m src.cli gaps -p "Projeto X"            # ver lacunas
python -m src.cli versions -p "Projeto X"        # listar versões
python -m src.cli diff -p "Projeto X" --old v001
python -m src.cli show -p "Projeto X" -f summary
```

## Flags do CLI

### Comuns

| Flag | Descrição |
|------|-----------|
| `-p`, `--project` | Nome do projeto |
| `-f`, `--format` | Formato de saída: `md`, `json`, `both` |
| `--no-llm` | Desabilitar LLM, usar extração por regras (só Frank) |
| `-e`, `--enrich` | Enriquecer com dados da web (default: True) |
| `--xlsx` | Gerar planilha Excel |
| `--pptx` | Gerar apresentação PowerPoint |
| `--valuation` | Rodar DCF + múltiplos + IRR + cenários |
| `-v`, `--verbose` | Mostrar progresso detalhado |

### Manual overrides (E3.5)

Use quando o web enrichment falha ou quando você quer pinar números específicos.
Cada flag preenche apenas slots vazios — não sobrescreve dados extraídos do CIM.

| Flag | Descrição |
|------|-----------|
| `--ev-ebitda` | Múltiplo EV/EBITDA mediano do setor (ex: `11.0`) |
| `--ev-revenue` | Múltiplo EV/Revenue mediano do setor (ex: `1.8`) |
| `--market-size-brl-bn` | TAM em BRL Bn (ex: `12.5`) |
| `--market-cagr` | CAGR em decimal (ex: `0.08` para 8%) |
| `--stake-pct` | Stake do investidor (ex: `0.30` para 30%) |
| `--entry-price` | Override do preço de entrada (BRL k) |

## Testes

```bash
# Suite completa (142 tests, ~3min)
python -m pytest tests/ -v

# Apenas regressão Frank (byte-identical)
python -m pytest tests/test_frank_regression.py -v

# Apenas regressão Regenera (synthetic-shaped)
python -m pytest tests/test_regenera_regression.py -v

# Smoke por fase
python -m pytest tests/test_e3_scenarios.py     # cenários divergem
python -m pytest tests/test_e4_irr_moic.py      # IRR/MOIC propagation
python -m pytest tests/test_e5_visual.py        # XLSX/PPTX visual
```

A suite cobre tanto **shape Frank** (3-entity óptica, balanços, accents)
quanto **shape Regenera** (6-entity beleza, sem balanços, "Receita Liquida"
sem accent, manual overrides). Frank e Regenera-synthetic baselines
permanecem byte-identical em cada fase.

## Arquitetura

```
inputs (list[PDF|XLSX])
     │
     ▼
┌──────────┐    ┌────────────┐    ┌────────────────┐
│ Parsing  │───▶│ Classific. │───▶│   Extração     │
│ PDF +    │    │ (página →  │    │ LLM (Ollama)   │
│ XLSX     │    │  capítulo) │    │ + regras       │
└──────────┘    └────────────┘    └───────┬────────┘
                                          │
                                          ▼
                            ┌────────────────────────┐
                            │  Merge XLSX over PDF   │  E2
                            └────────┬───────────────┘
                                     │
                                     ▼
                            ┌────────────────────────┐
                            │ Manual overrides       │  E3.5
                            │ ← --ev-ebitda etc      │
                            └────────┬───────────────┘
                                     │
                                     ▼
                            ┌────────────────────────┐
                            │ Gap analysis           │
                            └────────┬───────────────┘
                                     │
                                     ▼
                            ┌────────────────────────┐
                            │ Web enrichment         │
                            │ (best-effort, opcional)│
                            └────────┬───────────────┘
                                     │
                                     ▼
                            ┌────────────────────────┐
                            │ Valuation              │
                            │  • build_scenarios     │
                            │  • DCF (perpetuidade   │
                            │    + exit multiple)    │
                            │  • Múltiplos           │
                            │  • IRR + MOIC          │
                            └────────┬───────────────┘
                                     │
                                     ▼
                            ┌────────────────────────┐
                            │ Exportação             │
                            │  • Markdown            │
                            │  • JSON                │
                            │  • Excel (11 abas)     │
                            │  • PowerPoint (14-15)  │
                            └────────────────────────┘
```

### Estrutura do código

```
src/
├── models/                      # Schemas (TrackedField, Evidence, Gap)
│   ├── evidence.py
│   ├── company.py
│   ├── financials.py            #   non_operating: bool (E1)
│   ├── market.py                #   global_multiples_median, market_sizes
│   └── dossier.py
│
├── parsers/                     # Extração de conteúdo
│   ├── pdf_parser.py            #   Classifier sector-agnostic (E3.1)
│   ├── financial_parser.py      #   PDF DRE/Balanço
│   ├── xlsx_financial_parser.py #   XLSX → DREs (E1) + block terminator (E3.4)
│   └── profile_parser.py        #   legal_name com filter de body tokens (E3.4)
│
├── pipeline/                    # Orquestração
│   ├── classifier.py            #   Sector-agnostic + structural fallback (E3.1)
│   ├── orchestrator.py          #   run_pipeline com manual overrides (E3.5)
│   ├── assembler.py             #   Markdown/JSON
│   ├── llm_extractor.py
│   └── rules_extractor.py       #   Frank-only fallback
│
├── valuation/                   # Modelo financeiro completo
│   ├── model.py                 #   ConsolidatedModel filtra non-op (E3.3),
│   │                            #   propaga is_projected (E4)
│   ├── scenarios.py             #   3 cenários, _extract_dre_value accent-
│   │                            #   insensitive (E3.3/B7), run_full_valuation
│   ├── dcf.py                   #   Perpetuity + exit multiple
│   └── multiples.py             #   EV/EBITDA + EV/Revenue + IRR/MOIC
│
├── llm/                         # Ollama local
│   ├── client.py
│   └── prompts.py
│
├── enrichment/                  # Web enrichment (best-effort)
│   ├── fetcher.py
│   ├── sources.py               #   +3 search funcs (E3.2):
│   │                            #     market_size, competitors, multiples
│   └── enricher.py              #   skip-if-already-filled
│
├── exporters/                   # Output formats
│   ├── xlsx_exporter.py         #   11 sheets, "—" for None (E5/P7)
│   └── pptx_exporter.py         #   accent-insensitive matcher (E5/P1),
│                                #   conditional balance slide (E5/P6),
│                                #   placeholder sections (E5/P5),
│                                #   auto-derived footer page numbers
│
├── storage/
│   └── versioning.py
│
└── cli.py                       # 5 comandos + 4 manual override flags

tests/
├── _helpers.py                  # deep_diff, read_sheet, first_cell_diff
├── _regenera_synthetic.py       # XLSX builder (E6)
├── conftest.py                  # 4 session fixtures
├── fixtures/
│   ├── frank_baseline/          # Frank golden files
│   └── regenera_synthetic_baseline/  # Regenera golden files (E6)
├── test_xlsx_parser.py          # 33 tests (E1)
├── test_e2_multi_input.py       # 11 tests (E2)
├── test_e3_classifier.py        # 16 tests (E3.1)
├── test_e3_scenarios.py         # 7 tests (E3.3)
├── test_e3_4_clean.py           # 12 tests (E3.4)
├── test_e3_2_enrichment.py      # 11 tests (E3.2)
├── test_e3_5_overrides.py       # 9 tests (E3.5)
├── test_e4_irr_moic.py          # 6 tests (E4)
├── test_e5_visual.py            # 9 tests (E5)
├── test_regenera_regression.py  # 15 tests (E6)
├── test_frank_regression.py     # 11 tests
├── test_full_dossier.py
├── test_pdf_pipeline.py
└── test_financial_parser.py
```

## Privacidade e confidencialidade

O sistema foi projetado para lidar com CIMs confidenciais:

| Componente | Dados que saem da máquina | Risco |
|------------|---------------------------|-------|
| LLM (Ollama local) | Nenhum | Zero |
| Web enrichment | Apenas o nome da empresa em queries de busca | Mínimo (equivalente a googlar) |
| Manual overrides (E3.5) | Nenhum — flags do CLI ficam locais | Zero |
| Exportação | Nenhum | Zero |

**Decisão de design (E3.5):** APIs como Brave/Tavily/Serper foram avaliadas
e rejeitadas porque enviariam o nome da empresa-alvo a terceiros junto
com queries específicas tipo "concorrentes de X", efetivamente vazando
qual deal você está analisando. O conteúdo do CIM (financeiros, termos,
cap table) **nunca** sai da sua máquina.

## Limitações conhecidas

| Item | Impacto | Mitigação |
|------|---------|-----------|
| DuckDuckGo bloqueio sistêmico (HTTP 403) | Web enrichment instável | Manual overrides via flags (E3.5) |
| Concorrentes em logos/imagens no PDF | Lista parcial | Aceitar gap |
| Marcas próprias só em imagens | Lista parcial | Aceitar gap |
| Balanços ausentes em alguns CIMs | Slide PPT suprimido (E5/P6) | Pipeline lida graceful |
| `_calc_irr` retorna 0.15 quando Newton não converge | Smoke signal: IRR=15.0% + MOIC=0 | E4 corrige a causa upstream |
| Slide visual em PNG não OCRzado (Bioma timeline) | 2 de 5 eventos perdidos | Aceitar; OCR fora de escopo |
| Timeline/produtos/executivos LLM-roleta | Counts variam 3-5 entre runs | Surfa como gap quando falha |

## Roadmap

| Marco | Descrição | Status |
|-------|-----------|--------|
| Marco 0 | MVP: pipeline ponta a ponta | ✅ |
| Marco 1 | CLI com versionamento | ✅ |
| Marco 2 | LLM local (Ollama + Qwen 2.5 14B) | ✅ |
| Marco 3 | Enriquecimento web | ✅ |
| Marco 4a | Excel (11 abas) | ✅ |
| Marco 4b | PowerPoint (14-15 slides) | ✅ |
| Marco 5 | Valuation completo (DCF + Múltiplos + IRR + 3 cenários) | ✅ |
| **E1-E6** | **Generalização: 2 golden CIMs travados, 142 tests** | ✅ |
| Próximos | 3º CIM real, OCR, alternativa ao DDG (com confidencialidade) | Futuro |

## Dependências

- `pdfplumber` — PDFs (texto + tabelas)
- `openpyxl` — XLSX (parse + export)
- `python-pptx` + `matplotlib` — geração de slides com gráficos
- `typer` + `rich` — CLI com tabelas no terminal
- `requests` + `beautifulsoup4` — web scraping
- `ollama` — LLM local (Qwen 2.5 14B) para extração inteligente
- `pytest` — suite de testes

## Hardware recomendado

- **RAM**: 32GB (mínimo 16GB)
- **GPU**: NVIDIA com 16GB VRAM (ex: RTX 5080) para Qwen 2.5 14B
- **Alternativa**: modelos menores (qwen2.5:7b) rodam com 8GB VRAM
- **CPU-only**: funciona mas extração LLM fica significativamente mais lenta
