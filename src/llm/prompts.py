"""
Prompt templates for LLM-based extraction.
Each function returns a (system_prompt, user_prompt) tuple.
"""
from __future__ import annotations

SYSTEM_EXTRACTION = (
    "Você é um analista financeiro especializado em extrair dados estruturados de documentos "
    "de transação (CIM, teasers, apresentações institucionais). "
    "Responda APENAS com JSON válido, sem explicações, sem markdown, sem texto adicional. "
    "Se uma informação não estiver presente no texto, use null. "
    "Nunca invente dados que não estão no texto."
)


def prompt_company_profile(text: str) -> tuple[str, str]:
    return SYSTEM_EXTRACTION, f"""Extraia o perfil da empresa a partir do texto abaixo.

Retorne JSON no formato:
{{
  "legal_name": "razão social completa ou null",
  "trade_name": "nome fantasia ou null",
  "description": "descrição breve da empresa (1-2 frases) ou null",
  "founding_year": 2012,
  "headquarters": "cidade, estado ou null",
  "sector": "setor de atuação ou null",
  "business_model": "descrição do modelo de negócio ou null",
  "target_audience": "descrição do público alvo ou null",
  "number_of_stores": 604,
  "number_of_employees": null
}}

TEXTO:
{text}"""


def prompt_executives(text: str) -> tuple[str, str]:
    return SYSTEM_EXTRACTION, f"""Extraia todos os executivos/diretores/sócios mencionados no texto abaixo.

ATENÇÃO CRÍTICA sobre o layout do PDF:
- Os nomes podem estar em COLUNAS SEPARADAS, não em linhas contínuas
- Exemplo do layout real no PDF:
  14+        11+         6+          10+         5+
  Celso      Gustavo     Luis        Fábio       Cesar
  Silva      Freitas     Oliveira    Nadruz      Lucchesi
  (48%)      (48%)       (1%)        (2%)        (1%)

- Neste exemplo, os executivos são:
  - Celso Silva (48%), NÃO "Celso Gustavo Silva"
  - Gustavo Freitas (48%), NÃO "Luis Freitas"
  - Luis Oliveira (1%)
  - Fábio Nadruz (2%)
  - Cesar Lucchesi (1%)

- Os números acima dos nomes (14+, 11+, etc.) são ANOS DE EXPERIÊNCIA, não parte do nome
- Cada COLUNA é um executivo: primeiro nome em cima, sobrenome embaixo, percentual abaixo
- NÃO junte nomes de colunas diferentes

ATENÇÃO sobre CARGOS e ENTIDADES:
- O grupo pode ter várias entidades: Franqueadora, Distribuidora, Holding, Lojas Próprias
- Cada executivo pode ter um cargo DIFERENTE em cada entidade
- Se o texto mencionar a entidade junto ao cargo, INCLUA a entidade no campo "role"
  Exemplo: "CEO da Distribuidora", "CFO da Franqueadora", "Fundador da Distribuidora e Franqueadora"
- Se uma pessoa é "Fundador", especifique de qual(is) entidade(s)
- Cuidado: "Fundador" e "CEO" podem ser pessoas DIFERENTES

Retorne JSON no formato:
{{
  "executives": [
    {{
      "name": "nome completo (primeiro nome + sobrenome da MESMA coluna)",
      "role": "cargo na empresa (incluir entidade se mencionada, ex: CEO da Distribuidora)",
      "entity": "qual entidade do grupo (Franqueadora, Distribuidora, Holding, etc) ou null",
      "tenure_years": 10,
      "ownership_pct": 48.0,
      "background": "breve descrição da experiência ou null"
    }}
  ]
}}

TEXTO:
{text}"""


def prompt_timeline(text: str) -> tuple[str, str]:
    return SYSTEM_EXTRACTION, f"""Extraia TODOS os marcos históricos/timeline da empresa mencionados no texto abaixo.

Procure por:
- Anos específicos (2012, 2014, 2016, 2017, 2021, 2022, 2023, 2024, 2025, etc.)
- Marcos como: início de operação, expansão, selos, prêmios, número de unidades, lançamentos
- Selos como: ABF, GPTW, Exame
- Inaugurações, novas sedes, lançamentos de produtos

Retorne JSON no formato:
{{
  "events": [
    {{
      "year": 2012,
      "description": "descrição do evento (uma frase curta e específica)"
    }}
  ]
}}

IMPORTANTE:
- Cada evento deve ter UMA descrição específica. NÃO agrupe múltiplos eventos no mesmo ano.
- Se houver vários eventos no mesmo ano, crie entradas separadas com o mesmo ano.
- Ordene por ano.
- Inclua apenas eventos com ano explícito no texto.

TEXTO:
{text}"""


def prompt_products(text: str) -> tuple[str, str]:
    return SYSTEM_EXTRACTION, f"""Extraia os produtos, marcas e soluções da empresa mencionados no texto abaixo.

ATENÇÃO sobre DEDUPLICAÇÃO:
- NÃO liste o mesmo produto/categoria mais de uma vez
- "Armações próprias", "Armações", "Armações da distribuição" são a MESMA categoria — liste apenas UMA VEZ como "Armações"
- Diferencie entre CATEGORIAS de produto (Lentes, Armações, Solar, Contato) e MARCAS PRÓPRIAS (Paola Belle, Eurolens, etc.)
- Marcas próprias devem ser listadas SEPARADAMENTE das categorias

Retorne JSON no formato:
{{
  "products": [
    {{
      "name": "nome do produto ou marca",
      "category": "categoria (ex: Lentes, Armações, Solar, Contato)",
      "revenue_share_pct": 72.9,
      "is_proprietary": true,
      "description": "breve descrição ou null"
    }}
  ]
}}

Diferencie entre produtos gerais e marcas próprias da empresa.

TEXTO:
{text}"""


def prompt_competitors(text: str) -> tuple[str, str]:
    return SYSTEM_EXTRACTION, f"""Extraia os concorrentes/competidores mencionados no texto abaixo.

REGRAS IMPORTANTES:
1. Concorrentes são REDES VAREJISTAS que competem diretamente com a empresa-alvo. 
   Exemplos no setor óptico: Óticas Carol, Chilli Beans, Óticas Diniz, Mercadão dos Óculos.
2. NÃO inclua fabricantes/fornecedores (HOYA, Carl Zeiss, etc.) como concorrentes a menos 
   que eles também operem redes de lojas no Brasil.
3. EssilorLuxottica pode ser listada SE operar lojas (Óticas Carol é do grupo EssilorLuxottica).
   Nesse caso, use o nome da rede varejista, não do grupo industrial.
4. Se houver TEXTO DE LOGOS/IMAGENS (seção "TEXTO ADICIONAL EXTRAÍDO"), use esses nomes 
   como nomes das empresas. Logos em imagens são os nomes reais dos concorrentes.
5. Se houver uma tabela com números (lojas, faturamento) mas sem nomes legíveis, e houver 
   texto de OCR disponível, associe os nomes do OCR com os números pela posição (esquerda 
   para direita).
6. O campo "investor" é o INVESTIDOR/FUNDO que investiu naquela rede, NÃO a própria rede.
7. Se houver logos identificados como "LOGO_1: nome", "LOGO_2: nome", etc., use esses 
   nomes na ordem correspondente aos dados numéricos.

Retorne JSON no formato:
{{
  "competitors": [
    {{
      "name": "nome da REDE VAREJISTA (não do fabricante)",
      "stores": 1408,
      "revenue": 887,
      "revenue_unit": "BRL MM",
      "investor": "nome do investidor/fundo ou null",
      "market_share_pct": null
    }}
  ]
}}

TEXTO:
{text}"""


def prompt_market(text: str) -> tuple[str, str]:
    return SYSTEM_EXTRACTION, f"""Extraia dados de tamanho de mercado, crescimento e fragmentação mencionados no texto abaixo.

Retorne JSON no formato:
{{
  "market_sizes": [
    {{
      "geography": "Global ou Brasil",
      "value": 172.7,
      "unit": "USD Bn ou BRL Bn",
      "year": 2029,
      "cagr": 0.033
    }}
  ],
  "fragmentation": "descrição da fragmentação do mercado ou null",
  "growth_drivers": ["driver 1", "driver 2"],
  "barriers": ["barreira 1", "barreira 2"]
}}

TEXTO:
{text}"""


def prompt_transaction(text: str) -> tuple[str, str]:
    return SYSTEM_EXTRACTION, f"""Extraia as informações sobre a transação/deal mencionadas no texto abaixo.

Retorne JSON no formato:
{{
  "transaction_type": "tipo da transação (ex: investimento minoritário) ou null",
  "target_stake_range": "faixa de participação buscada (ex: <40%) ou null",
  "advisor": "nome do assessor financeiro ou null",
  "context": "contexto e objetivo da transação (1-2 frases) ou null",
  "perimeter": "o que está incluído na transação ou null",
  "capital_needed": null,
  "use_of_proceeds": "como o capital será usado ou null"
}}

TEXTO:
{text}"""


def prompt_multiples(text: str) -> tuple[str, str]:
    return SYSTEM_EXTRACTION, f"""Extraia os múltiplos de valuation e transações precedentes mencionados no texto abaixo.

Retorne JSON no formato:
{{
  "precedent_transactions": [
    {{
      "date": "mês/ano",
      "buyer": "comprador",
      "target": "empresa alvo",
      "stake_pct": 100,
      "value": "~USD 920MM",
      "ev_revenue": 0.9,
      "ev_ebitda": 10.2
    }}
  ],
  "median_ev_revenue": 1.8,
  "median_ev_ebitda": 11.0
}}

TEXTO:
{text}"""