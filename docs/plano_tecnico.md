# Projeto Dossiê — Plano Técnico e de Produto (v0.1)

## 1. Visão Geral

**O que estamos construindo:** um sistema local em Python que recebe documentos de transação (PDF, PPTX, DOCX) e gera um dossiê estruturado com rastreabilidade, detecção de lacunas e versionamento.

**MVP:** pipeline ponta a ponta — do arquivo bruto ao dossiê montado — usando apenas o conteúdo do documento de entrada. Sem enriquecimento por internet.

**Primeiro caso de teste:** CIM do Projeto Frank (Mercadão dos Óculos).

**Abordagem de desenvolvimento:** programação assistida por IA (Claude/Copilot).

---

## 2. Arquitetura de Alto Nível

```
┌─────────────┐     ┌──────────────┐     ┌───────────────┐     ┌────────────────┐
│  INGESTION   │────▶│   PARSING    │────▶│ SEGMENTATION  │────▶│ CLASSIFICATION │
│ (ler arquivo)│     │ (extrair     │     │ (dividir em   │     │ (mapear blocos │
│              │     │  conteúdo)   │     │  blocos)      │     │  aos capítulos)│
└─────────────┘     └──────────────┘     └───────────────┘     └────────────────┘
                                                                        │
                                                                        ▼
┌─────────────┐     ┌──────────────┐     ┌───────────────┐     ┌────────────────┐
│  VERSIONING  │◀────│   ASSEMBLY   │◀────│  GAP ANALYSIS │◀────│  EXTRACTION    │
│ (salvar com  │     │ (montar      │     │ (o que falta?)│     │ (extrair dados │
│  snapshot)   │     │  dossiê)     │     │               │     │  estruturados) │
└─────────────┘     └──────────────┘     └───────────────┘     └────────────────┘
```

### Princípios de design

1. **Schema-first**: o modelo do dossiê é o contrato central. Todo o resto serve ele.
2. **Rastreabilidade nativa**: cada dado extraído carrega sua fonte (arquivo, página, trecho, confiança).
3. **LLM como ferramenta, não como mágica**: o LLM classifica e extrai; a lógica de negócio (gaps, scores, validação) é código próprio.
4. **Modular**: cada estágio do pipeline é independente e testável.
5. **Formato antes de conteúdo**: o sistema sabe a forma do dossiê mesmo antes de ter dados.

---

## 3. Modelo de Dados (Schema do Dossiê)

Este é o coração do sistema. Cada capítulo do dossiê é um schema Pydantic com campos tipados.

### 3.1 Conceitos transversais

```python
# Toda informação extraída tem uma evidência
class Evidence:
    source_file: str          # "Projeto_Frank_CIM.pdf"
    page: int | None          # 29
    excerpt: str              # trecho relevante (máx 500 chars)
    confidence: float         # 0.0 a 1.0 (atribuído pelo sistema)
    extraction_method: str    # "llm_extraction" | "table_parse" | "manual"

# Todo campo do dossiê tem um status
class FieldStatus:
    status: Literal["filled", "partial", "empty", "conflicting"]
    evidences: list[Evidence]
    notes: str | None         # observações do sistema

# Um campo rastreado = valor + metadados
class TrackedField[T]:
    value: T | None
    field_status: FieldStatus
```

### 3.2 Capítulos do Dossiê (resumo)

| Capítulo | Subcapítulos principais | Complexidade MVP |
|----------|------------------------|-----------------|
| **1. Sobre a Empresa** | Descrição, histórico, sócios, produtos, quadro funcional, diretoria | Média |
| **2. Financeiro** | DRE, balanço, índices, projeções | Alta |
| **3. Mercado** | Tamanho, crescimento, players, Five Forces, SWOT | Média |
| **4. Transação** | Capital necessário, OPEX/CAPEX, participação | Média |
| **5. Deal** | Estrutura sugerida ou reação a proposta | Baixa (interpretativo) |
| **6. Reputação & Contencioso** | Reclame Aqui, processos, passivos | Nula no MVP (depende de internet) |
| **7. Valuation** | Modelo financeiro, múltiplos, DCF, cenários | Fase posterior |

**Para o MVP, os capítulos 1, 2, 3 e 4 são o foco.** Os capítulos 5, 6 e 7 são mapeados como lacunas.

### 3.3 Schema detalhado dos capítulos prioritários

```python
# --- CAPÍTULO 1: SOBRE A EMPRESA ---
class CompanyProfile:
    legal_name: TrackedField[str]
    trade_name: TrackedField[str]
    description: TrackedField[str]
    founding_year: TrackedField[int]
    headquarters: TrackedField[str]
    sector: TrackedField[str]
    business_model: TrackedField[str]       # franquia, varejo, etc.
    target_audience: TrackedField[str]
    number_of_stores: TrackedField[int]
    number_of_employees: TrackedField[int]
    
class TimelineEvent:
    year: int
    description: str
    evidence: Evidence

class Shareholder:
    name: str
    role: str | None
    ownership_pct: float | None
    evidence: Evidence

class Executive:
    name: str
    role: str
    tenure_years: int | None
    background: str | None
    ownership_pct: float | None
    evidence: Evidence

class Product:
    name: str
    category: str                # armações, lentes, solar, etc.
    description: str | None
    revenue_share_pct: float | None
    evidence: Evidence

class CompanyChapter:
    profile: CompanyProfile
    timeline: list[TimelineEvent]
    shareholders: list[Shareholder]
    executives: list[Executive]
    products: list[Product]
    brands: list[TrackedField[str]]

# --- CAPÍTULO 2: FINANCEIRO ---
class DRELine:
    label: str                   # "Receita Bruta", "EBITDA", etc.
    values: dict[str, float]     # {"2021": 48000, "2022": 65000, ...}
    is_projected: dict[str, bool]
    evidence: Evidence

class BalanceSheetLine:
    label: str
    values: dict[str, float]
    evidence: Evidence

class FinancialMetrics:
    ebitda_margin: TrackedField[dict[str, float]]
    net_margin: TrackedField[dict[str, float]]
    net_debt: TrackedField[dict[str, float]]
    leverage_ratio: TrackedField[dict[str, float]]  # dívida líquida / EBITDA

class FinancialChapter:
    dre_consolidated: list[DRELine]
    dre_franqueadora: list[DRELine]
    dre_distribuidora: list[DRELine]
    dre_lojas_proprias: list[DRELine]
    balance_franqueadora: list[BalanceSheetLine]
    balance_distribuidora: list[BalanceSheetLine]
    balance_lojas_proprias: list[BalanceSheetLine]
    metrics: FinancialMetrics
    capex_projection: TrackedField[dict[str, float]]
    dividend_projection: TrackedField[dict[str, float]]

# --- CAPÍTULO 3: MERCADO ---
class MarketSize:
    geography: str              # "Brasil", "Global"
    value: float
    unit: str                   # "BRL Bn", "USD Bn"
    year: int
    cagr: float | None
    evidence: Evidence

class Competitor:
    name: str
    stores: int | None
    revenue: float | None
    revenue_unit: str | None
    market_share_pct: float | None
    investor: str | None
    strengths: list[str]
    weaknesses: list[str]
    evidence: Evidence

class MarketChapter:
    market_sizes: list[MarketSize]
    growth_drivers: list[TrackedField[str]]
    market_fragmentation: TrackedField[str]
    competitors: list[Competitor]
    value_chain: TrackedField[str]
    barriers_to_entry: list[TrackedField[str]]
    recent_transactions: list[dict]       # transações M&A

# --- CAPÍTULO 4: TRANSAÇÃO ---
class TransactionChapter:
    context: TrackedField[str]
    transaction_type: TrackedField[str]    # "investimento minoritário"
    target_stake_range: TrackedField[str]  # "<40%"
    capital_needed: TrackedField[float]
    opex_component: TrackedField[float]
    capex_component: TrackedField[float]
    use_of_proceeds: TrackedField[str]
    advisor: TrackedField[str]

# --- DOSSIÊ COMPLETO ---
class Dossier:
    metadata: DossierMetadata
    company: CompanyChapter
    financials: FinancialChapter
    market: MarketChapter
    transaction: TransactionChapter
    gaps: list[Gap]               # lacunas detectadas
    version: str
    created_at: datetime
    updated_at: datetime
```

### 3.4 Modelo de Lacunas (Gaps)

```python
class Gap:
    chapter: str                  # "company", "financials", etc.
    field_path: str               # "company.profile.number_of_employees"
    severity: Literal["critical", "important", "nice_to_have"]
    description: str              # "Número de funcionários não encontrado"
    suggested_source: str | None  # "Consultar RAIS ou LinkedIn"
    requires_internet: bool       # True = não pode ser preenchido sem pesquisa
```

---

## 4. Decisões Técnicas

### 4.1 Stack do MVP

| Componente | Escolha | Justificativa |
|-----------|---------|---------------|
| Linguagem | Python 3.11+ | Ecossistema de parsing, LLM, e dados |
| Schemas | Pydantic v2 | Validação, serialização, type-safety |
| PDF parsing | PyMuPDF (fitz) + pdfplumber | fitz para texto com layout; pdfplumber para tabelas |
| PPTX parsing | python-pptx | Maduro e estável |
| DOCX parsing | python-docx | Maduro e estável |
| LLM | Anthropic API (Claude Sonnet) | Custo-benefício para classificação e extração |
| Storage | JSON + filesystem | Simples, versionável, inspecionável |
| CLI | typer ou click | Interface leve |
| Testes | pytest | Padrão |
| Gestão de deps | uv ou poetry | Modernos, confiáveis |

### 4.2 Estratégia de Parsing de PDF

O CIM do Projeto Frank é um PDF de apresentação. Isso traz desafios específicos:

**Problemas reais:**
- Layout multi-coluna (não é texto linear)
- Tabelas financeiras com formatação complexa
- Gráficos (dados visuais que não extraem como texto)
- Watermarks ("Cópia para Trigger") poluindo o texto extraído
- Headers/footers repetidos ("PRIVATE AND CONFIDENTIAL")

**Estratégia em camadas:**

```
Camada 1: Extração bruta
├── PyMuPDF → texto por página com coordenadas
├── pdfplumber → tabelas estruturadas
└── Limpeza → remover watermarks, headers, footers

Camada 2: Estruturação
├── Identificar tipo de slide (título, conteúdo, tabela, gráfico)
├── Agrupar texto por blocos visuais (usando coordenadas)
└── Associar tabelas ao contexto textual da página

Camada 3: Interpretação (LLM)
├── Classificar cada página/bloco → capítulo do dossiê
├── Extrair dados estruturados de blocos complexos
└── Interpretar contexto quando o layout é ambíguo
```

**Decisão importante:** para gráficos (como os charts de faturamento), o MVP aceita a limitação. Gráficos que têm os dados em tabelas adjacentes são capturados via tabela. Gráficos isolados são registrados como "dado visual não extraído" nas lacunas.

### 4.3 Uso do LLM no Pipeline

O LLM é usado em 3 pontos específicos:

| Estágio | Input | Output | Prompt Strategy |
|---------|-------|--------|----------------|
| **Classificação** | Texto de uma página/bloco | Capítulo(s) do dossiê | Few-shot com exemplos do schema |
| **Extração** | Bloco de texto + schema do capítulo | Dados estruturados (JSON) | Schema-guided extraction |
| **Interpretação** | Tabela bruta + contexto | Dados financeiros limpos | Chain-of-thought com validação |

**Custo estimado por CIM processado:** ~$0.50-2.00 (Sonnet, ~50 páginas, ~3 chamadas por página média).

### 4.4 Versionamento

Cada execução do pipeline gera um snapshot:

```
data/
├── inputs/
│   └── projeto_frank/
│       └── Projeto_Frank_CIM.pdf
├── outputs/
│   └── projeto_frank/
│       └── dossier_v1.json         # dossiê atual
├── versions/
│   └── projeto_frank/
│       ├── v001_2026-03-30T14:00.json
│       ├── v002_2026-03-31T10:00.json  # após re-processamento
│       └── changelog.json              # diff entre versões
```

---

## 5. Estrutura de Pastas do Projeto

```
dossie-engine/
│
├── src/
│   ├── __init__.py
│   │
│   ├── models/                    # Schemas Pydantic (o coração)
│   │   ├── __init__.py
│   │   ├── dossier.py            # Dossiê completo + metadata
│   │   ├── company.py            # Cap. 1: Sobre a Empresa
│   │   ├── financials.py         # Cap. 2: Financeiro
│   │   ├── market.py             # Cap. 3: Mercado
│   │   ├── transaction.py        # Cap. 4: Transação
│   │   ├── evidence.py           # Evidence, TrackedField, FieldStatus
│   │   └── gaps.py               # Gap model
│   │
│   ├── pipeline/                  # Estágios do pipeline
│   │   ├── __init__.py
│   │   ├── orchestrator.py       # Orquestra o pipeline ponta a ponta
│   │   ├── ingestion.py          # Leitura de arquivos
│   │   ├── parsing.py            # Coordena parsers por formato
│   │   ├── segmentation.py       # Divide conteúdo em blocos
│   │   ├── classification.py     # Mapeia blocos → capítulos
│   │   ├── extraction.py         # Extrai dados estruturados
│   │   ├── gap_analysis.py       # Detecta lacunas
│   │   └── assembly.py           # Monta dossiê final
│   │
│   ├── parsers/                   # Parsers específicos por formato
│   │   ├── __init__.py
│   │   ├── base.py               # Interface base
│   │   ├── pdf_parser.py         # PyMuPDF + pdfplumber
│   │   ├── pptx_parser.py        # python-pptx
│   │   └── docx_parser.py        # python-docx
│   │
│   ├── llm/                       # Integração com LLM
│   │   ├── __init__.py
│   │   ├── client.py             # Wrapper Anthropic API
│   │   ├── prompts/              # Templates de prompt
│   │   │   ├── classification.py
│   │   │   ├── extraction.py
│   │   │   └── financial.py
│   │   └── response_parser.py    # Parse de respostas JSON do LLM
│   │
│   ├── storage/                   # Persistência e versionamento
│   │   ├── __init__.py
│   │   ├── file_store.py         # Salvar/carregar dossiês
│   │   └── versioning.py         # Snapshots e changelog
│   │
│   ├── validators/                # Validação de dados extraídos
│   │   ├── __init__.py
│   │   └── financial_validator.py # Checar consistência DRE/balanço
│   │
│   └── cli.py                     # Interface de linha de comando
│
├── tests/
│   ├── __init__.py
│   ├── fixtures/                  # Arquivos de teste
│   │   └── projeto_frank_sample.pdf
│   ├── test_models.py
│   ├── test_parsing.py
│   ├── test_classification.py
│   ├── test_extraction.py
│   └── test_pipeline.py
│
├── data/                          # Dados de trabalho (gitignored)
│   ├── inputs/
│   ├── outputs/
│   └── versions/
│
├── config/
│   ├── settings.py               # Configurações do sistema
│   └── dossier_template.yaml     # Definição dos capítulos e campos esperados
│
├── docs/
│   └── plano_tecnico.md
│
├── pyproject.toml
├── .env.example                   # ANTHROPIC_API_KEY
├── .gitignore
└── README.md
```

---

## 6. Backlog do MVP

### Sprint 0 — Fundação (3-4 dias)

| # | Tarefa | Critério de aceite |
|---|--------|--------------------|
| 0.1 | Setup do projeto (pyproject.toml, estrutura de pastas, deps) | `uv run python -c "import src"` funciona |
| 0.2 | Definir models Pydantic: Evidence, TrackedField, FieldStatus, Gap | Models instanciam e serializam para JSON |
| 0.3 | Definir models dos capítulos: CompanyChapter, FinancialChapter, MarketChapter, TransactionChapter | Models completos com todos os campos do dossiê |
| 0.4 | Definir Dossier (modelo raiz) e DossierMetadata | Dossiê vazio instancia com todos os capítulos zerados |
| 0.5 | Definir dossier_template.yaml (campos esperados por capítulo com severidade) | Arquivo YAML que o gap_analysis usa como referência |
| 0.6 | Setup do LLM client (wrapper Anthropic) | Chamada teste retorna resposta |
| 0.7 | CLI básico: `dossie process <arquivo>` | Comando existe e aceita arquivo |

### Sprint 1 — Parsing & Segmentação (5-7 dias)

| # | Tarefa | Critério de aceite |
|---|--------|--------------------|
| 1.1 | PDF parser: extração de texto por página (PyMuPDF) | Texto de cada página do CIM extraído |
| 1.2 | PDF parser: extração de tabelas (pdfplumber) | Tabelas DRE/balanço extraídas como listas de dicts |
| 1.3 | Limpeza de texto: remover watermarks, headers, footers | Texto limpo sem "PRIVATE AND CONFIDENTIAL", sem watermark |
| 1.4 | Segmentação por página/slide | Cada página vira um `ContentBlock` com texto, tabelas, metadata |
| 1.5 | Detecção de tipo de página (título, conteúdo, tabela, gráfico) | Tipo atribuído a cada página |
| 1.6 | Testar com Projeto Frank: todas as 53 páginas parseadas | Relatório de qualidade do parsing por página |

### Sprint 2 — Classificação & Extração (5-7 dias)

| # | Tarefa | Critério de aceite |
|---|--------|--------------------|
| 2.1 | Classificação de blocos via LLM (página → capítulo) | Cada página do CIM mapeada ao capítulo correto |
| 2.2 | Extração: CompanyProfile (nome, descrição, setor, etc.) | Dados da empresa extraídos do CIM com evidências |
| 2.3 | Extração: Timeline (marcos históricos) | Timeline extraída da pág. 28 |
| 2.4 | Extração: Shareholders e Executives | Sócios e diretoria extraídos da pág. 30 |
| 2.5 | Extração: Products e Brands | Produtos e marcas extraídos das págs. 34-37 |
| 2.6 | Extração: Competitors | Top 5 concorrentes extraídos da pág. 24 |
| 2.7 | Extração: MarketSize | Dados de mercado extraídos das págs. 14, 22 |
| 2.8 | Extração: TransactionContext | Dados da transação extraídos da pág. 10 |
| 2.9 | Testar com Projeto Frank: dados extraídos vs. manual | Conferir 20 campos-chave manualmente |

### Sprint 3 — Financeiro (5-7 dias)

| # | Tarefa | Critério de aceite |
|---|--------|--------------------|
| 3.1 | Parser especializado para tabelas DRE | DRE franqueadora (pág. 47) parseado em DRELine[] |
| 3.2 | Parser especializado para tabelas Balanço | Balanço franqueadora (pág. 48) parseado |
| 3.3 | DRE distribuidora e lojas próprias | Págs. 49-51 parseadas |
| 3.4 | Balanço distribuidora e lojas próprias | Págs. 50, 52 parseadas |
| 3.5 | Cálculo de métricas: margem EBITDA, margem líquida, alavancagem | Métricas calculadas e validadas |
| 3.6 | Validação cruzada: total DRE = soma por unidade | Validator reporta inconsistências |
| 3.7 | Captura de projeções (CAPEX, dividendos) | Dados das págs. 46 extraídos |

### Sprint 4 — Gap Analysis, Assembly & Versioning (3-5 dias)

| # | Tarefa | Critério de aceite |
|---|--------|--------------------|
| 4.1 | Gap analysis: comparar dossiê preenchido vs. template | Lista de gaps com severidade |
| 4.2 | Assembly: gerar dossiê em Markdown | Arquivo .md legível com todos os capítulos |
| 4.3 | Assembly: gerar dossiê em JSON | Arquivo .json com schema completo |
| 4.4 | Versioning: salvar snapshots | Snapshot salvo com timestamp |
| 4.5 | CLI completo: `dossie process`, `dossie gaps`, `dossie show` | Comandos funcionais |
| 4.6 | Testar pipeline ponta a ponta com Projeto Frank | CIM entra, dossiê sai, gaps listados |

---

## 7. Roadmap Pós-MVP

| Fase | Escopo | Dependência |
|------|--------|-------------|
| **v0.2** — Enriquecimento | Web search para preencher gaps (APIs, scraping) | API keys, definição de fontes |
| **v0.3** — Multi-input | Aceitar múltiplos arquivos e consolidar no mesmo dossiê | Lógica de merge e conflito |
| **v0.4** — Score de qualidade | Score por capítulo, por campo, por fonte | Critérios técnicos definidos |
| **v0.5** — Valuation básico | Modelo financeiro com premissas variáveis, múltiplos | Dados financeiros limpos |
| **v0.6** — Output PPT/Excel | Gerar apresentação e planilha a partir do dossiê | Templates definidos |
| **v0.7** — Cenários | Caso base/pessimista/otimista, "what needs to be true" | Modelo financeiro pronto |
| **v1.0** — Sistema completo | Todos os capítulos com profundidade, interface desktop | Maturidade do core |

---

## 8. Riscos e Mitigações

| Risco | Probabilidade | Impacto | Mitigação |
|-------|--------------|---------|-----------|
| PDF parsing de baixa qualidade (layouts complexos) | Alta | Alto | Fallback para LLM com visão (enviar página como imagem) |
| Tabelas financeiras extraídas com erros | Média | Alto | Validação cruzada automática + flag de revisão humana |
| LLM hallucina dados que não existem no documento | Média | Alto | Sempre vincular extração à evidência; nunca aceitar dado sem trecho-fonte |
| Custo de LLM alto em uso repetido | Baixa | Médio | Cache de respostas; processar só o que mudou |
| Scope creep (querer fazer tudo no MVP) | Alta | Alto | Backlog priorizado; capítulos 5-7 são explicitamente fora do MVP |
| Formato de input muito diferente do Projeto Frank | Média | Médio | Parsers modulares; testar com 2-3 CIMs diferentes na v0.2 |

---

## 9. Próximos Passos Imediatos

### Esta semana: Sprint 0

1. **Criar o projeto** — estrutura de pastas, pyproject.toml, dependências
2. **Implementar os models Pydantic** — começando por `evidence.py` e `company.py`
3. **Testar o parsing do CIM** — rodar PyMuPDF e pdfplumber no PDF do Projeto Frank para entender a qualidade da extração bruta
4. **Configurar o LLM client** — wrapper simples para a API da Anthropic

### O primeiro teste concreto (fim da semana):

```bash
# Resultado esperado: texto de cada página + tabelas identificadas
dossie parse data/inputs/Projeto_Frank_CIM.pdf --output data/debug/parsing_report.json
```

Este relatório de parsing é o primeiro deliverable tangível. Ele mostra:
- Quantas páginas foram parseadas
- Texto extraído por página (com qualidade)
- Tabelas encontradas (com estrutura)
- Páginas problemáticas (gráficos, layouts complexos)

A partir dele, calibramos o restante do pipeline.

---

## 10. Definição de "Pronto" para o MVP

O MVP está pronto quando:

- [ ] O CIM do Projeto Frank é processado ponta a ponta
- [ ] O dossiê gerado contém os capítulos 1-4 com dados extraídos
- [ ] Cada dado tem evidência (fonte, página, trecho)
- [ ] As lacunas são listadas com severidade
- [ ] Os dados financeiros (DRE, balanço) estão estruturados e validados
- [ ] O dossiê é salvo em JSON e Markdown
- [ ] O pipeline roda via CLI
- [ ] O tempo de processamento é < 5 minutos
- [ ] A qualidade é verificável: um analista consegue comparar dossiê vs. CIM
