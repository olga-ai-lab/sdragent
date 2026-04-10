"""
Discovery Module — Busca de leads via Apify Google Maps e BrasilAPI.
Custo estimado: $20-40 total para 500+ empresas (Apify).
BrasilAPI: gratuito.
"""

import time
import hashlib
import json
from typing import Optional
import httpx
from config.settings import (
    APIFY_API_KEY, ICP_DEFINITIONS, CIDADES_ALVO, API_ENDPOINTS,
    CLIENTES_ATIVOS_88I, DESCARTADOS,
)


class LeadDiscovery:

    def __init__(self, apify_key: str = APIFY_API_KEY):
        self.apify_key = apify_key
        self.client = httpx.Client(timeout=120.0)
        self.discovered: list[dict] = []

    # ───────────────────────────────────────────
    # APIFY GOOGLE MAPS SCRAPER
    # ───────────────────────────────────────────

    def search_apify_maps(
        self,
        query: str,
        cidade: str,
        max_results: int = 50,
        icp: str = "ICP1",
    ) -> list[dict]:
        """
        Busca empresas no Google Maps via Apify.
        Actor: drobnikj~crawler-google-places
        """
        search_term = f"{query} {cidade}"
        print(f"  🔍 Apify Maps: '{search_term}' (max {max_results})...")

        run_input = {
            "searchStringsArray": [search_term],
            "maxCrawledPlacesPerSearch": max_results,
            "language": "pt-BR",
            "includeWebResults": False,
        }

        # Start the actor run
        resp = self.client.post(
            f"https://api.apify.com/v2/acts/drobnikj~crawler-google-places/runs?token={self.apify_key}",
            json=run_input,
        )
        if resp.status_code == 402:
            print(f"  ⚠️  Apify sem créditos — pulando query.")
            return []
        resp.raise_for_status()        
        run_data = resp.json().get("data", {})
        run_id = run_data.get("id")

        if not run_id:
            print(f"  ⚠️  Apify run falhou para: {search_term}")
            return []

        # Poll for completion
        dataset_id = self._wait_for_run(run_id)
        if not dataset_id:
            return []

        # Get results
        results_resp = self.client.get(
            f"https://api.apify.com/v2/datasets/{dataset_id}/items?token={self.apify_key}"
        )
        raw_results = results_resp.json()

        leads = []
        for item in raw_results:
            lead = self._parse_apify_result(item, icp, cidade)
            if lead and not self._is_excluded(lead["nome"]):
                leads.append(lead)

        print(f"  ✅ {len(leads)} empresas encontradas em {cidade}")
        return leads

    def _wait_for_run(self, run_id: str, timeout: int = 300) -> Optional[str]:
        """Aguarda run do Apify completar (polling com backoff)."""
        start = time.time()
        wait = 5
        while time.time() - start < timeout:
            time.sleep(wait)
            resp = self.client.get(
                f"https://api.apify.com/v2/actor-runs/{run_id}?token={self.apify_key}"
            )
            status = resp.json().get("data", {}).get("status")
            if status == "SUCCEEDED":
                return resp.json()["data"]["defaultDatasetId"]
            if status in ("FAILED", "ABORTED", "TIMED-OUT"):
                print(f"  ❌ Apify run {status}")
                return None
            wait = min(wait * 1.5, 30)  # backoff
        return None

    def _parse_apify_result(self, item: dict, icp: str, cidade: str) -> Optional[dict]:
        """Converte resultado Apify em formato de lead padronizado."""
        nome = item.get("title", "").strip()
        if not nome or len(nome) < 3:
            return None

        return {
            "nome": nome,
            "site": item.get("website", ""),
            "telefone": item.get("phone", ""),
            "endereco": item.get("address", ""),
            "cidade": cidade,
            "uf": item.get("state", ""),
            "rating_google": item.get("totalScore"),
            "reviews_count": item.get("reviewsCount"),
            "categoria_google": item.get("categoryName", ""),
            "place_id": item.get("placeId", ""),
            "icp_tipo": icp,
            "source": "apify_google_maps",
            "status": "discovered",
        }

    # ───────────────────────────────────────────
    # BRASIL API — CNPJ ENRICHMENT (gratuito)
    # ───────────────────────────────────────────

    def enrich_cnpj(self, cnpj: str) -> Optional[dict]:
        """
        Busca dados da empresa via BrasilAPI (gratuito).
        Retorna: razão social, CNAE, porte, sócios, endereço.
        """
        cnpj_clean = cnpj.replace(".", "").replace("/", "").replace("-", "")
        url = API_ENDPOINTS["brasil_api_cnpj"].format(cnpj=cnpj_clean)

        try:
            resp = self.client.get(url)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()

            return {
                "cnpj": data.get("cnpj"),
                "razao_social": data.get("razao_social"),
                "nome_fantasia": data.get("nome_fantasia"),
                "cnae_fiscal": data.get("cnae_fiscal"),
                "cnae_descricao": data.get("cnae_fiscal_descricao"),
                "cnaes_secundarios": [
                    c.get("codigo") for c in data.get("cnaes_secundarios", [])
                ],
                "porte": data.get("porte"),
                "capital_social": data.get("capital_social"),
                "situacao_cadastral": data.get("situacao_cadastral"),
                "data_inicio_atividade": data.get("data_inicio_atividade"),
                "municipio": data.get("municipio"),
                "uf": data.get("uf"),
                "socios": [
                    {
                        "nome": s.get("nome_socio"),
                        "qualificacao": s.get("qualificacao_socio"),
                    }
                    for s in data.get("qsa", [])
                ],
            }
        except Exception as e:
            print(f"  ⚠️  BrasilAPI erro para CNPJ {cnpj}: {e}")
            return None

    # ───────────────────────────────────────────
    # BATCH DISCOVERY — roda todas as queries
    # ───────────────────────────────────────────

    def run_full_discovery(
        self,
        icps: list[str] = None,
        cidades: list[str] = None,
        max_per_query: int = 30,
    ) -> list[dict]:
        """
        Executa discovery completo:
        - Para cada ICP → para cada query → para cada cidade → Apify
        - Deduplicação por nome normalizado
        """
        icps = icps or ["ICP1", "ICP2", "ICP3"]
        cidades = cidades or CIDADES_ALVO[:4]  # começar com 4 cidades

        all_leads = []
        seen_names = set()

        for icp_key in icps:
            icp_def = ICP_DEFINITIONS.get(icp_key)
            if not icp_def:
                continue

            queries = icp_def.get("apify_queries", [])
            print(f"\n{'='*60}")
            print(f"🎯 {icp_key}: {icp_def['nome']}")
            print(f"   {len(queries)} queries × {len(cidades)} cidades")
            print(f"{'='*60}")

            for query in queries:
                for cidade in cidades:
                    leads = self.search_apify_maps(query, cidade, max_per_query, icp_key)
                    for lead in leads:
                        name_key = self._normalize_name(lead["nome"])
                        if name_key not in seen_names:
                            seen_names.add(name_key)
                            all_leads.append(lead)
                    time.sleep(2)  # rate limit entre queries

        self.discovered = all_leads
        print(f"\n📊 TOTAL DESCOBERTO: {len(all_leads)} empresas únicas")
        return all_leads

    # ───────────────────────────────────────────
    # HELPERS
    # ───────────────────────────────────────────

    def _normalize_name(self, name: str) -> str:
        return name.lower().strip().replace(" ", "").replace("-", "").replace(".", "")

    def _is_excluded(self, name: str) -> bool:
        name_lower = name.lower()
        for excluded in CLIENTES_ATIVOS_88I + DESCARTADOS:
            if excluded.lower() in name_lower:
                return True
        return False

    def close(self):
        self.client.close()
