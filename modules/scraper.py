"""
Web Scraper — Extração assíncrona de conteúdo de sites para enrichment.
Alimenta o Claude com contexto real da empresa.
"""

import asyncio
import hashlib
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from modules.logger import get_logger

log = get_logger("sdr.scraper")

# Headers para evitar bloqueio
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}

# Páginas de interesse para seguro/delivery
PRIORITY_PATHS = [
    "/",
    "/sobre",
    "/about",
    "/quem-somos",
    "/para-entregadores",
    "/entregadores",
    "/motoristas",
    "/parceiros",
    "/seguranca",
    "/seguros",
    "/insurance",
    "/politica-de-seguro",
]


class WebScraper:

    def __init__(self, max_pages: int = 5, timeout: float = 15.0):
        self.max_pages = max_pages
        self.timeout = timeout
        self.cache: dict[str, str] = {}

    async def scrape_site(self, url: str) -> dict:
        """
        Scrapa um site e retorna conteúdo estruturado.
        Foca em páginas relevantes para seguro/entregadores.
        """
        if not url:
            return {"content": "", "pages_scraped": 0, "error": "URL vazia"}

        base_url = self._normalize_url(url)
        cache_key = hashlib.md5(base_url.encode()).hexdigest()

        if cache_key in self.cache:
            return {"content": self.cache[cache_key], "pages_scraped": 0, "cached": True}

        log.info(f"Scraping: {base_url}")

        all_text = []
        pages_scraped = 0
        errors = []

        async with httpx.AsyncClient(
            timeout=self.timeout,
            headers=HEADERS,
            follow_redirects=True,
            verify=False,
        ) as client:
            # Scrape priority pages
            for path in PRIORITY_PATHS[:self.max_pages]:
                page_url = urljoin(base_url, path)
                try:
                    text = await self._scrape_page(client, page_url)
                    if text:
                        all_text.append(f"--- {path} ---\n{text}")
                        pages_scraped += 1
                except Exception as e:
                    errors.append(f"{path}: {str(e)[:80]}")

                await asyncio.sleep(0.5)  # rate limit

        combined = "\n\n".join(all_text)

        # Truncar para economizar tokens do Claude
        if len(combined) > 4000:
            combined = combined[:4000] + "\n[...conteúdo truncado...]"

        self.cache[cache_key] = combined

        log.info(f"Scraped {pages_scraped} páginas de {base_url} ({len(combined)} chars)")

        return {
            "content": combined,
            "pages_scraped": pages_scraped,
            "url": base_url,
            "errors": errors if errors else None,
        }

    async def _scrape_page(self, client: httpx.AsyncClient, url: str) -> Optional[str]:
        """Scrapa uma página e extrai texto limpo."""
        resp = await client.get(url)
        if resp.status_code != 200:
            return None

        content_type = resp.headers.get("content-type", "")
        if "text/html" not in content_type:
            return None

        soup = BeautifulSoup(resp.text, "lxml")

        # Remover scripts, styles, nav, footer
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "iframe"]):
            tag.decompose()

        # Extrair texto
        text = soup.get_text(separator="\n", strip=True)

        # Limpar linhas vazias e espaços excessivos
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        # Remover linhas muito curtas (menus, botões)
        lines = [line for line in lines if len(line) > 15 or any(kw in line.lower() for kw in [
            "entregador", "motorista", "seguro", "delivery", "parceiro",
            "cobertura", "acidente", "proteção", "plataforma",
        ])]

        return "\n".join(lines[:100])  # max 100 linhas relevantes

    async def scrape_batch(self, leads: list[dict]) -> list[dict]:
        """
        Scrapa sites de um batch de leads em paralelo (com semaphore).
        Adiciona campo 'web_content' a cada lead.
        """
        semaphore = asyncio.Semaphore(5)  # max 5 concurrent requests

        async def _scrape_one(lead: dict) -> dict:
            async with semaphore:
                site = lead.get("site", "")
                if not site:
                    lead["web_content"] = ""
                    return lead

                result = await self.scrape_site(site)
                lead["web_content"] = result.get("content", "")
                lead["web_pages_scraped"] = result.get("pages_scraped", 0)
                return lead

        tasks = [_scrape_one(lead) for lead in leads]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        enriched = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                log.error(f"Scrape error for {leads[i].get('nome')}: {result}")
                leads[i]["web_content"] = ""
                enriched.append(leads[i])
            else:
                enriched.append(result)

        scraped_count = sum(1 for l in enriched if l.get("web_content"))
        log.info(f"Batch scrape completo: {scraped_count}/{len(leads)} sites com conteúdo")

        return enriched

    def _normalize_url(self, url: str) -> str:
        """Normaliza URL adicionando schema se necessário."""
        url = url.strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        return url

    def scrape_batch_sync(self, leads: list[dict]) -> list[dict]:
        """Wrapper síncrono para scrape_batch."""
        return asyncio.run(self.scrape_batch(leads))
