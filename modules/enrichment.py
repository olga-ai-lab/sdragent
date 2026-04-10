"""
Enrichment Module — Enriquecimento de leads via Lusha + Claude.
Custo: Lusha $50-150/mês · Claude (Haiku) ~$0.001/lead
"""

import json
from typing import Optional
import httpx
from config.settings import LUSHA_API_KEY, API_ENDPOINTS
from modules.claude_client import ClaudeClient


class LeadEnrichment:

    def __init__(self, claude: ClaudeClient, lusha_key: str = LUSHA_API_KEY):
        self.claude = claude
        self.lusha_key = lusha_key
        self.client = httpx.Client(timeout=30.0)

    # ───────────────────────────────────────────
    # LUSHA — Email + Telefone + Decisor
    # ───────────────────────────────────────────

    def enrich_lusha(self, domain: str) -> Optional[dict]:
        """
        Busca decisores via Lusha Domain Search.
        Retorna: nome, cargo, email, telefone, LinkedIn.
        """
        if not self.lusha_key or not domain:
            return None

        try:
            resp = self.client.get(
                "https://api.lusha.com/v2/company/enrich",
                params={"domain": domain},
                headers={"api_key": self.lusha_key},
            )
            if resp.status_code != 200:
                return None

            data = resp.json()
            contacts = data.get("contacts", [])

            # Priorizar decisores: CEO > COO > CFO > Dir > VP > Head > Gerente
            priority_titles = [
                "ceo", "chief executive", "fundador", "founder",
                "coo", "chief operating", "diretor operac",
                "cfo", "chief financial", "diretor financ",
                "diretor", "director", "vp", "vice president",
                "head", "gerente", "manager",
            ]

            best_contact = None
            best_priority = len(priority_titles)

            for contact in contacts:
                title = (contact.get("title") or "").lower()
                for i, keyword in enumerate(priority_titles):
                    if keyword in title:
                        if i < best_priority:
                            best_priority = i
                            best_contact = contact
                        break

            if not best_contact and contacts:
                best_contact = contacts[0]

            if best_contact:
                return {
                    "decisor_nome": best_contact.get("full_name", ""),
                    "decisor_cargo": best_contact.get("title", ""),
                    "decisor_email": best_contact.get("email", ""),
                    "decisor_telefone": best_contact.get("phone", ""),
                    "decisor_linkedin": best_contact.get("linkedin_url", ""),
                    "source": "lusha",
                }

        except Exception as e:
            print(f"  ⚠️  Lusha erro para {domain}: {e}")

        return None

    # ───────────────────────────────────────────
    # CLAUDE AI — Extração de dados web
    # ───────────────────────────────────────────

    def enrich_with_ai(self, lead: dict, web_content: str = "") -> dict:
        """
        Usa Claude (Haiku) para extrair/inferir dados do lead.
        Regra de Ouro #4: output JSON, max_tokens=300
        """
        empresa = lead.get("nome", "")
        site = lead.get("site", "")
        categoria = lead.get("categoria_google", "")

        prompt = f"""Analise esta empresa e extraia dados para qualificação como prospect de seguro para entregadores:

EMPRESA: {empresa}
SITE: {site}
CATEGORIA GOOGLE: {categoria}
CIDADE: {lead.get('cidade', '')}
{f'CONTEÚDO WEB: {web_content[:2000]}' if web_content else ''}

Extraia em JSON:
{{
  "segmento": "food delivery | moto delivery | quick commerce | logistica | courier urbano | ecommerce | tms | outro",
  "tem_entregadores": true/false,
  "entregadores_estimado": "número ou faixa (ex: 500-1000) ou null",
  "porte": "grande | medio | pequeno | micro",
  "plataforma_digital": true/false,
  "seguro_delivery_detectado": "sim | nao | desconhecido",
  "decisor_sugerido_cargo": "CEO | COO | Dir. Operações | Head Logística | null",
  "formato_email_provavel": "nome@dominio.com.br ou [nome]@dominio.com.br",
  "risco_exclusao": "tipo de exclusão se não for ICP válido, ou null",
  "confianca": 0.0-1.0
}}"""

        try:
            result = self.claude.call(
                "enrichment",
                prompt,
                max_tokens=300,
                json_output=True,
            )
            if isinstance(result, dict):
                return result
        except Exception as e:
            print(f"  ⚠️  Claude enrich erro para {empresa}: {e}")

        return {}

    # ───────────────────────────────────────────
    # PIPELINE DE ENRIQUECIMENTO COMPLETO
    # ───────────────────────────────────────────

    def enrich_lead(self, lead: dict, use_lusha: bool = True) -> dict:
        """
        Pipeline completo de enriquecimento:
        1. Lusha (email, telefone, decisor)
        2. Claude AI (segmento, porte, seguro)
        3. Merge dos dados
        """
        enriched = {**lead}
        domain = self._extract_domain(lead.get("site", ""))

        # Step 1: Lusha
        if use_lusha and domain:
            lusha_data = self.enrich_lusha(domain)
            if lusha_data:
                enriched.update(lusha_data)
                print(f"  📧 Lusha: {lusha_data.get('decisor_nome', '?')} — {lusha_data.get('decisor_cargo', '?')}")

        # Step 2: Claude AI enrichment
        ai_data = self.enrich_with_ai(lead)
        if ai_data:
            enriched["ai_segmento"] = ai_data.get("segmento")
            enriched["ai_tem_entregadores"] = ai_data.get("tem_entregadores")
            enriched["ai_entregadores_est"] = ai_data.get("entregadores_estimado")
            enriched["ai_porte"] = ai_data.get("porte")
            enriched["ai_plataforma_digital"] = ai_data.get("plataforma_digital")
            enriched["ai_seguro_detectado"] = ai_data.get("seguro_delivery_detectado")
            enriched["ai_formato_email"] = ai_data.get("formato_email_provavel")
            enriched["ai_risco_exclusao"] = ai_data.get("risco_exclusao")
            enriched["ai_confianca"] = ai_data.get("confianca", 0.0)

            # Se AI detectou que não é ICP válido
            if ai_data.get("risco_exclusao"):
                enriched["status"] = "excluded"
                enriched["exclusion_reason"] = ai_data["risco_exclusao"]

        enriched["enrichment_complete"] = True
        return enriched

    def enrich_batch(self, leads: list[dict], use_lusha: bool = True) -> list[dict]:
        """Enriquece batch de leads com progress."""
        enriched = []
        total = len(leads)
        for i, lead in enumerate(leads):
            print(f"\n[{i+1}/{total}] 🔄 Enriquecendo: {lead.get('nome', '?')}")
            result = self.enrich_lead(lead, use_lusha)
            enriched.append(result)
        return enriched

    # ───────────────────────────────────────────
    # HELPERS
    # ───────────────────────────────────────────

    def _extract_domain(self, url: str) -> str:
        if not url:
            return ""
        url = url.replace("https://", "").replace("http://", "").replace("www.", "")
        return url.split("/")[0].strip()

    def close(self):
        self.client.close()
