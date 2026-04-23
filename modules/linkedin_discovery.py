"""
LinkedIn Discovery — Busca de empresas via Apify LinkedIn Company Search.
Passo 1 do pipeline: encontrar prospects diretamente no LinkedIn com filtros.

Actor padrão: curious_coder/linkedin-company-search-scraper
"""

import time
from typing import Optional
import httpx

from config.settings import (
    APIFY_API_KEY,
    APIFY_ACTOR_LINKEDIN_COMPANIES,
    HUNT_LINKEDIN_MAX_RESULTS,
    CLIENTES_ATIVOS_88I,
    DESCARTADOS,
)
from modules.logger import get_logger

log = get_logger("sdr.linkedin_discovery")


class LinkedInDiscovery:
    """
    Busca empresas no LinkedIn via Apify.

    Filtros são passados como dict e encaminhados diretamente ao actor.
    Estrutura esperada — preencher em settings.LINKEDIN_HUNT_FILTERS
    ou passar direto no método search_companies():

        {
            "keywords": "last mile delivery entregadores",
            "location": "Brazil",          # texto livre ou geoUrn do LinkedIn
            "industry": [],                # ex: ["Transportation", "Logistics"]
            "companySize": [],             # ex: ["B","C","D"] (ver tabela abaixo)
            "icp_tipo": "ICP1",            # metadado interno, não vai pro actor
        }

    Tabela company size (LinkedIn):
        A = 1-10     B = 11-50    C = 51-200   D = 201-500
        E = 501-1000 F = 1001-5000 G = 5001-10000 H = 10001+
    """

    def __init__(self, apify_key: str = APIFY_API_KEY):
        self.apify_key = apify_key
        self.client = httpx.Client(timeout=120.0)

    # ───────────────────────────────────────────
    # BUSCA PRINCIPAL
    # ───────────────────────────────────────────

    def search_companies(
        self,
        filters: dict,
        max_results: int = HUNT_LINKEDIN_MAX_RESULTS,
    ) -> list[dict]:
        """
        Busca empresas no LinkedIn usando os filtros fornecidos.

        Args:
            filters: dict com chaves: keywords, location, industry, companySize, icp_tipo
            max_results: limite de resultados por query

        Returns:
            Lista de leads no formato padrão do pipeline.
        """
        icp_tipo = filters.get("icp_tipo", "ICP1")
        keywords = filters.get("keywords", "")

        log.info(f"LinkedIn search: '{keywords}' (max {max_results})")
        print(f"  🔗 LinkedIn: '{keywords}' (max {max_results})...")

        actor_input = self._build_actor_input(filters, max_results)
        actor_id = APIFY_ACTOR_LINKEDIN_COMPANIES.replace("/", "~")

        resp = self.client.post(
            f"https://api.apify.com/v2/acts/{actor_id}/runs?token={self.apify_key}",
            json=actor_input,
        )

        if resp.status_code == 402:
            print("  ⚠️  Apify sem créditos — pulando LinkedIn search.")
            return []

        try:
            resp.raise_for_status()
        except Exception as e:
            print(f"  ⚠️  Apify LinkedIn run falhou: {e}")
            return []

        run_id = resp.json().get("data", {}).get("id")
        if not run_id:
            print("  ⚠️  Apify não retornou run_id para LinkedIn search.")
            return []

        dataset_id = self._wait_for_run(run_id)
        if not dataset_id:
            return []

        items_resp = self.client.get(
            f"https://api.apify.com/v2/datasets/{dataset_id}/items?token={self.apify_key}"
        )
        raw = items_resp.json()

        leads = []
        for item in raw:
            lead = self._parse_result(item, icp_tipo)
            if lead and not self._is_excluded(lead["nome"]):
                leads.append(lead)

        print(f"  ✅ {len(leads)} empresas encontradas no LinkedIn")
        return leads

    def run_discovery(
        self,
        filters_list: list[dict],
        max_per_query: int = HUNT_LINKEDIN_MAX_RESULTS,
    ) -> list[dict]:
        """
        Executa múltiplas queries LinkedIn (uma por item em filters_list).
        Deduplica por nome normalizado + linkedin_url.

        Args:
            filters_list: lista de dicts de filtros, cada um gera uma query
            max_per_query: limite de resultados por query

        Returns:
            Lista deduplicada de leads.
        """
        all_leads: list[dict] = []
        seen: set[str] = set()

        for i, filters in enumerate(filters_list):
            label = filters.get("keywords", f"query-{i+1}")
            print(f"\n{'─'*50}")
            print(f"🔗 LinkedIn query {i+1}/{len(filters_list)}: {label}")
            print(f"{'─'*50}")

            leads = self.search_companies(filters, max_per_query)

            for lead in leads:
                dedup_key = self._dedup_key(lead)
                if dedup_key not in seen:
                    seen.add(dedup_key)
                    all_leads.append(lead)

            time.sleep(3)  # rate limit entre queries

        print(f"\n📊 LinkedIn total: {len(all_leads)} empresas únicas")
        return all_leads

    # ───────────────────────────────────────────
    # BUILD ACTOR INPUT
    # ───────────────────────────────────────────

    def _build_actor_input(self, filters: dict, max_results: int) -> dict:
        """
        Constrói o payload para o actor Apify LinkedIn.
        Campos internos (icp_tipo) são removidos antes de enviar.
        """
        actor_input: dict = {"maxItems": max_results}

        if filters.get("keywords"):
            actor_input["keywords"] = filters["keywords"]

        if filters.get("location"):
            actor_input["location"] = filters["location"]

        if filters.get("industry"):
            actor_input["industry"] = filters["industry"]

        if filters.get("companySize"):
            actor_input["companySize"] = filters["companySize"]

        # Campos extras que o actor possa aceitar (passados direto)
        for key in ("searchUrl", "cookie", "proxyConfig"):
            if filters.get(key):
                actor_input[key] = filters[key]

        return actor_input

    # ───────────────────────────────────────────
    # PARSE RESULTADO
    # ───────────────────────────────────────────

    def _parse_result(self, item: dict, icp_tipo: str) -> Optional[dict]:
        """
        Converte item do Apify LinkedIn em formato padrão de lead.
        Campos mapeados dos resultados mais comuns do actor.
        """
        nome = (item.get("name") or item.get("companyName") or "").strip()
        if not nome or len(nome) < 2:
            return None

        linkedin_url = (
            item.get("linkedinUrl")
            or item.get("url")
            or item.get("profileUrl")
            or ""
        )
        site = item.get("website") or item.get("companyWebsite") or ""
        descricao = item.get("description") or item.get("about") or ""
        employees = item.get("employeeCount") or item.get("staffCount") or item.get("employees")
        industry = item.get("industry") or item.get("industries") or ""
        if isinstance(industry, list):
            industry = ", ".join(industry)

        headquarter = item.get("headquarter") or item.get("location") or {}
        if isinstance(headquarter, dict):
            cidade = headquarter.get("city", "")
            uf = headquarter.get("state", "")
            pais = headquarter.get("country", "Brazil")
        else:
            cidade = str(headquarter)
            uf = ""
            pais = "Brazil"

        return {
            "nome": nome,
            "site": site,
            "linkedin_url": linkedin_url,
            "descricao_linkedin": descricao[:500] if descricao else "",
            "employees_linkedin": employees,
            "industry_linkedin": industry,
            "cidade": cidade,
            "uf": uf,
            "pais": pais,
            "icp_tipo": icp_tipo,
            "source": "linkedin",
            "status": "discovered",
        }

    # ───────────────────────────────────────────
    # APIFY POLLING
    # ───────────────────────────────────────────

    def _wait_for_run(self, run_id: str, timeout: int = 300) -> Optional[str]:
        """Aguarda run Apify completar com backoff exponencial."""
        start = time.time()
        wait = 5
        while time.time() - start < timeout:
            time.sleep(wait)
            resp = self.client.get(
                f"https://api.apify.com/v2/actor-runs/{run_id}?token={self.apify_key}"
            )
            data = resp.json().get("data", {})
            status = data.get("status")

            if status == "SUCCEEDED":
                return data.get("defaultDatasetId")
            if status in ("FAILED", "ABORTED", "TIMED-OUT"):
                print(f"  ❌ Apify LinkedIn run {status}")
                return None

            wait = min(wait * 1.5, 30)

        print("  ❌ Apify LinkedIn run timeout")
        return None

    # ───────────────────────────────────────────
    # HELPERS
    # ───────────────────────────────────────────

    def _dedup_key(self, lead: dict) -> str:
        """Chave de deduplicação: LinkedIn URL (preferencial) ou nome normalizado."""
        if lead.get("linkedin_url"):
            return lead["linkedin_url"].rstrip("/").lower()
        return lead["nome"].lower().strip().replace(" ", "")

    def _is_excluded(self, name: str) -> bool:
        name_lower = name.lower()
        for excluded in CLIENTES_ATIVOS_88I + DESCARTADOS:
            if excluded.lower() in name_lower:
                return True
        return False

    def close(self):
        self.client.close()
