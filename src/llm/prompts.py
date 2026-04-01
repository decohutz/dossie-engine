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

ATENÇÃO: os nomes podem estar em linhas separadas (ex: "Celso" em uma linha e "Silva" na seguinte).
Junte primeiro nome e sobrenome mesmo que estejam em linhas diferentes.

Retorne JSON no formato:
{{
  "executives": [
    {{
      "name": "nome completo",
      "role": "cargo na empresa",
      "tenure_years": 10,
      "ownership_pct": 48.0,
      "background": "breve descrição da experiência ou null"
    }}
  ]
}}

TEXTO:
{text}"""


def prompt_timeline(text: str) -> tuple[str, str]:
    return SYSTEM_EXTRACTION, f"""Extraia os marcos históricos/timeline da empresa mencionados no texto abaixo.

Retorne JSON no formato:
{{
  "events": [
    {{
      "year": 2012,
      "description": "descrição do evento"
    }}
  ]
}}

Ordene por ano. Inclua apenas eventos com ano explícito no texto.

TEXTO:
{text}"""


def prompt_products(text: str) -> tuple[str, str]:
    return SYSTEM_EXTRACTION, f"""Extraia os produtos, marcas e soluções da empresa mencionados no texto abaixo.

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

ATENÇÃO: alguns nomes de empresas podem ser logos/imagens e não aparecer como texto.
Se houver uma tabela com números (lojas, faturamento) mas sem nomes, extraia os números
e coloque "Empresa não identificada (posição N)" como nome.

Retorne JSON no formato:
{{
  "competitors": [
    {{
      "name": "nome da empresa",
      "stores": 1408,
      "revenue": 887,
      "revenue_unit": "BRL MM",
      "investor": "nome do investidor ou null",
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