"""
Discovery Module — Busca de leads via LinkedIn (Apify), Google Maps (Apify) e BrasilAPI.

Ordem no pipeline:
  1. LinkedIn Company Search  — filtros de indústria/porte/localização
  2. Google Maps              — cobertura geográfica por cidade
  3. BrasilAPI CNPJ           — enriquecimento gratuito por CNPJ

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
    HUNT_DEFAULT_SOURCES, LINKEDIN_HUNT_FILTERS, HUNT_LINKEDIN_MAX_RESULTS,
)
from modules.linkedin_discovery import LinkedInDiscovery
from modules.lead_merger import LeadMerger


class LeadDiscovery:

    def __init__(self, apify_key: str = APIFY_API_KEY):
        self.apify_key = apify_key
        self.client = httpx.Client(timeout=120.0)
        self.discovered: list[dict] = []
        self._linkedin = LinkedInDiscovery(apify_key)
        self._merger = LeadMerger()

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
        sources: list[str] = None,
        linkedin_filters: list[dict] = None,
    ) -> list[dict]:
        """
        Executa discovery completo em duas fontes (ordem de execução):

        1. LinkedIn Company Search (Apify) — filtros de indústria/porte
        2. Google Maps (Apify)             — cobertura geográfica por cidade

        Args:
            icps: ICPs a processar para Google Maps (["ICP1","ICP2","ICP3"])
            cidades: lista de cidades para Google Maps
            max_per_query: max resultados por query em ambas as fontes
            sources: ["linkedin","google_maps"] — quais fontes ativar
            linkedin_filters: substitui LINKEDIN_HUNT_FILTERS do settings

        Deduplicação global por nome normalizado + linkedin_url.
        """
        icps = icps or ["ICP1", "ICP2", "ICP3"]
        cidades = cidades or CIDADES_ALVO[:4]
        sources = sources or HUNT_DEFAULT_SOURCES
        linkedin_filters = linkedin_filters if linkedin_filters is not None else LINKEDIN_HUNT_FILTERS

        all_leads: list[dict] = []
        seen: set[str] = set()

        def _add(lead: dict):
            key = self._dedup_key(lead)
            if key not in seen:
                seen.add(key)
                all_leads.append(lead)

        # ── STEP 1: LINKEDIN ─────────────────────────────────────
        if "linkedin" in sources and linkedin_filters:
            print(f"\n{'='*60}")
            print("🔗 STEP 1 — LINKEDIN COMPANY SEARCH")
            print(f"   {len(linkedin_filters)} queries configuradas")
            print(f"{'='*60}")
            li_leads = self._linkedin.run_discovery(
                linkedin_filters,
                max_per_query=max_per_query,
            )
            for lead in li_leads:
                _add(lead)
            print(f"  → {len(li_leads)} leads do LinkedIn adicionados")
        elif "linkedin" in sources and not linkedin_filters:
            print("\n⚠️  LinkedIn habilitado mas LINKEDIN_HUNT_FILTERS está vazio.")
            print("   Preencha config/settings.py → LINKEDIN_HUNT_FILTERS para ativar.")

        # ── STEP 2: GOOGLE MAPS ──────────────────────────────────
        if "google_maps" in sources:
            for icp_key in icps:
                icp_def = ICP_DEFINITIONS.get(icp_key)
                if not icp_def:
                    continue

                queries = icp_def.get("apify_queries", [])
                print(f"\n{'='*60}")
                print(f"🗺️  STEP 2 — GOOGLE MAPS | {icp_key}: {icp_def['nome']}")
                print(f"   {len(queries)} queries × {len(cidades)} cidades")
                print(f"{'='*60}")

                for query in queries:
                    for cidade in cidades:
                        leads = self.search_apify_maps(query, cidade, max_per_query, icp_key)
                        for lead in leads:
                            _add(lead)
                        time.sleep(2)

        # Funde leads da mesma empresa vindos de fontes diferentes
        all_leads = self._merger.merge_batch(all_leads)

        self.discovered = all_leads
        print(f"\n📊 TOTAL DESCOBERTO: {len(all_leads)} empresas únicas")
        return all_leads

    # ───────────────────────────────────────────
    # HELPERS
    # ───────────────────────────────────────────

    def _normalize_name(self, name: str) -> str:
        return name.lower().strip().replace(" ", "").replace("-", "").replace(".", "")

    def _dedup_key(self, lead: dict) -> str:
        """Usa linkedin_url quando disponível, senão nome normalizado."""
        if lead.get("linkedin_url"):
            return lead["linkedin_url"].rstrip("/").lower()
        return self._normalize_name(lead["nome"])

    def _is_excluded(self, name: str) -> bool:
        name_lower = name.lower()
        for excluded in CLIENTES_ATIVOS_88I + DESCARTADOS:
            if excluded.lower() in name_lower:
                return True
        return False

    def close(self):
        self.client.close()
        self._linkedin.close()
