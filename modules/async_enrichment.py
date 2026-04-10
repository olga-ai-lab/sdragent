"""
Async Enrichment — Pipeline de enriquecimento assíncrono.
Scraper + Lusha + Claude em paralelo para 5x mais velocidade.
"""

import asyncio
import time
from typing import Optional

import httpx

from config.settings import LUSHA_API_KEY
from modules.claude_client import ClaudeClient
from modules.scraper import WebScraper
from modules.supabase_client import SupabaseClient
from modules.logger import get_logger

log = get_logger("sdr.enrichment")


class AsyncEnrichment:

    def __init__(
        self,
        claude: ClaudeClient,
        supabase: Optional[SupabaseClient] = None,
        lusha_key: str = LUSHA_API_KEY,
    ):
        self.claude = claude
        self.supabase = supabase
        self.lusha_key = lusha_key
        self.scraper = WebScraper(max_pages=4, timeout=12.0)

    async def enrich_lead(self, lead: dict, use_lusha: bool = True) -> dict:
        """
        Pipeline completo assíncrono para um lead:
        1. Web scrape (async)
        2. Lusha (async)
        3. Cache check (Supabase)
        4. Claude AI enrichment (sync — API call)
        5. Merge
        """
        empresa = lead.get("nome", "?")
        log.info(f"Enriquecendo: {empresa}")

        # Check cache primeiro (Regra de Ouro #7)
        if self.supabase:
            cache_key = f"enrich:{self._normalize(empresa)}"
            cached = self.supabase.get_cached_enrichment(cache_key)
            if cached:
                log.info(f"Cache hit: {empresa}")
                lead.update(cached)
                lead["enrichment_complete"] = True
                lead["enrichment_source"] = "cache"
                return lead

        # Parallel: scrape + lusha
        scrape_task = self.scraper.scrape_site(lead.get("site", ""))
        lusha_task = self._lusha_async(lead.get("site", "")) if use_lusha else asyncio.coroutine(lambda: None)()

        scrape_result, lusha_result = await asyncio.gather(
            scrape_task,
            self._lusha_async(lead.get("site", "")) if use_lusha else self._empty_result(),
            return_exceptions=True,
        )

        # Process scrape result
        web_content = ""
        if isinstance(scrape_result, dict):
            web_content = scrape_result.get("content", "")
            lead["web_pages_scraped"] = scrape_result.get("pages_scraped", 0)

        # Process Lusha result
        if isinstance(lusha_result, dict) and lusha_result:
            lead.update(lusha_result)
            log.info(f"Lusha: {lusha_result.get('decisor_nome', '?')} — {lusha_result.get('decisor_cargo', '?')}")

        # Claude AI enrichment (sync — precisa do web_content)
        ai_data = self._claude_enrich(lead, web_content)
        if ai_data and isinstance(ai_data, dict):
            lead["ai_segmento"] = ai_data.get("segmento")
            lead["ai_tem_entregadores"] = ai_data.get("tem_entregadores")
            lead["ai_entregadores_est"] = ai_data.get("entregadores_estimado")
            lead["ai_porte"] = ai_data.get("porte")
            lead["ai_plataforma_digital"] = ai_data.get("plataforma_digital")
            lead["ai_seguro_detectado"] = ai_data.get("seguro_delivery_detectado")
            lead["ai_formato_email"] = ai_data.get("formato_email_provavel")
            lead["ai_risco_exclusao"] = ai_data.get("risco_exclusao")
            lead["ai_confianca"] = ai_data.get("confianca", 0.0)

            if ai_data.get("risco_exclusao"):
                lead["status"] = "excluded"
                lead["exclusion_reason"] = ai_data["risco_exclusao"]

        lead["enrichment_complete"] = True
        lead["enrichment_source"] = "live"

        # Save to cache
        if self.supabase:
            try:
                cache_data = {k: v for k, v in lead.items() if k.startswith("ai_") or k.startswith("decisor_")}
                self.supabase.set_cached_enrichment(cache_key, cache_data, "async_pipeline")
            except Exception as e:
                log.warning(f"Cache write error: {e}")

        return lead

    async def enrich_batch(self, leads: list[dict], use_lusha: bool = True, concurrency: int = 5) -> list[dict]:
        """
        Enriquece batch de leads com controle de concorrência.
        Default: 5 leads em paralelo.
        """
        start = time.time()
        total = len(leads)
        log.info(f"Batch enrichment: {total} leads, concurrency={concurrency}")

        semaphore = asyncio.Semaphore(concurrency)
        results = []
        completed = 0

        async def _enrich_one(lead: dict) -> dict:
            nonlocal completed
            async with semaphore:
                try:
                    result = await self.enrich_lead(lead, use_lusha)
                    completed += 1
                    if completed % 10 == 0 or completed == total:
                        log.info(f"Progresso: {completed}/{total} ({completed/total*100:.0f}%)")
                    return result
                except Exception as e:
                    log.error(f"Erro enriquecendo {lead.get('nome', '?')}: {e}")
                    lead["enrichment_complete"] = False
                    lead["enrichment_error"] = str(e)
                    completed += 1
                    return lead

        tasks = [_enrich_one(lead) for lead in leads]
        results = await asyncio.gather(*tasks)

        elapsed = time.time() - start
        success = sum(1 for r in results if r.get("enrichment_complete"))
        log.info(
            f"Batch completo: {success}/{total} em {elapsed:.1f}s "
            f"({elapsed/max(total,1):.1f}s/lead)"
        )

        return list(results)

    def enrich_batch_sync(self, leads: list[dict], use_lusha: bool = True) -> list[dict]:
        """Wrapper síncrono."""
        return asyncio.run(self.enrich_batch(leads, use_lusha))

    # ───────────────────────────────────────────
    # INTERNAL
    # ───────────────────────────────────────────

    async def _lusha_async(self, site: str) -> Optional[dict]:
        """Busca Lusha via httpx async."""
        if not self.lusha_key or not site:
            return None

        domain = self._extract_domain(site)
        if not domain:
            return None

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    "https://api.lusha.com/v2/company/enrich",
                    params={"domain": domain},
                    headers={"api_key": self.lusha_key},
                )
                if resp.status_code != 200:
                    return None

                data = resp.json()
                contacts = data.get("contacts", [])

                priority_titles = [
                    "ceo", "chief executive", "fundador", "founder",
                    "coo", "chief operating", "cfo", "chief financial",
                    "diretor", "director", "vp", "vice president",
                    "head", "gerente", "manager",
                ]

                best = None
                best_prio = len(priority_titles)

                for c in contacts:
                    title = (c.get("title") or "").lower()
                    for i, kw in enumerate(priority_titles):
                        if kw in title and i < best_prio:
                            best_prio = i
                            best = c
                            break

                if not best and contacts:
                    best = contacts[0]

                if best:
                    return {
                        "decisor_nome": best.get("full_name", ""),
                        "decisor_cargo": best.get("title", ""),
                        "decisor_email": best.get("email", ""),
                        "decisor_telefone": best.get("phone", ""),
                        "decisor_linkedin": best.get("linkedin_url", ""),
                        "source_decisor": "lusha",
                    }
        except Exception as e:
            log.warning(f"Lusha async error for {site}: {e}")

        return None

    def _claude_enrich(self, lead: dict, web_content: str = "") -> Optional[dict]:
        """Claude AI enrichment (síncrono — API call)."""
        empresa = lead.get("nome", "")
        prompt = f"""Analise esta empresa para qualificação como prospect de seguro para entregadores:

EMPRESA: {empresa}
SITE: {lead.get('site', '')}
CATEGORIA: {lead.get('categoria_google', '')}
CIDADE: {lead.get('cidade', '')}
{f'CONTEÚDO DO SITE:{chr(10)}{web_content[:3000]}' if web_content else ''}

Extraia em JSON:
{{
  "segmento": "food delivery | moto delivery | quick commerce | logistica | courier urbano | ecommerce | tms | outro",
  "tem_entregadores": true/false,
  "entregadores_estimado": "número ou faixa ou null",
  "porte": "grande | medio | pequeno | micro",
  "plataforma_digital": true/false,
  "seguro_delivery_detectado": "sim | nao | desconhecido",
  "decisor_sugerido_cargo": "cargo sugerido ou null",
  "formato_email_provavel": "formato ou null",
  "risco_exclusao": "motivo se não for ICP válido, ou null",
  "confianca": 0.0-1.0
}}"""

        try:
            return self.claude.call("enrichment", prompt, max_tokens=300, json_output=True)
        except Exception as e:
            log.error(f"Claude enrich error for {empresa}: {e}")
            return None

    async def _empty_result(self):
        return None

    def _extract_domain(self, url: str) -> str:
        if not url:
            return ""
        url = url.replace("https://", "").replace("http://", "").replace("www.", "")
        return url.split("/")[0].strip()

    def _normalize(self, name: str) -> str:
        return name.lower().strip().replace(" ", "_").replace("-", "_")
