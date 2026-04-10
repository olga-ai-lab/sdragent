"""
Lead Hunter — Caça de leads via Apify (LinkedIn + Google Maps) + Lusha.

Diferença do discovery.py (Google Maps): este módulo adiciona LinkedIn como
fonte de empresas e usa Lusha Person API para mapear o decisor antes mesmo
de entrar no pipeline de enriquecimento.

Fluxo:
  1. Apify Google Maps  → lista de empresas  (herda LeadDiscovery)
  2. Apify LinkedIn     → lista de empresas  (via company-search actor)
  3. Lusha Person API   → decisor por domain  (CEO/COO/CFO/Dir)
  4. Dedup + merge      → prontos para score + Supabase

Uso CLI:
  python main.py hunt --sources google_maps,linkedin --icps ICP1,ICP2 --limit 30
"""

from __future__ import annotations

import re
import time
from typing import Optional

import httpx

from config.settings import (
    APIFY_API_KEY,
    APIFY_ACTOR_LINKEDIN_COMPANIES,
    CIDADES_ALVO,
    ICP_DEFINITIONS,
    LUSHA_API_KEY,
)
from modules.discovery import LeadDiscovery
from modules.logger import get_logger

log = get_logger("sdr.hunter")

# ─── Apify actors ───────────────────────────────────────────────
# Actor primário lido do .env (APIFY_ACTOR_LINKEDIN_COMPANIES).
# Fallback hardcoded caso nenhum env var seja definido.
# Nota: Apify aceita "username~actorname" OU "username/actorname" na URL.
_ACTOR_LINKEDIN_FALLBACK = "vdrmota~linkedin-company-scraper"


class LeadHunter:
    """
    Motor de caça multi-fonte.

    Sources suportadas:
      - "google_maps" : Apify Google Maps (já existia em discovery.py)
      - "linkedin"    : Apify LinkedIn Company Search (novo)

    Após a caça, enriquece todos os leads com Lusha Person API para
    mapear o decisor (CEO/COO/CFO/Dir) com email + telefone.
    """

    # Títulos priorizados em ordem de relevância
    DECISOR_TITLES = [
        "ceo", "chief executive", "fundador", "founder", "co-founder",
        "coo", "chief operating", "diretor operac",
        "cfo", "chief financial", "diretor financ",
        "diretor", "director", "vp ", "vice president",
        "head of", "gerente", "manager",
    ]

    def __init__(
        self,
        apify_key: str = APIFY_API_KEY,
        lusha_key: str = LUSHA_API_KEY,
    ):
        self.apify_key = apify_key
        self.lusha_key = lusha_key
        self.http = httpx.Client(timeout=120.0)
        self._gmaps = LeadDiscovery(apify_key)  # reutiliza discovery existente

    # ────────────────────────────────────────────────────────────
    # Source 1: Google Maps (delega para LeadDiscovery)
    # ────────────────────────────────────────────────────────────

    def hunt_google_maps(
        self,
        icps: list[str] = None,
        cidades: list[str] = None,
        max_per_query: int = 30,
    ) -> list[dict]:
        """Descobre empresas via Google Maps (Apify). Herda LeadDiscovery."""
        log.info("Iniciando hunt via Google Maps", extra={"icps": icps, "cidades": cidades})
        return self._gmaps.run_full_discovery(icps, cidades, max_per_query)

    # ────────────────────────────────────────────────────────────
    # Source 2: LinkedIn Company Search (Apify)
    # ────────────────────────────────────────────────────────────

    def hunt_linkedin_companies(
        self,
        keywords: list[str],
        max_results: int = 50,
        icp: str = "ICP1",
    ) -> list[dict]:
        """
        Busca empresas no LinkedIn via Apify.

        O actor é configurável via env var APIFY_ACTOR_LINKEDIN_COMPANIES.
        Se não definido, usa o fallback hardcoded.

        Actors testados:
          - apify~linkedin-company-scraper
          - vdrmota~linkedin-company-scraper   (fallback)

        Se nenhum funcionar, configure manualmente:
          APIFY_ACTOR_LINKEDIN_COMPANIES=seu_actor no .env
        """
        primary = APIFY_ACTOR_LINKEDIN_COMPANIES or _ACTOR_LINKEDIN_FALLBACK
        fallback = _ACTOR_LINKEDIN_FALLBACK if primary != _ACTOR_LINKEDIN_FALLBACK else None

        leads = []
        for keyword in keywords:
            log.info(f"LinkedIn hunt: '{keyword}' (max {max_results})")

            run_input = {
                "keyword": keyword,
                "searchUrl": f"https://www.linkedin.com/search/results/companies/?keywords={keyword}&origin=GLOBAL_SEARCH_HEADER",
                "maxResults": max_results,
                "country": "BR",
                "countryCode": "BR",
            }

            batch = self._run_apify_actor(primary, run_input)

            if not batch and fallback:
                log.warning(f"Actor primário sem resultado para '{keyword}', tentando fallback")
                batch = self._run_apify_actor(fallback, {
                    "queries": [keyword],
                    "country": "Brazil",
                    "maxResults": max_results,
                })

            if not batch:
                log.warning(
                    f"LinkedIn: sem resultados para '{keyword}'. "
                    "Configure APIFY_ACTOR_LINKEDIN_COMPANIES com um actor válido."
                )

            for item in batch:
                lead = self._parse_linkedin_company(item, icp)
                if lead:
                    leads.append(lead)

        log.info(f"LinkedIn hunt concluído: {len(leads)} empresas")
        return leads

    def _parse_linkedin_company(self, item: dict, icp: str) -> Optional[dict]:
        """Normaliza resultado do Apify LinkedIn para formato padrão de lead."""
        name = (
            item.get("name")
            or item.get("companyName")
            or item.get("title")
            or item.get("company_name")
            or ""
        ).strip()
        if not name or len(name) < 3:
            return None

        # Extrai headquarter como cidade
        hq = item.get("headquarter") or item.get("headquarters") or {}
        if isinstance(hq, dict):
            cidade = hq.get("city") or hq.get("geographicArea") or ""
        else:
            cidade = str(hq)

        return {
            "nome": name,
            "site": (
                item.get("website")
                or item.get("companyWebsite")
                or item.get("websiteUrl")
                or ""
            ),
            "telefone": item.get("phone") or "",
            "endereco": str(hq) if hq else (item.get("address") or ""),
            "cidade": cidade or item.get("city") or "",
            "uf": item.get("state") or item.get("country") or "",
            "linkedin_empresa": (
                item.get("linkedinUrl")
                or item.get("url")
                or item.get("companyUrl")
                or ""
            ),
            "num_funcionarios": str(
                item.get("employeeCount")
                or item.get("staffCount")
                or item.get("companySize")
                or ""
            ),
            "descricao_linkedin": item.get("description") or item.get("tagline") or "",
            "icp_tipo": icp,
            "source": "apify_linkedin",
            "status": "discovered",
        }

    # ────────────────────────────────────────────────────────────
    # Lusha: Person Search por domain
    # ────────────────────────────────────────────────────────────

    def find_decision_maker(
        self, domain: str, company_name: str = ""
    ) -> Optional[dict]:
        """
        Busca o melhor decisor para um domínio via Lusha Company Enrich.

        Endpoint: GET /v2/company/enrich?domain={domain}
        Prioriza: CEO > COO > CFO > Diretor > VP > Head > Gerente

        Retorna dict com decisor_nome, decisor_cargo, decisor_email,
        decisor_telefone, decisor_linkedin + dados da empresa (employees, revenue).
        """
        if not self.lusha_key:
            log.debug("Lusha sem API key — pulando person search")
            return None
        if not domain:
            return None

        try:
            resp = self.http.get(
                "https://api.lusha.com/v2/company/enrich",
                params={"domain": domain},
                headers={"api_key": self.lusha_key},
                timeout=30,
            )

            if resp.status_code == 402:
                log.warning("Lusha: créditos esgotados")
                return None
            if resp.status_code == 404:
                log.debug(f"Lusha: domínio não encontrado — {domain}")
                return None
            if resp.status_code != 200:
                log.warning(f"Lusha HTTP {resp.status_code} para {domain}")
                return None

            data = resp.json()
            # Suporta ambos os formatos de resposta da Lusha v2
            company_data = data.get("data") or data
            contacts = (
                company_data.get("contacts")
                or data.get("contacts")
                or []
            )

            best = self._pick_best_contact(contacts)

            result = {
                # empresa
                "lusha_employees": str(
                    company_data.get("employees")
                    or company_data.get("employeesRange")
                    or ""
                ),
                "lusha_revenue": str(
                    company_data.get("revenueRange")
                    or company_data.get("revenue")
                    or ""
                ),
                "lusha_industry": company_data.get("mainIndustry") or "",
            }

            if best:
                result.update(
                    {
                        "decisor_nome": (
                            best.get("fullName")
                            or best.get("full_name")
                            or f"{best.get('firstName','')} {best.get('lastName','')}".strip()
                        ),
                        "decisor_cargo": (
                            best.get("title")
                            or best.get("jobTitle")
                            or ""
                        ),
                        "decisor_email": self._extract_email(best),
                        "decisor_telefone": self._extract_phone(best),
                        "decisor_linkedin": (
                            best.get("linkedInUrl")
                            or best.get("linkedin_url")
                            or best.get("linkedinUrl")
                            or ""
                        ),
                        "source_decisor": "lusha",
                    }
                )

            return result if result else None

        except Exception as exc:
            log.warning(f"Lusha erro para {domain}: {exc}")
            return None

    def _pick_best_contact(self, contacts: list[dict]) -> Optional[dict]:
        """Seleciona o contato com maior prioridade de cargo."""
        best, best_rank = None, len(self.DECISOR_TITLES)
        for c in contacts:
            title = (c.get("title") or c.get("jobTitle") or "").lower()
            for rank, kw in enumerate(self.DECISOR_TITLES):
                if kw in title and rank < best_rank:
                    best_rank = rank
                    best = c
                    break
        # fallback: primeiro da lista
        return best or (contacts[0] if contacts else None)

    def _extract_email(self, contact: dict) -> str:
        emails = contact.get("emailAddresses") or contact.get("emails") or []
        if isinstance(emails, list) and emails:
            first = emails[0]
            if isinstance(first, dict):
                return first.get("emailAddress") or first.get("email") or ""
            return str(first)
        if isinstance(emails, str):
            return emails
        return contact.get("email") or ""

    def _extract_phone(self, contact: dict) -> str:
        phones = contact.get("phoneNumbers") or contact.get("phones") or []
        if isinstance(phones, list) and phones:
            first = phones[0]
            if isinstance(first, dict):
                return (
                    first.get("normalizedNumber")
                    or first.get("number")
                    or first.get("phone")
                    or ""
                )
            return str(first)
        if isinstance(phones, str):
            return phones
        return contact.get("phone") or ""

    # ────────────────────────────────────────────────────────────
    # Enriquecimento em lote com Lusha
    # ────────────────────────────────────────────────────────────

    def enrich_leads_with_lusha(
        self, leads: list[dict], rate_limit_s: float = 0.5
    ) -> list[dict]:
        """
        Para cada lead, chama Lusha Company Enrich pelo domain do site.
        Atualiza decisor_nome, decisor_cargo, decisor_email, decisor_telefone.

        rate_limit_s: pausa entre chamadas (Lusha free tier: ~2 req/s)
        """
        if not self.lusha_key:
            log.warning("LUSHA_API_KEY não configurada — enriquecimento Lusha pulado")
            return leads

        total = len(leads)
        enriched = []
        for i, lead in enumerate(leads):
            site = lead.get("site") or ""
            domain = self._extract_domain(site)

            if domain:
                log.info(f"Lusha [{i+1}/{total}]: {lead.get('nome','?')} ({domain})")
                contact = self.find_decision_maker(domain, lead.get("nome", ""))
                if contact:
                    lead = {**lead, **contact}
                    log.info(
                        f"  → {lead.get('decisor_nome','?')} ({lead.get('decisor_cargo','?')})"
                    )
                time.sleep(rate_limit_s)
            else:
                log.debug(f"Lusha [{i+1}/{total}]: {lead.get('nome','?')} sem domain — pulado")

            enriched.append(lead)

        lusha_found = sum(1 for l in enriched if l.get("source_decisor") == "lusha")
        log.info(f"Lusha enriquecimento: {lusha_found}/{total} leads com decisor")
        return enriched

    # ────────────────────────────────────────────────────────────
    # Pipeline de caça completo
    # ────────────────────────────────────────────────────────────

    def run_hunt(
        self,
        sources: list[str] = None,
        icps: list[str] = None,
        cidades: list[str] = None,
        max_per_query: int = 30,
        use_lusha: bool = True,
    ) -> list[dict]:
        """
        Pipeline completo de caça de leads:

          1. Busca via Apify (Google Maps e/ou LinkedIn)
          2. Deduplicação por nome normalizado
          3. Enriquecimento Lusha (decisor + dados empresa)

        Returns: lista de leads prontos para score + persist no Supabase.
        """
        sources = sources or ["google_maps"]
        icps = icps or ["ICP1", "ICP2", "ICP3"]
        cidades = cidades or CIDADES_ALVO[:4]

        all_leads: list[dict] = []
        seen: set[str] = set()

        # ── Source 1: Google Maps ────────────────────────────────
        if "google_maps" in sources:
            log.info("=== Caçando via Google Maps (Apify) ===")
            maps_leads = self.hunt_google_maps(icps, cidades, max_per_query)
            for lead in maps_leads:
                key = self._normalize_name(lead.get("nome", ""))
                if key and key not in seen:
                    seen.add(key)
                    all_leads.append(lead)
            log.info(f"Google Maps: {len(maps_leads)} encontrados, {len(all_leads)} únicos")

        # ── Source 2: LinkedIn ───────────────────────────────────
        if "linkedin" in sources:
            log.info("=== Caçando via LinkedIn (Apify) ===")
            before = len(all_leads)
            for icp_key in icps:
                icp_def = ICP_DEFINITIONS.get(icp_key, {})
                queries = icp_def.get("apify_queries", [])
                if not queries:
                    continue
                linkedin_leads = self.hunt_linkedin_companies(queries, max_per_query, icp_key)
                for lead in linkedin_leads:
                    key = self._normalize_name(lead.get("nome", ""))
                    if key and key not in seen:
                        seen.add(key)
                        all_leads.append(lead)
            log.info(f"LinkedIn: {len(all_leads) - before} novos leads únicos adicionados")

        log.info(f"Total caçado (dedup): {len(all_leads)} empresas")

        # ── Lusha: decisor por domain ────────────────────────────
        if use_lusha and all_leads:
            log.info("=== Enriquecendo decisores via Lusha ===")
            all_leads = self.enrich_leads_with_lusha(all_leads)

        return all_leads

    # ────────────────────────────────────────────────────────────
    # Apify helpers (compartilhados entre Google Maps e LinkedIn)
    # ────────────────────────────────────────────────────────────

    def _run_apify_actor(
        self, actor_id: str, run_input: dict, timeout: int = 300
    ) -> list[dict]:
        """Dispara um actor Apify, aguarda conclusão e retorna items do dataset."""
        if not self.apify_key:
            log.warning("APIFY_API_KEY não configurada")
            return []

        try:
            resp = self.http.post(
                f"https://api.apify.com/v2/acts/{actor_id}/runs?token={self.apify_key}",
                json=run_input,
                timeout=30,
            )
            if resp.status_code == 402:
                log.warning("Apify: créditos esgotados")
                return []
            if resp.status_code == 404:
                log.warning(f"Apify: actor não encontrado — {actor_id}")
                return []
            resp.raise_for_status()

            run_id = resp.json().get("data", {}).get("id")
            if not run_id:
                log.error(f"Apify: run_id não retornado para actor {actor_id}")
                return []

            dataset_id = self._wait_apify(run_id, timeout)
            if not dataset_id:
                return []

            items_resp = self.http.get(
                f"https://api.apify.com/v2/datasets/{dataset_id}/items?token={self.apify_key}",
                timeout=60,
            )
            result = items_resp.json()
            return result if isinstance(result, list) else []

        except Exception as exc:
            log.error(f"Apify actor {actor_id} falhou: {exc}")
            return []

    def _wait_apify(self, run_id: str, timeout: int = 300) -> Optional[str]:
        """Poll até o run concluir. Retorna dataset_id ou None se falhou."""
        start = time.time()
        wait = 5
        while time.time() - start < timeout:
            time.sleep(wait)
            try:
                resp = self.http.get(
                    f"https://api.apify.com/v2/actor-runs/{run_id}?token={self.apify_key}",
                    timeout=30,
                )
                run_data = resp.json().get("data", {})
                status = run_data.get("status")
                if status == "SUCCEEDED":
                    return run_data.get("defaultDatasetId")
                if status in ("FAILED", "ABORTED", "TIMED-OUT"):
                    log.error(f"Apify run {run_id}: {status}")
                    return None
            except Exception as exc:
                log.warning(f"Apify poll erro: {exc}")
            wait = min(wait * 1.5, 30)

        log.error(f"Apify run {run_id}: timeout ({timeout}s)")
        return None

    # ────────────────────────────────────────────────────────────
    # Utilidades
    # ────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_domain(url: str) -> str:
        if not url:
            return ""
        url = re.sub(r"^https?://", "", url)
        url = re.sub(r"^www\.", "", url)
        return url.split("/")[0].strip().lower()

    @staticmethod
    def _normalize_name(name: str) -> str:
        return re.sub(r"[\s\-\.]", "", name.lower())

    def close(self):
        self.http.close()
        self._gmaps.close()
