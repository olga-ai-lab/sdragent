"""
Intelligence Engine — camada incremental de inteligência SDR (L1-L7).

Implementação em produção:
- L1/L1b: Claude web research
- L2: consulta CNPJ
- L2b/L3: Lusha company/person
- L4/L5: Apify LinkedIn profile/posts
- L6 + deal value + build_report + L7
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from config.settings import (
    APIFY_API_KEY,
    ENABLE_LINKEDIN_POSTS,
    ENABLE_LINKEDIN_PROFILE,
    ENABLE_LUSHA_PERSON,
    ENABLE_WEB_RESEARCH,
    LUSHA_API_KEY,
    SUPABASE_KEY,
    SUPABASE_URL,
)
from modules.claude_client import ClaudeClient
from modules.logger import get_logger

log = get_logger("sdr.intelligence")

REUNIAO_CITADOS = ["pick and go", "gaudium", "machine", "intelipost"]


@dataclass
class ScoreResult:
    score: int
    breakdown: dict[str, int]
    porte: str


class IntelligenceEngine:
    """Engine incremental com compatibilidade para o projeto atual."""

    def __init__(self, claude: Optional[ClaudeClient] = None):
        self.claude = claude
        self.http = httpx.Client(timeout=45.0)
        self.enable_web_research = ENABLE_WEB_RESEARCH
        self.enable_lusha_person = ENABLE_LUSHA_PERSON
        self.enable_linkedin_profile = ENABLE_LINKEDIN_PROFILE
        self.enable_linkedin_posts = ENABLE_LINKEDIN_POSTS

    # ───────────────────────────────────────────
    # Utilitários portados da edge function
    # ───────────────────────────────────────────

    @staticmethod
    def clean_name(raw: str) -> str:
        if not raw:
            return ""
        name = raw.split(" - ")[0].split(" (")[0].split(", ")[0].strip()
        name = re.sub(
            r"^(Cofundador|Co-?fundador|Fundador|CEO|COO|CFO|CTO|Diretor|Gerente|Presidente)\s*(e\s*)?(CEO|COO|CFO|CTO|Fundador)?\s*",
            "",
            name,
            flags=re.IGNORECASE,
        ).strip()
        name = re.sub(r"\s+(CEO|COO|CFO|CTO|Cofundador|Fundador|Diretor|Gerente|Presidente|VP|Head)\b.*$", "", name, flags=re.IGNORECASE).strip()
        return name

    @staticmethod
    def strip_cite(text: str) -> str:
        if not text:
            return ""
        cleaned = re.sub(r"\[\d+\]", "", text)
        cleaned = re.sub(r"\(source:.*?\)", "", cleaned, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", cleaned).strip()

    @staticmethod
    def extract_json(text: str) -> Optional[dict]:
        if not text:
            return None
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            pass
        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if fenced:
            try:
                return json.loads(fenced.group(1).strip())
            except json.JSONDecodeError:
                pass
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            raw = text[start:end + 1]
            for candidate in (
                raw,
                re.sub(r"[\r\n\t]+", " ", raw),
                re.sub(r",\s*([}\]])", r"\1", re.sub(r"[\r\n\t]+", " ", raw)),
            ):
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    continue
        return None

    @staticmethod
    def normalize_work(raw: Any) -> list[dict]:
        items = IntelligenceEngine._safe_array(raw)
        return [
            {
                "title": e.get("title") or e.get("role") or e.get("position") or "",
                "company": e.get("company") or e.get("companyName") or e.get("company_name") or "",
                "duration": e.get("duration") or e.get("dateRange") or e.get("started_on") or "",
            }
            for e in items
            if (e.get("title") or e.get("role") or e.get("company"))
        ]

    @staticmethod
    def normalize_edu(raw: Any) -> list[dict]:
        items = IntelligenceEngine._safe_array(raw)
        return [
            {
                "school": e.get("school") or e.get("schoolName") or e.get("school_name") or "",
                "degree": e.get("degree") or e.get("degreeName") or "",
                "field": e.get("field") or e.get("fieldOfStudy") or "",
            }
            for e in items
            if (e.get("school") or e.get("schoolName"))
        ]

    @staticmethod
    def normalize_posts(raw: Any, limit: int = 10) -> list[dict]:
        items = IntelligenceEngine._safe_array(raw)[:limit]
        posts = []
        for p in items:
            text = p.get("text") or p.get("content") or ""
            if not text:
                continue
            posts.append(
                {
                    "text": text,
                    "likes": p.get("likes")
                    or p.get("numLikes")
                    or (p.get("stats") or {}).get("reactions")
                    or (p.get("socialCount") or {}).get("reactions")
                    or 0,
                    "date": ((p.get("postedAt") or {}).get("date") if isinstance(p.get("postedAt"), dict) else p.get("postedAt"))
                    or ((p.get("posted_at") or {}).get("date") if isinstance(p.get("posted_at"), dict) else p.get("posted_at"))
                    or "",
                    "url": p.get("url") or p.get("linkedinUrl") or "",
                    "author": (p.get("author") or {}).get("name") or "",
                }
            )
        return posts

    @staticmethod
    def _safe_array(raw: Any) -> list[dict]:
        if not raw:
            return []
        if isinstance(raw, list):
            return raw
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, list) else []
            except json.JSONDecodeError:
                return []
        return []

    @staticmethod
    def _parse_first_num(raw: Any) -> int:
        m = re.search(r"(\d[\d.,]*)", str(raw or ""))
        return int(m.group(1).replace(".", "").replace(",", "")) if m else 0

    @staticmethod
    def _domain_from(url: str) -> str:
        if not url:
            return ""
        cleaned = re.sub(r"^https?://", "", url, flags=re.IGNORECASE)
        cleaned = re.sub(r"^www\.", "", cleaned, flags=re.IGNORECASE)
        return cleaned.split("/")[0].lower()

    @staticmethod
    def _is_placeholder(name: str) -> bool:
        if not name:
            return True
        return bool(re.search(r"^(a identificar|a classif|nome nao|não encontrad|não dispon|não identif|informação)", name, re.IGNORECASE)) or len(name.strip()) < 3

    @staticmethod
    def _extract_linkedin_person_url(text: str) -> Optional[str]:
        if not text:
            return None
        m = re.search(r"https?://(?:[\w]+\.)?linkedin\.com/in/[a-zA-Z0-9%_.\-]+/?", text)
        return m.group(0).rstrip("/") if m else None

    # ───────────────────────────────────────────
    # L1-L5 reais (produção)
    # ───────────────────────────────────────────

    async def l1_web_research(self, lead: dict) -> Optional[dict]:
        if not self.enable_web_research:
            log.info("L1 web research desabilitado por feature flag")
            return None
        company = lead.get("nome") or lead.get("company_name") or ""
        domain = self._domain_from(lead.get("site") or lead.get("website") or "")
        city = lead.get("cidade") or lead.get("city") or ""
        icp = lead.get("icp_tipo") or ""
        if not company:
            return None

        prompt = (
            f'Pesquise "{company}" ({domain}) {city}. '\
            f'Considere ICP {icp}. Retorne APENAS JSON: '\
            '{"nome_oficial":"","descricao":"","site":"","cnpj":"","ceo_fundador":"","cargo_decisor":"",'\
            '"linkedin_decisor":"","linkedin_empresa":"","num_entregadores":"","num_funcionarios":"",'\
            '"receita_estimada":"","num_entregas_mes":"","num_clientes_plataforma":"",'\
            '"segmento":"","usa_seguro":"","tem_api":"","noticias_recentes":"","contexto_estrategico":""}'
        )
        raw = await self._claude_web_json(prompt, max_tokens=1400)
        if not isinstance(raw, dict):
            return None
        normalized = {
            "nome_oficial": self.strip_cite(str(raw.get("nome_oficial") or company)),
            "descricao": self.strip_cite(str(raw.get("descricao") or "")),
            "site": str(raw.get("site") or lead.get("site") or "").strip(),
            "cnpj": str(raw.get("cnpj") or "").strip(),
            "ceo_fundador": self.clean_name(str(raw.get("ceo_fundador") or lead.get("decisor_nome") or "")),
            "cargo_decisor": self.strip_cite(str(raw.get("cargo_decisor") or "")),
            "linkedin_decisor": self._extract_linkedin_person_url(str(raw.get("linkedin_decisor") or "")) or "",
            "linkedin_empresa": str(raw.get("linkedin_empresa") or "").strip(),
            "num_entregadores": str(raw.get("num_entregadores") or ""),
            "num_funcionarios": str(raw.get("num_funcionarios") or ""),
            "receita_estimada": str(raw.get("receita_estimada") or ""),
            "num_entregas_mes": str(raw.get("num_entregas_mes") or ""),
            "num_clientes_plataforma": str(raw.get("num_clientes_plataforma") or ""),
            "segmento": self.strip_cite(str(raw.get("segmento") or "")),
            "usa_seguro": self.strip_cite(str(raw.get("usa_seguro") or "")),
            "tem_api": self.strip_cite(str(raw.get("tem_api") or "")),
            "noticias_recentes": self.strip_cite(str(raw.get("noticias_recentes") or "")),
            "contexto_estrategico": self.strip_cite(str(raw.get("contexto_estrategico") or "")),
        }
        return normalized

    async def l1b_linkedin_search(self, lead: dict) -> Optional[str]:
        if not self.enable_web_research:
            return None
        name = self.clean_name(lead.get("decisor_nome") or lead.get("stakeholder_name") or "")
        company = lead.get("nome") or ""
        if not name or self._is_placeholder(name):
            return None
        prompt = f'LinkedIn pessoal de "{name}" empresa "{company}" Brasil. Responda apenas URL linkedin.com/in/...'
        payload = await self._claude_web_text(prompt, max_tokens=300)
        if not payload:
            return None
        return self._extract_linkedin_person_url(payload)

    async def l2_cnpj_lookup(self, cnpj: str) -> Optional[dict]:
        cnpj_digits = re.sub(r"\D", "", cnpj or "")
        if len(cnpj_digits) != 14:
            return None

        # Prioriza function existente no Supabase (compatível com referência)
        if SUPABASE_URL and SUPABASE_KEY:
            try:
                resp = await self._async_post(
                    f"{SUPABASE_URL.rstrip('/')}/functions/v1/consulta-cnpj",
                    headers={"Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json"},
                    json_data={"cnpj": cnpj_digits},
                    timeout=30,
                )
                if resp and resp.get("success"):
                    return resp
            except Exception:
                pass

        # fallback: BrasilAPI
        return await self._async_get_json(f"https://brasilapi.com.br/api/cnpj/v1/{cnpj_digits}")

    async def l2b_company_lookup(self, domain: str) -> Optional[dict]:
        if not LUSHA_API_KEY or not domain:
            return None
        url = f"https://api.lusha.com/v2/company?domain={domain}"
        data = await self._async_get_json(url, headers={"api_key": LUSHA_API_KEY})
        company = (data or {}).get("data")
        if not company:
            return None
        employees_range = company.get("employees") or ""
        return {
            "employees": self._parse_employee_range(employees_range) or int(company.get("employeesInLinkedin") or 0),
            "employees_range": employees_range,
            "revenue": self._lusha_revenue(company.get("revenueRange")),
            "industry": company.get("mainIndustry") or "",
        }

    async def l3_person_lookup(self, lead: dict) -> Optional[dict]:
        if not self.enable_lusha_person:
            log.info("L3 Lusha person desabilitado por feature flag")
            return None
        if not LUSHA_API_KEY:
            log.info("L3 Lusha person sem LUSHA_API_KEY")
            return None
        linkedin = lead.get("decisor_linkedin") or lead.get("linkedin_url") or ""
        domain = self._domain_from(lead.get("site") or lead.get("website") or "")
        name = self.clean_name(lead.get("decisor_nome") or lead.get("stakeholder_name") or "")
        first, last = "", ""
        parts = [p for p in name.split() if p]
        if len(parts) >= 2:
            first, last = parts[0], parts[-1]

        if linkedin and "linkedin.com/in/" in linkedin:
            url = f"https://api.lusha.com/v2/person?linkedinUrl={httpx.QueryParams({'u': self._ensure_http(linkedin)})['u']}"
        elif first and last and domain:
            url = (
                "https://api.lusha.com/v2/person?"
                f"firstName={httpx.QueryParams({'f': first})['f']}&"
                f"lastName={httpx.QueryParams({'l': last})['l']}&"
                f"companyDomain={httpx.QueryParams({'d': domain})['d']}"
            )
        else:
            return None

        data = await self._async_get_json(url, headers={"api_key": LUSHA_API_KEY}, timeout=35)
        contact = ((data or {}).get("contact") or {}).get("data")
        if not contact:
            return None
        return {
            "nome": contact.get("fullName") or f"{contact.get('firstName', '')} {contact.get('lastName', '')}".strip(),
            "cargo": ((contact.get("jobTitle") or {}).get("title")) or "",
            "seniority": ((contact.get("jobTitle") or {}).get("seniority")) or "",
            "email": (((contact.get("emailAddresses") or [{}])[0]).get("email")) or "",
            "email_confidence": (((contact.get("emailAddresses") or [{}])[0]).get("emailConfidence")) or "",
            "telefone": (((contact.get("phoneNumbers") or [{}])[0]).get("number")) or "",
            "linkedin": ((contact.get("socialLinks") or {}).get("linkedin")) or "",
            "followers": contact.get("linkedinFollowersCount") or 0,
            "connections": contact.get("linkedinConnectionsCount") or 0,
        }

    async def l4_linkedin_profile(self, linkedin_url: str) -> Optional[dict]:
        if not self.enable_linkedin_profile:
            log.info("L4 LinkedIn profile desabilitado por feature flag")
            return None
        if "linkedin.com/in/" not in (linkedin_url or ""):
            return None
        if not APIFY_API_KEY:
            log.info("L4 LinkedIn profile sem APIFY_API_KEY")
            return None
        actor = os.getenv("APIFY_ACTOR_LINKEDIN_PROFILE", "dev_fusion~linkedin-profile-scraper")
        data = await self._run_apify(actor, {"profileUrls": [self._ensure_http(linkedin_url)]}, timeout=60)
        if not data:
            return None
        p = data[0]
        return {
            "full_name": p.get("full_name") or p.get("fullName") or p.get("name") or "",
            "headline": p.get("headline") or p.get("job_title") or "",
            "summary": p.get("description") or p.get("about") or p.get("summary") or "",
            "experience": self.normalize_work(p.get("experience") or p.get("experiences") or []),
            "education": self.normalize_edu(p.get("education") or p.get("educations") or []),
            "skills": self._safe_array(p.get("skills")) if isinstance(p.get("skills"), list) else p.get("skills") or [],
            "followers": p.get("followers") or 0,
            "connections": p.get("connections") or 0,
            "email": p.get("email") or "",
        }

    async def l5_linkedin_posts(self, linkedin_url: str) -> Optional[list[dict]]:
        if not self.enable_linkedin_posts:
            log.info("L5 LinkedIn posts desabilitado por feature flag")
            return []
        if "linkedin.com/in/" not in (linkedin_url or ""):
            return []
        if not APIFY_API_KEY:
            log.info("L5 LinkedIn posts sem APIFY_API_KEY")
            return []
        actor = os.getenv("APIFY_ACTOR_LINKEDIN_POSTS", "harvestapi~linkedin-profile-posts")
        max_posts = int(os.getenv("LINKEDIN_POSTS_MAX", "10"))
        data = await self._run_apify(actor, {"targetUrls": [self._ensure_http(linkedin_url)], "maxPosts": max_posts}, timeout=45)
        return self.normalize_posts(data or [], limit=max_posts)

    # ───────────────────────────────────────────
    # L6 / deal value / report
    # ───────────────────────────────────────────

    def l6_score(self, data: dict) -> ScoreResult:
        bk: dict[str, int] = {}
        score = 0
        porte = data.get("porte_str") or "Pequeno"
        n_func = int(data.get("n_func") or 0)
        receita = str(data.get("receita") or "")
        ent = int(data.get("entregadores") or 0)

        if n_func >= 1000 or re.search(r"100M|bilh", receita, re.IGNORECASE):
            porte = "Grande"
        elif n_func >= 100 or re.search(r"10M|50M", receita, re.IGNORECASE):
            porte = "Medio"
        elif ent >= 50000:
            porte = "Grande"
        elif ent >= 5000:
            porte = "Medio"

        icp = data.get("icp", "")
        if icp == "ICP1":
            bk["vol"] = 30 if ent >= 100000 else 20 if ent >= 10000 else 12 if ent >= 1000 else 5 if ent > 0 else 0
            bk["plat"] = 15 if data.get("tech") else 10
            bk["port"] = 10 if porte == "Grande" else 7 if porte == "Medio" else 4
            cargo = str(data.get("cargo") or "")
            if data.get("decisor"):
                if re.search(r"CEO|COO|CFO|Fundador|Founder|Country", cargo, re.IGNORECASE):
                    bk["dec"] = 15
                elif re.search(r"Dir|VP|Head|Gerente", cargo, re.IGNORECASE):
                    bk["dec"] = 12
                else:
                    bk["dec"] = 8
            else:
                bk["dec"] = 3
            if data.get("has_linkedin") and bk["dec"] < 15:
                bk["dec"] = min(15, bk["dec"] + 3)
            bk["jorn"] = 10 if data.get("tech") else 5
            score = sum(bk.values())
        elif icp == "ICP2":
            bk["vol"] = 30 if ent >= 1_000_000 else 20 if ent >= 100_000 else 12 if ent >= 10_000 else 5
            bk["api"] = 25 if data.get("has_api") else 5
            despacho = data.get("despacho")
            bk["pos"] = 20 if despacho == "etiqueta" else 10 if despacho == "cotacao" else 0
            bk["mkt"] = 15
            score = sum(bk.values())
        elif icp == "ICP3":
            tms = int(data.get("tms") or 0)
            bk["cli"] = 30 if tms >= 500 else 20 if tms >= 100 else 10
            bk["int"] = 25 if data.get("has_api") else 10
            bk["prt"] = 20 if porte == "Pequeno" else 15 if porte == "Medio" else 10
            bk["cit"] = 15 if data.get("reuniao_citada") else 0
            bk["par"] = 10 if data.get("has_api") else 5
            score = sum(bk.values())
        else:
            score = min(50, 30 + (10 if data.get("decisor") else 0) + (10 if ent > 0 else 0))

        if not data.get("seguro") and score > 0:
            score = min(100, score + 5)
        return ScoreResult(score=min(100, score), breakdown=bk, porte=porte)

    def calc_deal_value(self, icp: str, entregadores: int, entregas_mes: int, num_clientes: int) -> dict[str, Any]:
        ap, bag, viagens_por_ent = 0.14, 0.07, 200
        if icp == "ICP1":
            if entregadores > 0:
                viagens = entregas_mes or (entregadores * viagens_por_ent)
                return {
                    "deal_value_est": round(viagens * ap + entregadores * 0.05 * 15),
                    "deal_value_premissas": f"{entregadores}ent x{viagens}viag xR${ap}",
                }
            return {"deal_value_est": 0, "deal_value_premissas": "Sem dados"}
        if icp == "ICP2":
            volume = entregas_mes or num_clientes * 1000
            return {
                "deal_value_est": round(volume * bag) if volume > 0 else 0,
                "deal_value_premissas": f"{volume}desp xR${bag}" if volume > 0 else "Sem dados",
            }
        if icp == "ICP3":
            base = num_clientes or entregadores
            return {
                "deal_value_est": round(base * 500 * bag) if base > 0 else 0,
                "deal_value_premissas": f"{base}transp x500 xR${bag}" if base > 0 else "Sem dados",
            }
        return {"deal_value_est": 0, "deal_value_premissas": "ICP?"}

    def build_report(
        self,
        lead: dict,
        score: ScoreResult,
        deal_value: dict[str, Any],
        closing_intelligence: Optional[dict] = None,
        source: str = "python_os",
    ) -> dict[str, Any]:
        return {
            "closing_intelligence": closing_intelligence or None,
            "score": score.score,
            "score_breakdown": score.breakdown,
            "deal_value_est": deal_value["deal_value_est"],
            "deal_value_premissas": deal_value["deal_value_premissas"],
            "canal_recomendado": (closing_intelligence or {}).get("canal", "whatsapp"),
            "recent_posts": self.normalize_posts(lead.get("last_post") or lead.get("recent_posts")),
            "work_experience": self.normalize_work(lead.get("works") or lead.get("work_experience")),
            "linkedin_personal": {
                "name": lead.get("decisor_nome") or lead.get("stakeholder_name") or "",
                "title": lead.get("decisor_cargo") or lead.get("stakeholder_role") or "",
                "url": lead.get("decisor_linkedin") or lead.get("linkedin_url") or "",
            },
            "linkedin_company": {
                "name": lead.get("nome") or lead.get("company_name") or "",
                "url": lead.get("linkedin_empresa") or "",
            },
            "source": source,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    # ───────────────────────────────────────────
    # L7
    # ───────────────────────────────────────────

    def l7_generate(self, lead: dict, report_context: dict[str, Any]) -> Optional[dict]:
        if not self.claude:
            return None
        prompt = (
            "Você é SDR sênior da 88i. Gere SOMENTE JSON válido com campos:\n"
            "perfil_decisor,momento_atual,gap_oportunidade,canal,abertura,pitch_1_frase,pontos_conexao,timing,"
            "o_que_evitar,objecao_1,resposta_1,objecao_2,resposta_2,roteiro,proximo_passo.\n"
            f"Lead:\n{json.dumps(lead, ensure_ascii=False)}\n"
            f"Contexto:\n{json.dumps(report_context, ensure_ascii=False)}"
        )
        try:
            response = self.claude.call("strategy", prompt, max_tokens=1400)
            parsed = self.extract_json(response if isinstance(response, str) else json.dumps(response))
            if isinstance(parsed, dict):
                return parsed
        except Exception as exc:
            log.warning(f"L7 falhou para {lead.get('nome', '?')}: {exc}")
        return None

    # ───────────────────────────────────────────
    # Pipeline completo de intelligence (produção)
    # ───────────────────────────────────────────

    def generate_intelligence_sync(self, lead: dict) -> Optional[dict]:
        try:
            return asyncio.run(self.generate_intelligence(lead))
        except RuntimeError:
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(self.generate_intelligence(lead))

    def build_full_intelligence_report(self, lead: dict) -> Optional[dict]:
        """API pública explícita para gerar o report completo (L1-L7)."""
        return self.generate_intelligence_sync(lead)

    def build_intelligence(self, lead: dict) -> Optional[dict]:
        """Alias de compatibilidade para chamadas legadas."""
        return self.build_full_intelligence_report(lead)

    async def generate_intelligence(self, lead: dict) -> Optional[dict]:
        source_breakdown: dict[str, str] = {}

        web = await self._safe_step("l1", self.l1_web_research(lead), source_breakdown)
        if web and not lead.get("decisor_linkedin"):
            lead["decisor_linkedin"] = web.get("linkedin_decisor") or lead.get("decisor_linkedin")

        if not lead.get("decisor_linkedin"):
            li = await self._safe_step("l1b", self.l1b_linkedin_search(lead), source_breakdown)
            if li:
                lead["decisor_linkedin"] = li

        cnpj_data = await self._safe_step("l2", self.l2_cnpj_lookup((web or {}).get("cnpj") or lead.get("cnpj") or ""), source_breakdown)
        company_domain = self._domain_from((web or {}).get("site") or lead.get("site") or "")
        lusha_company = await self._safe_step("l2b", self.l2b_company_lookup(company_domain), source_breakdown)
        lusha_person = await self._safe_step("l3", self.l3_person_lookup({**lead, "site": (web or {}).get("site") or lead.get("site")}), source_breakdown)

        linkedin_url = lead.get("decisor_linkedin") or (lusha_person or {}).get("linkedin") or ""
        profile_task = self._safe_step("l4", self.l4_linkedin_profile(linkedin_url), source_breakdown)
        posts_task = self._safe_step("l5", self.l5_linkedin_posts(linkedin_url), source_breakdown)
        profile, posts_raw = await asyncio.gather(profile_task, posts_task)

        work = self.normalize_work((profile or {}).get("experience") or lead.get("works"))
        edu = self.normalize_edu((profile or {}).get("education") or lead.get("education"))
        posts = self.normalize_posts(posts_raw or lead.get("last_post"))

        entregadores = self._parse_first_num((web or {}).get("num_entregadores") or lead.get("ai_entregadores_est") or lead.get("entregadores_est"))
        entregas_mes = self._parse_first_num((web or {}).get("num_entregas_mes"))
        num_clientes = self._parse_first_num((web or {}).get("num_clientes_plataforma"))
        icp = lead.get("icp_tipo", "")

        score = self.l6_score(
            {
                "icp": icp,
                "entregadores": entregadores,
                "tech": bool(re.search(r"delivery|logtech|plataforma|marketplace", str((web or {}).get("segmento") or ""), re.IGNORECASE)),
                "porte_str": lead.get("porte") or "Pequeno",
                "receita": (lusha_company or {}).get("revenue") or (web or {}).get("receita_estimada") or "",
                "n_func": (lusha_company or {}).get("employees") or self._parse_first_num((web or {}).get("num_funcionarios")),
                "decisor": bool((lusha_person or {}).get("nome") or lead.get("decisor_nome")),
                "cargo": (lusha_person or {}).get("cargo") or lead.get("decisor_cargo") or "",
                "has_linkedin": bool(linkedin_url),
                "has_api": bool(re.search(r"api|integra", str((web or {}).get("descricao") or ""), re.IGNORECASE)),
                "despacho": "etiqueta" if (web or {}).get("segmento") == "geradora_etiqueta" else "",
                "tms": num_clientes if icp == "ICP3" else 0,
                "reuniao_citada": any(c in (lead.get("nome", "").lower()) for c in REUNIAO_CITADOS),
                "seguro": not bool(re.search(r"não|nao|sem|nenhum", str((web or {}).get("usa_seguro") or ""), re.IGNORECASE)),
            }
        )
        deal = self.calc_deal_value(icp, entregadores, entregas_mes, num_clientes)

        context = {
            "web": web,
            "cnpj": cnpj_data,
            "lusha_company": lusha_company,
            "lusha_person": lusha_person,
            "linkedin_profile": profile,
            "recent_posts": posts[:3],
            "work_experience": work[:4],
            "education": edu[:3],
            "score": score.score,
            "score_breakdown": score.breakdown,
            "deal": deal,
        }
        closing = self.l7_generate(lead, context)
        report = self.build_report(lead, score, deal, closing_intelligence=closing, source="full_l1_l7")
        report["raw_enrichment"] = {
            "l1": web,
            "l2": cnpj_data,
            "l2b": lusha_company,
            "l3": lusha_person,
            "l4": profile,
            "l5": posts_raw,
        }
        report["web_enrichment"] = web or {}
        report["linkedin_profile"] = profile or {}
        report["linkedin_posts"] = posts
        report["source_breakdown"] = source_breakdown
        report["work_experience"] = work
        report["education"] = edu
        report["recent_posts"] = posts
        return report

    # ───────────────────────────────────────────
    # Integrações externas
    # ───────────────────────────────────────────

    async def _safe_step(self, step: str, coro, source_breakdown: dict[str, str]):
        try:
            result = await coro
            source_breakdown[step] = "ok" if result else "empty"
            return result
        except Exception as exc:
            source_breakdown[step] = f"error:{type(exc).__name__}"
            log.warning(f"Falha isolada na etapa {step}: {exc}")
            return None

    async def _claude_web_json(self, prompt: str, max_tokens: int = 1000) -> Optional[dict]:
        text = await self._claude_web_text(prompt, max_tokens=max_tokens)
        return self.extract_json(text or "") if text else None

    async def _claude_web_text(self, prompt: str, max_tokens: int = 1000) -> Optional[str]:
        api_key = os.getenv("ANTHROPIC_API_KEY_2") or os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            if self.claude:
                try:
                    fallback = self.claude.call("enrichment", prompt, max_tokens=max_tokens)
                    return fallback if isinstance(fallback, str) else json.dumps(fallback, ensure_ascii=False)
                except Exception as exc:
                    log.warning(f"Fallback Claude client falhou: {exc}")
            return None
        payload = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": max_tokens,
            "tools": [{"type": "web_search_20250305", "name": "web_search"}],
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            resp = await self._async_post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
                json_data=payload,
                timeout=90,
            )
            if not resp:
                return None
            content = resp.get("content") or []
            text_blocks = [b.get("text", "") for b in content if b.get("type") == "text"]
            return "\n".join(text_blocks).strip()
        except Exception as exc:
            log.warning(f"Claude web_search falhou: {exc}")
            return None

    async def _run_apify(self, actor_id: str, payload: dict, timeout: int) -> Optional[list[dict]]:
        if not APIFY_API_KEY:
            return None
        url = f"https://api.apify.com/v2/acts/{actor_id}/run-sync-get-dataset-items?token={APIFY_API_KEY}&timeout={timeout}&memory=256"
        result = await self._async_post(url, headers={"Content-Type": "application/json"}, json_data=payload, timeout=timeout + 10)
        return result if isinstance(result, list) and result else None

    async def _async_get_json(self, url: str, headers: Optional[dict] = None, timeout: int = 30, retries: int = 2) -> Optional[dict]:
        for attempt in range(retries + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.get(url, headers=headers or {})
                if resp.status_code == 429 and attempt < retries:
                    await asyncio.sleep(1 + attempt)
                    continue
                if resp.status_code >= 400:
                    log.debug(f"GET falhou {url} status={resp.status_code}")
                    return None
                return resp.json()
            except (httpx.TimeoutException, httpx.HTTPError) as exc:
                if attempt >= retries:
                    log.warning(f"GET erro {url}: {exc}")
                    return None
                await asyncio.sleep(1 + attempt)
        return None

    async def _async_post(self, url: str, headers: dict, json_data: dict, timeout: int = 45, retries: int = 2) -> Optional[dict]:
        for attempt in range(retries + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(url, headers=headers, json=json_data)
                if resp.status_code == 429 and attempt < retries:
                    await asyncio.sleep(1 + attempt)
                    continue
                if resp.status_code >= 400:
                    log.debug(f"POST falhou {url} status={resp.status_code}")
                    return None
                return resp.json()
            except (httpx.TimeoutException, httpx.HTTPError) as exc:
                if attempt >= retries:
                    log.warning(f"POST erro {url}: {exc}")
                    return None
                await asyncio.sleep(1 + attempt)
        return None

    @staticmethod
    def _parse_employee_range(value: str) -> int:
        if not value:
            return 0
        match_range = re.search(r"(\d[\d,]*)\s*[-–]\s*(\d[\d,]*)", value)
        if match_range:
            a = int(match_range.group(1).replace(",", ""))
            b = int(match_range.group(2).replace(",", ""))
            return round((a + b) / 2)
        match_num = re.search(r"(\d[\d,]*)", value)
        return int(match_num.group(1).replace(",", "")) if match_num else 0

    @staticmethod
    def _lusha_revenue(revenue_range: Any) -> str:
        if not isinstance(revenue_range, list) or len(revenue_range) != 2:
            return ""
        return f"R${round(revenue_range[0] / 1_000_000)}M-{round(revenue_range[1] / 1_000_000)}M"

    @staticmethod
    def _ensure_http(url: str) -> str:
        if url.startswith("http"):
            return url
        return f"https://{url}"

    def close(self):
        self.http.close()
