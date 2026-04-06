"""
Web enrichment orchestrator.
Takes a dossier with gaps, searches the web, and fills what it can.
"""
from __future__ import annotations
from ..models.evidence import Evidence, TrackedField, Gap
from ..models.dossier import Dossier
from ..llm.client import OllamaClient
from .sources import (
    scrape_reclame_aqui,
    search_jusbrasil,
    search_company_info,
    search_google_reviews,
)


SYSTEM_ENRICH = (
    "Você é um analista financeiro. Extraia dados estruturados do texto de pesquisa web abaixo. "
    "Responda APENAS com JSON válido, sem explicações. "
    "Se a informação não estiver presente, use null."
)


def _evidence(source: str, url: str = "", confidence: float = 0.6) -> Evidence:
    return Evidence(
        source_file=source, page=0, excerpt=url[:300],
        confidence=confidence, extraction_method="web_enrichment",
    )


def enrich_dossier(
    dossier: Dossier,
    use_llm: bool = True,
    verbose: bool = False,
) -> Dossier:
    """Enrich a dossier by searching the web for gaps marked with requires_internet.

    Args:
        dossier: The dossier to enrich
        use_llm: Whether to use LLM to process web results
        verbose: Print progress
    """
    company_name = dossier.company.profile.trade_name.value or dossier.metadata.target_company
    # Strip common prefixes that mess up search queries
    for prefix in ["Grupo ", "Rede ", "Holding "]:
        if company_name and company_name.startswith(prefix):
            company_name = company_name[len(prefix):]
            break
    if not company_name:
        if verbose:
            print("  ⚠️  No company name available for web enrichment")
        return dossier

    if verbose:
        print(f"\n  🌐 Enriquecimento web para: {company_name}")

    # Initialize LLM client if needed
    client = None
    if use_llm:
        client = OllamaClient()
        if not client.is_available():
            if verbose:
                print("  ⚠️  Ollama não disponível. Salvando dados brutos.")
            client = None

    # --- REPUTATION (Reclame Aqui + Google Reviews) ---
    _enrich_reputation(dossier, company_name, client, verbose)

    # --- LITIGATION (Jusbrasil) ---
    _enrich_litigation(dossier, company_name, client, verbose)

    # --- COMPANY INFO (legal name, HQ, employees) ---
    _enrich_company_info(dossier, company_name, client, verbose)

    # Update gaps: remove filled ones, update remaining
    _update_gaps(dossier, verbose)

    return dossier


def _enrich_reputation(
    dossier: Dossier, company_name: str,
    client: OllamaClient | None, verbose: bool,
):
    """Enrich reputation data from Reclame Aqui and Google Reviews."""
    if verbose:
        print(f"\n  [Web] Buscando reputação...")

    ra_data = scrape_reclame_aqui(company_name, verbose)
    google_data = search_google_reviews(company_name, verbose)

    combined_text = ""
    sources = []
    if ra_data:
        combined_text += f"RECLAME AQUI: {ra_data['text']}\n"
        sources.append(ra_data["source"])
    if google_data:
        combined_text += f"GOOGLE REVIEWS: {google_data['text']}\n"
        sources.append(google_data["source"])

    if not combined_text:
        if verbose:
            print("    ❌ Nenhum dado de reputação encontrado")
        return

    if client:
        prompt = f"""Extraia a reputação da empresa a partir dos dados de pesquisa web abaixo.

Retorne JSON no formato:
{{
  "reclame_aqui_score": 7.8,
  "reclame_aqui_complaints": 342,
  "reclame_aqui_resolution_rate": 0.89,
  "reclame_aqui_status": "Bom",
  "google_rating": 4.2,
  "google_reviews_count": 150,
  "summary": "breve resumo da reputação (1-2 frases)"
}}

DADOS:
{combined_text[:4000]}"""

        data = client.extract_json(prompt, SYSTEM_ENRICH)
        if data and isinstance(data, dict):
            source_name = " + ".join(sources)
            url = (ra_data or google_data or {}).get("url", "")

            # Store as a structured reputation field
            dossier.company.profile.reputation = TrackedField.filled(
                data, _evidence(source_name, url)
            )
            if verbose:
                summary = data.get("summary", "dados extraídos")
                print(f"    ✅ Reputação: {summary}")
            return

    # Fallback: store raw text without LLM processing
    source_name = " + ".join(sources)
    url = (ra_data or google_data or {}).get("url", "")
    dossier.company.profile.reputation = TrackedField.filled(
        {"raw_text": combined_text[:2000]},
        _evidence(source_name, url, confidence=0.4)
    )
    if verbose:
        print(f"    ✅ Reputação: dados brutos salvos (sem LLM)")


def _enrich_litigation(
    dossier: Dossier, company_name: str,
    client: OllamaClient | None, verbose: bool,
):
    """Enrich litigation data from Jusbrasil."""
    if verbose:
        print(f"\n  [Web] Buscando contencioso...")

    jus_data = search_jusbrasil(company_name, verbose)

    if not jus_data:
        if verbose:
            print("    ❌ Nenhum dado de contencioso encontrado")
        return

    if client:
        prompt = f"""Extraia informações sobre processos judiciais e contencioso da empresa a partir dos dados de pesquisa abaixo.

Retorne JSON no formato:
{{
  "total_lawsuits_found": 15,
  "lawsuit_types": ["trabalhista", "cível", "tributário"],
  "notable_cases": ["descrição breve caso 1", "descrição breve caso 2"],
  "risk_level": "baixo/médio/alto",
  "summary": "breve resumo do contencioso (1-2 frases)"
}}

DADOS:
{jus_data['text'][:4000]}"""

        data = client.extract_json(prompt, SYSTEM_ENRICH)
        if data and isinstance(data, dict):
            dossier.company.profile.litigation = TrackedField.filled(
                data, _evidence("jusbrasil", jus_data.get("url", ""))
            )
            if verbose:
                summary = data.get("summary", "dados extraídos")
                print(f"    ✅ Contencioso: {summary}")
            return

    # Fallback
    dossier.company.profile.litigation = TrackedField.filled(
        {"raw_text": jus_data["text"][:2000]},
        _evidence("jusbrasil", jus_data.get("url", ""), confidence=0.4)
    )
    if verbose:
        print(f"    ✅ Contencioso: dados brutos salvos")


def _enrich_company_info(
    dossier: Dossier, company_name: str,
    client: OllamaClient | None, verbose: bool,
):
    """Enrich basic company info (legal name, HQ, employees)."""
    profile = dossier.company.profile

    # Check which fields need enrichment
    fields_needed = []
    if profile.legal_name.is_empty:
        fields_needed.append("legal_name")
    if profile.headquarters.is_empty:
        fields_needed.append("headquarters")
    if profile.number_of_employees.is_empty:
        fields_needed.append("number_of_employees")

    if not fields_needed:
        return

    if verbose:
        print(f"\n  [Web] Buscando info da empresa: {fields_needed}")

    info_data = search_company_info(company_name, fields_needed, verbose)

    if not info_data:
        if verbose:
            print("    ❌ Nenhum dado de empresa encontrado")
        return

    if client:
        prompt = f"""Extraia informações cadastrais da empresa "{company_name}" a partir dos dados de pesquisa abaixo.

Retorne JSON no formato:
{{
  "legal_name": "razão social completa ou null",
  "headquarters": "cidade, estado ou null",
  "number_of_employees": 500,
  "cnpj": "XX.XXX.XXX/XXXX-XX ou null"
}}

DADOS:
{info_data['text'][:4000]}"""

        data = client.extract_json(prompt, SYSTEM_ENRICH)
        if data and isinstance(data, dict):
            ev = _evidence("web_search", info_data.get("url", ""))

            if data.get("legal_name") and profile.legal_name.is_empty:
                profile.legal_name = TrackedField.filled(data["legal_name"], ev)
                if verbose:
                    print(f"    ✅ Razão social: {data['legal_name']}")

            if data.get("headquarters") and profile.headquarters.is_empty:
                profile.headquarters = TrackedField.filled(data["headquarters"], ev)
                if verbose:
                    print(f"    ✅ Sede: {data['headquarters']}")

            if data.get("number_of_employees") and profile.number_of_employees.is_empty:
                profile.number_of_employees = TrackedField.filled(data["number_of_employees"], ev)
                if verbose:
                    print(f"    ✅ Funcionários: {data['number_of_employees']}")


def _update_gaps(dossier: Dossier, verbose: bool):
    """Remove gaps that were filled by enrichment."""
    profile = dossier.company.profile
    filled_paths = set()

    # Check which fields were filled
    if hasattr(profile, 'reputation') and isinstance(getattr(profile, 'reputation', None), TrackedField):
        if profile.reputation.is_filled:
            filled_paths.add("company.reputation")

    if hasattr(profile, 'litigation') and isinstance(getattr(profile, 'litigation', None), TrackedField):
        if profile.litigation.is_filled:
            filled_paths.add("company.litigation")

    if profile.legal_name.is_filled:
        filled_paths.add("company.profile.legal_name")
    if profile.headquarters.is_filled:
        filled_paths.add("company.profile.headquarters")
    if profile.number_of_employees.is_filled:
        filled_paths.add("company.profile.number_of_employees")
        filled_paths.add("company.employee_count")

    # Remove filled gaps
    original_count = len(dossier.gaps)
    dossier.gaps = [g for g in dossier.gaps if g.field_path not in filled_paths]
    removed = original_count - len(dossier.gaps)

    if verbose and removed:
        print(f"\n  ✅ {removed} gaps preenchidos pelo enriquecimento web")