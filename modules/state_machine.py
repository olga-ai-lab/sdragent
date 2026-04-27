"""
State Machine — Ciclo de vida do lead com transições protegidas.
Impede transições inválidas e registra toda mudança.
"""

from datetime import datetime, timezone
from modules.logger import get_logger

log = get_logger("sdr.state")

# ═══════════════════════════════════════════════════════════════
# TRANSIÇÕES VÁLIDAS
# ═══════════════════════════════════════════════════════════════
#
#  discovered → enriched → HOT/WARM/COLD → contacted → replied
#                                                    → meeting_booked → won/lost
#                                          → no_response → nurture/archived
#  (qualquer) → excluded
#
VALID_TRANSITIONS = {
    "discovered":      ["enriched", "excluded"],
    "enriched":        ["HOT", "WARM", "COLD", "excluded"],
    "HOT":             ["contacted", "excluded", "WARM"],
    "WARM":            ["contacted", "excluded", "HOT", "COLD"],
    "COLD":            ["contacted", "excluded", "WARM", "nurture", "archived"],
    "contacted":       ["replied", "no_response", "meeting_booked", "excluded"],
    "replied":         ["meeting_booked", "contacted", "nurture", "lost"],
    "no_response":     ["contacted", "nurture", "archived"],
    "meeting_booked":  ["won", "lost", "no_show"],
    "no_show":         ["contacted", "meeting_booked", "lost", "archived"],
    "nurture":         ["contacted", "HOT", "WARM", "archived"],
    "won":             [],  # estado final
    "lost":            ["nurture", "contacted"],  # pode reativar
    "archived":        ["contacted", "nurture"],  # pode reativar
    "excluded":        [],  # estado final (mas pode ser revertido manualmente)
}


class LeadStateMachine:

    def __init__(self, supabase_client=None):
        self.supabase = supabase_client

    def can_transition(self, current_status: str, new_status: str) -> bool:
        """Verifica se a transição é válida."""
        allowed = VALID_TRANSITIONS.get(current_status, [])
        return new_status in allowed

    def transition(self, lead: dict, new_status: str, reason: str = "") -> dict:
        """
        Executa transição de estado com validação.
        Retorna lead atualizado ou levanta ValueError se transição inválida.
        """
        current = lead.get("status", "discovered")
        empresa = lead.get("nome", lead.get("empresa_id", "?"))

        if not self.can_transition(current, new_status):
            msg = f"Transição inválida: {current} → {new_status} para '{empresa}'"
            log.error(msg, extra={
                "empresa": empresa,
                "current_status": current,
                "attempted_status": new_status,
            })
            raise ValueError(msg)

        # Executar transição
        lead["status"] = new_status
        lead["status_changed_at"] = datetime.now(timezone.utc).isoformat()
        lead["status_reason"] = reason

        # Adicionar ao histórico
        history = lead.get("status_history", [])
        history.append({
            "from": current,
            "to": new_status,
            "reason": reason,
            "at": lead["status_changed_at"],
        })
        lead["status_history"] = history

        log.info(
            f"Transição: {current} → {new_status} | {empresa}",
            extra={
                "empresa": empresa,
                "from_status": current,
                "to_status": new_status,
                "reason": reason,
            },
        )

        # Persist no Supabase
        if self.supabase:
            try:
                self.supabase.update_lead(
                    lead.get("empresa_id", empresa),
                    {
                        "status": new_status,
                        "status_changed_at": lead["status_changed_at"],
                    },
                )
            except Exception as e:
                log.error(f"Erro persistindo transição no Supabase: {e}")

            try:
                from config.settings import SDR_STAGE_MAP
                stage_id = SDR_STAGE_MAP.get(new_status, 42)
                self.supabase.update_crm_stage(
                    lead.get("empresa_id", empresa),
                    stage_id,
                    sdr_status=new_status,
                )
            except Exception as e:
                log.error(f"Erro atualizando stage CRM: {e}")

        return lead

    def get_available_transitions(self, current_status: str) -> list[str]:
        """Lista transições possíveis a partir do status atual."""
        return VALID_TRANSITIONS.get(current_status, [])

    def bulk_transition(self, leads: list[dict], new_status: str, reason: str = "") -> tuple[list[dict], list[dict]]:
        """
        Transição em batch. Retorna (sucesso, falhas).
        """
        success = []
        failures = []
        for lead in leads:
            try:
                updated = self.transition(lead, new_status, reason)
                success.append(updated)
            except ValueError:
                failures.append(lead)
        return success, failures
