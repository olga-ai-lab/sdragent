"""
Supabase Client — CRUD para o pipeline SDR.
Usa httpx direto (sem SDK) para máximo controle.
"""

import json
from datetime import datetime, timezone
from typing import Optional
import httpx
from config.settings import SUPABASE_URL, SUPABASE_KEY, SUPABASE_SERVICE_KEY, SUPABASE_TABLES, SDR_STAGE_MAP, PIPELINE_SDR_IA

# Colunas conhecidas de companies_88i_pipeline.
# Campos extras no lead dict são descartados antes do upsert para evitar 400.
_PIPELINE_COLUMNS = {
    "empresa_id","nome","site","cnpj","segmento","icp_tipo","status","tier","porte",
    "cidade","uf","score","score_breakdown","score_icp","decisor_nome","decisor_cargo",
    "decisor_email","decisor_telefone","decisor_linkedin","telefone","seguro_atual",
    "seguradora_parceira","gap_oportunidade","entregadores_est","ai_segmento",
    "ai_tem_entregadores","ai_entregadores_est","ai_porte","ai_plataforma_digital",
    "ai_seguro_detectado","ai_formato_email","ai_risco_exclusao","ai_confianca",
    "enrichment_complete","exclusion_reason","source","place_id","produto_88i",
    "obs_estrategica","proxima_acao","updated_at","tier_88i","status_pipeline",
    "estado","linkedin_empresa","linkedin_decisor","email","score_88i","fonte",
    "data_inclusao","finder","corretor_flag","canal_comercial","contato","valor_tcv",
    "grau_confianca","atualizacao","motivo_perda","responsavel",
    # novas colunas adicionadas via migração
    "deal_value_est","deal_value_premissas","sinal_dor","sinal_dor_motivo",
    "linkedin_url","descricao_linkedin","employees_linkedin","industry_linkedin","pais",
    "sources","status_history","status_changed_at","status_reason",
    "web_pages_scraped","enrichment_source","enrichment_error","ai_decisor_sugerido",
}


# ── Mapper: agent lead dict → leads_bamaq columns ────────────────────────
_AGENT_TO_CRM: dict[str, str] = {
    "nome":             "company_name",
    "site":             "website",
    "cnpj":             "cnpj_empresa",
    "segmento":         "company_industry",
    "cidade":           "city",
    "uf":               "state",
    "porte":            "porte",
    "decisor_nome":     "full_name",
    "decisor_cargo":    "job_title",
    "decisor_email":    "email",
    "decisor_telefone": "phone",
    "decisor_linkedin": "linkedin_url",
    "source":           "source",
    "score":            "sdr_score",
    "score_breakdown":  "sdr_score_breakdown",
}

_CRM_PASSTHROUGH = {
    "empresa_id", "icp_tipo", "deal_value_est", "deal_value_premissas",
    "produto_88i", "tier_88i", "seguro_atual", "entregadores_est",
    "gap_oportunidade", "obs_estrategica", "proxima_acao",
    "linkedin_empresa", "sdr_status", "ai_enrichment",
    "bant_score", "budget_confirmado", "authority_confirmado",
    "need_confirmado", "timeline_confirmado",
    "numero_entregadores", "volume_entregas", "faixa_mensal",
}


def _map_agent_to_crm(lead: dict, pipeline_id: int = PIPELINE_SDR_IA) -> dict:
    crm: dict = {"pipeline_id": pipeline_id}
    for src, dst in _AGENT_TO_CRM.items():
        if src in lead:
            crm[dst] = lead[src]
    for key in _CRM_PASSTHROUGH:
        if key in lead:
            crm[key] = lead[key]
    sdr_status = lead.get("status") or lead.get("sdr_status") or "discovered"
    crm["sdr_status"] = sdr_status
    crm["stage_id"]   = SDR_STAGE_MAP.get(sdr_status, 42)
    crm["updated_at"] = datetime.now(timezone.utc).isoformat()
    return crm


class SupabaseClient:

    def __init__(self, url: str = SUPABASE_URL, key: str = SUPABASE_KEY):
        self.url = url.rstrip("/")
        # Prefer service_role key for server-side usage — bypasses API allowlist
        effective_key = SUPABASE_SERVICE_KEY or key
        self.headers = {
            "apikey": effective_key,
            "Authorization": f"Bearer {effective_key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }
        self.client = httpx.Client(timeout=30.0, headers=self.headers)

    def _rest_url(self, table: str) -> str:
        return f"{self.url}/rest/v1/{table}"

    # ───────────────────────────────────────────
    # LEADS
    # ───────────────────────────────────────────

    def upsert_lead(self, lead: dict) -> dict:
        """Upsert lead no pipeline — deduplicação por empresa_id."""
        # Filtra apenas colunas conhecidas para evitar erro 400 do PostgREST
        payload = {k: v for k, v in lead.items() if k in _PIPELINE_COLUMNS}
        payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        # web_content pode ser grande — não persistir no pipeline
        payload.pop("web_content", None)
        resp = self.client.post(
            self._rest_url(SUPABASE_TABLES["leads"]),
            json=payload,
            headers={**self.headers, "Prefer": "return=representation,resolution=merge-duplicates"},
        )
        resp.raise_for_status()
        return resp.json()

    def get_leads(self, status: Optional[str] = None, icp: Optional[str] = None, limit: int = 100) -> list[dict]:
        """Busca leads com filtros opcionais."""
        params = {"select": "*", "limit": str(limit), "order": "score.desc"}
        if status:
            params["status"] = f"eq.{status}"
        if icp:
            params["icp_tipo"] = f"eq.{icp}"
        resp = self.client.get(self._rest_url(SUPABASE_TABLES["leads"]), params=params)
        resp.raise_for_status()
        return resp.json()

    def update_lead(self, empresa_id: str, updates: dict) -> dict:
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        resp = self.client.patch(
            self._rest_url(SUPABASE_TABLES["leads"]),
            params={"empresa_id": f"eq.{empresa_id}"},
            json=updates,
        )
        resp.raise_for_status()
        return resp.json()

    def lead_exists(self, empresa_nome: str) -> bool:
        resp = self.client.get(
            self._rest_url(SUPABASE_TABLES["leads"]),
            params={"select": "id", "nome": f"eq.{empresa_nome}", "limit": "1"},
        )
        return len(resp.json()) > 0

    # ───────────────────────────────────────────
    # OUTREACH LOG
    # ───────────────────────────────────────────

    def log_outreach(self, empresa_id: str, step: dict) -> dict:
        record = {
            "empresa_id": empresa_id,
            "canal": step.get("canal"),
            "tipo": step.get("tipo"),
            "mensagem": step.get("mensagem", ""),
            "status": step.get("status", "sent"),
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
        resp = self.client.post(self._rest_url(SUPABASE_TABLES["outreach_log"]), json=record)
        resp.raise_for_status()
        return resp.json()

    def upsert_lead_intelligence(self, empresa_id: str, payload: dict) -> dict:
        """Persiste intelligence consolidada do lead (1 linha por empresa)."""
        record = {
            "empresa_id": empresa_id,
            "intelligence": payload,
            "score": payload.get("score"),
            "score_breakdown": payload.get("score_breakdown"),
            "deal_value_est": payload.get("deal_value_est"),
            "deal_value_premissas": payload.get("deal_value_premissas"),
            "canal_recomendado": payload.get("canal_recomendado"),
            "closing_intelligence": payload.get("closing_intelligence"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        resp = self.client.post(
            self._rest_url(SUPABASE_TABLES["lead_intelligence"]),
            json=record,
            headers={**self.headers, "Prefer": "return=representation,resolution=merge-duplicates"},
        )
        resp.raise_for_status()
        return resp.json()

    def get_lead_intelligence(self, empresa_id: str) -> Optional[dict]:
        resp = self.client.get(
            self._rest_url(SUPABASE_TABLES["lead_intelligence"]),
            params={"empresa_id": f"eq.{empresa_id}", "limit": "1", "select": "*"},
        )
        resp.raise_for_status()
        data = resp.json()
        return data[0] if data else None

    def insert_lead_event(self, event: dict) -> dict:
        """Armazena evento bruto de webhook para rastreabilidade/idempotência."""
        event = {
            **event,
            "created_at": event.get("created_at") or datetime.now(timezone.utc).isoformat(),
        }
        resp = self.client.post(self._rest_url(SUPABASE_TABLES["lead_events"]), json=event)
        resp.raise_for_status()
        return resp.json()

    def webhook_event_exists(self, provider: str, external_event_id: str) -> bool:
        if not external_event_id:
            return False
        resp = self.client.get(
            self._rest_url(SUPABASE_TABLES["lead_events"]),
            params={
                "select": "id",
                "provider": f"eq.{provider}",
                "external_event_id": f"eq.{external_event_id}",
                "limit": "1",
            },
        )
        resp.raise_for_status()
        return len(resp.json()) > 0

    def get_outreach_history(self, empresa_id: str) -> list[dict]:
        resp = self.client.get(
            self._rest_url(SUPABASE_TABLES["outreach_log"]),
            params={"empresa_id": f"eq.{empresa_id}", "order": "sent_at.desc"},
        )
        resp.raise_for_status()
        return resp.json()

    # ───────────────────────────────────────────
    # ENRICHMENT CACHE — Regra de Ouro #7
    # ───────────────────────────────────────────

    def get_cached_enrichment(self, cache_key: str) -> Optional[dict]:
        resp = self.client.get(
            self._rest_url(SUPABASE_TABLES["enrichment_cache"]),
            params={"cache_key": f"eq.{cache_key}", "limit": "1"},
        )
        data = resp.json()
        if data and len(data) > 0:
            raw = data[0].get("data") or {}
            # data é jsonb → já retorna como dict; fallback para string legada
            return raw if isinstance(raw, dict) else json.loads(raw)
        return None

    def set_cached_enrichment(self, cache_key: str, data: dict, source: str = ""):
        record = {
            "cache_key": cache_key,
            "data": data,          # jsonb — não serializar para string
            "source": source,
            "cached_at": datetime.now(timezone.utc).isoformat(),
        }
        self.client.post(
            self._rest_url(SUPABASE_TABLES["enrichment_cache"]),
            json=record,
            headers={**self.headers, "Prefer": "return=representation,resolution=merge-duplicates"},
        )

    # ───────────────────────────────────────────
    # MEETINGS
    # ───────────────────────────────────────────

    def book_meeting(self, empresa_id: str, decisor: str, data_reuniao: str, notas: str = "") -> dict:
        record = {
            "empresa_id": empresa_id,
            "decisor": decisor,
            "data_reuniao": data_reuniao,
            "notas": notas,
            "status": "agendada",
            "booked_at": datetime.now(timezone.utc).isoformat(),
        }
        resp = self.client.post(self._rest_url(SUPABASE_TABLES["meetings"]), json=record)
        resp.raise_for_status()
        return resp.json()

    # ───────────────────────────────────────────
    # RAW SQL (via RPC)
    # ───────────────────────────────────────────

    def rpc(self, function_name: str, params: dict = None) -> list:
        resp = self.client.post(f"{self.url}/rest/v1/rpc/{function_name}", json=params or {})
        resp.raise_for_status()
        return resp.json()

    # ───────────────────────────────────────────
    # CRM — leads_bamaq (Pipeline 6 Vendas IA)
    # ───────────────────────────────────────────

    def upsert_crm_lead(self, lead: dict) -> dict:
        """Upsert em leads_bamaq, dedup por empresa_id."""
        payload = _map_agent_to_crm(lead)
        resp = self.client.post(
            self._rest_url(SUPABASE_TABLES["crm_leads"]),
            json=payload,
            headers={**self.headers,
                     "Prefer": "return=representation,resolution=merge-duplicates"},
        )
        resp.raise_for_status()
        return resp.json()

    def get_crm_leads(self, pipeline_id: int = PIPELINE_SDR_IA,
                      stage_id: int = None, limit: int = 100) -> list[dict]:
        params = {
            "select": "*",
            "pipeline_id": f"eq.{pipeline_id}",
            "limit": str(limit),
            "order": "sdr_score.desc.nullslast",
        }
        if stage_id:
            params["stage_id"] = f"eq.{stage_id}"
        resp = self.client.get(self._rest_url(SUPABASE_TABLES["crm_leads"]), params=params)
        resp.raise_for_status()
        return resp.json()

    def update_crm_stage(self, empresa_id: str, stage_id: int,
                         sdr_status: str = "") -> dict:
        updates: dict = {
            "stage_id": stage_id,
            "stage_entered_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if sdr_status:
            updates["sdr_status"] = sdr_status
        resp = self.client.patch(
            self._rest_url(SUPABASE_TABLES["crm_leads"]),
            params={"empresa_id": f"eq.{empresa_id}"},
            json=updates,
        )
        resp.raise_for_status()
        return resp.json()

    def get_crm_lead_by_empresa_id(self, empresa_id: str) -> Optional[dict]:
        resp = self.client.get(
            self._rest_url(SUPABASE_TABLES["crm_leads"]),
            params={"empresa_id": f"eq.{empresa_id}", "limit": "1", "select": "*"},
        )
        resp.raise_for_status()
        data = resp.json()
        return data[0] if data else None

    def close(self):
        self.client.close()
