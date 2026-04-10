"""
Supabase Client — CRUD para o pipeline SDR.
Usa httpx direto (sem SDK) para máximo controle.
"""

import json
from datetime import datetime, timezone
from typing import Optional
import httpx
from config.settings import SUPABASE_URL, SUPABASE_KEY, SUPABASE_TABLES


class SupabaseClient:

    def __init__(self, url: str = SUPABASE_URL, key: str = SUPABASE_KEY):
        self.url = url.rstrip("/")
        self.headers = {
            "apikey": key,
            "Authorization": f"Bearer {key}",
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
        lead["updated_at"] = datetime.now(timezone.utc).isoformat()
        resp = self.client.post(
            self._rest_url(SUPABASE_TABLES["leads"]),
            json=lead,
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
            return json.loads(data[0].get("data", "{}"))
        return None

    def set_cached_enrichment(self, cache_key: str, data: dict, source: str = ""):
        record = {
            "cache_key": cache_key,
            "data": json.dumps(data, ensure_ascii=False),
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

    def close(self):
        self.client.close()
