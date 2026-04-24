"""
Webhook Server — callbacks da Evolution API com robustez operacional.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from config.settings import EVOLUTION_API_KEY
from modules.claude_client import ClaudeClient
from modules.logger import get_logger
from modules.outreach import OutreachEngine
from modules.state_machine import LeadStateMachine
from modules.supabase_client import SupabaseClient

log = get_logger("sdr.webhook")

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_supabase: Optional[SupabaseClient] = None
_state_machine: Optional[LeadStateMachine] = None
_conversation_agent: Optional["ConversationAgent"] = None


def init_webhook(supabase: SupabaseClient):
    global _supabase, _state_machine, _conversation_agent
    _supabase = supabase
    _state_machine = LeadStateMachine(supabase)
    _conversation_agent = ConversationAgent(supabase)
    log.info("Webhook Evolution API inicializado")


def close_webhook():
    if _conversation_agent:
        _conversation_agent.close()


class ConversationAgent:
    """
    Responde conversas inbound com contexto do lead e envia via Evolution API.

    O classificador de intent continua deterministico; o Claude entra apenas para
    redigir a resposta contextual.
    """

    def __init__(
        self,
        supabase: SupabaseClient,
        claude: Optional[ClaudeClient] = None,
        outreach: Optional[OutreachEngine] = None,
    ):
        self.supabase = supabase
        self.claude = claude or ClaudeClient()
        self.outreach = outreach or OutreachEngine(self.claude)

    def respond(
        self,
        lead: dict,
        phone: str,
        inbound_message: str,
        intent: str,
        next_best_action: dict,
        intelligence: dict,
    ) -> dict:
        empresa_id = lead.get("empresa_id") or lead.get("nome") or ""
        if not phone:
            return {"status": "ignored", "reason": "no_phone"}

        generation_status = "claude"
        try:
            reply = self._generate_reply(
                lead=lead,
                inbound_message=inbound_message,
                intent=intent,
                next_best_action=next_best_action,
                intelligence=intelligence,
            )
        except Exception as exc:
            generation_status = "fallback"
            log.warning(f"ConversationAgent Claude fallback para {empresa_id}: {exc}")
            reply = self._fallback_reply(intent, next_best_action)

        if not reply:
            return {"status": "ignored", "reason": "empty_reply"}

        send_result = self.outreach.send_whatsapp(phone, reply)
        self._log_agent_reply(empresa_id, reply, send_result)

        return {
            "status": send_result.get("status", "unknown"),
            "generation": generation_status,
            "reply": reply,
            "provider_response": send_result.get("response"),
            "error": send_result.get("error"),
        }

    def _generate_reply(
        self,
        lead: dict,
        inbound_message: str,
        intent: str,
        next_best_action: dict,
        intelligence: dict,
    ) -> str:
        system = (
            "Voce e o ConversationAgent da 88i Seguradora Digital. "
            "Responda como um SDR humano em portugues do Brasil, de forma curta, "
            "profissional e util. Nao mencione que voce e IA. Nao use emojis. "
            "Nao invente precos, datas, clientes ou coberturas que nao estejam no contexto. "
            "Se o lead recusou, encerre com respeito. Se pediu detalhes, explique em poucas "
            "linhas e puxe uma proxima acao simples."
        )
        payload = {
            "lead": self._lead_context(lead),
            "closing_intelligence": self._conversation_intelligence(intelligence),
            "inbound_message": inbound_message,
            "classified_intent": intent,
            "next_best_action": next_best_action,
            "output_rules": {
                "max_chars": 650,
                "channel": "whatsapp",
                "single_message": True,
            },
        }
        prompt = (
            "Gere somente o texto da resposta que sera enviado no WhatsApp.\n\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
        )
        result = self.claude.call(
            "outreach",
            prompt,
            system=system,
            max_tokens=260,
            temperature=0.2,
        )
        return self._clean_reply(str(result))

    def _lead_context(self, lead: dict) -> dict:
        fields = (
            "empresa_id", "nome", "icp_tipo", "status", "score",
            "segmento", "ai_segmento", "porte", "ai_porte",
            "entregadores_est", "ai_entregadores_est",
            "produto_88i", "sinal_dor", "sinal_dor_motivo",
            "decisor_nome", "decisor_cargo", "cidade", "uf",
            "obs_estrategica", "proxima_acao",
        )
        return {field: lead.get(field) for field in fields if lead.get(field) not in (None, "")}

    def _conversation_intelligence(self, intelligence: dict) -> dict:
        if not isinstance(intelligence, dict):
            return {}
        fields = (
            "abertura", "pitch_1_frase", "objecao_1", "resposta_1",
            "timing", "pontos_conexao", "o_que_evitar",
        )
        return {field: intelligence.get(field) for field in fields if intelligence.get(field)}

    def _fallback_reply(self, intent: str, next_best_action: dict) -> str:
        suggested = (next_best_action or {}).get("suggested_reply")
        if suggested:
            return self._clean_reply(str(suggested))
        return {
            "not_interested": "Obrigado pelo retorno. Vou encerrar os contatos por aqui.",
            "invalid_number": "Obrigado por avisar. Vou remover este contato da nossa base.",
            "redirect": "Perfeito, obrigado. Pode me informar o melhor contato para tratar desse tema?",
            "talk_later": "Combinado. Qual melhor dia e horario para eu te chamar?",
        }.get(intent, "Obrigado pelo retorno. Pode me dar um pouco mais de contexto para eu te responder melhor?")

    def _clean_reply(self, reply: str, max_chars: int = 650) -> str:
        text = reply.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
            text = re.sub(r"\s*```$", "", text).strip()
        if text.lower().startswith("resposta:"):
            text = text.split(":", 1)[1].strip()
        if len(text) <= max_chars:
            return text
        truncated = text[:max_chars].rsplit(".", 1)[0].strip()
        return truncated or text[:max_chars].strip()

    def _log_agent_reply(self, empresa_id: str, reply: str, send_result: dict):
        if not empresa_id:
            return
        try:
            self.supabase.log_outreach(
                empresa_id,
                {
                    "canal": "whatsapp",
                    "tipo": "conversation_agent_reply",
                    "mensagem": reply,
                    "status": send_result.get("status", "unknown"),
                },
            )
        except Exception as exc:
            log.warning(f"Falha logando resposta do ConversationAgent: {exc}")

    def close(self):
        self.outreach.close()
        self.claude.close()


@router.post("/evolution/receive")
async def evolution_receive_message(request: Request):
    """Recebe eventos da Evolution (inbound/status) com idempotência e persistência raw."""
    _validate_api_key(request)

    try:
        body = await request.json()
    except Exception:
        log.warning("Payload malformado no webhook receive")
        return {"status": "ignored", "reason": "malformed_json"}

    if not isinstance(body, dict):
        return {"status": "ignored", "reason": "invalid_payload"}

    event_name = str(body.get("event") or "")
    data = body.get("data") or {}
    key = data.get("key") or {}
    message_id = key.get("id") or data.get("id") or ""
    event_type = _detect_event_type(event_name, body)
    external_event_id = f"{event_type}:{message_id or key.get('remoteJid', '')}:{data.get('messageTimestamp', '')}"

    if _supabase and _supabase.webhook_event_exists("evolution", external_event_id):
        return {"status": "ignored", "reason": "duplicate_event", "event_type": event_type}

    phone = _extract_phone(body)
    empresa_id = ""
    lead = _find_lead_by_phone(phone) if phone else None
    if lead:
        empresa_id = lead.get("empresa_id") or lead.get("nome") or ""

    if _supabase:
        _persist_raw_event(
            empresa_id=empresa_id or f"unknown_{phone or 'na'}",
            event_type=event_type,
            external_event_id=external_event_id,
            phone=phone,
            payload=body,
        )

    if event_type != "inbound_message":
        return _process_non_inbound_event(event_type, phone, lead)

    return _process_inbound_event(body, phone, lead, message_id)


@router.post("/evolution/status")
async def evolution_status_update(request: Request):
    """Compat endpoint legado para status callbacks."""
    _validate_api_key(request)
    try:
        body = await request.json()
    except Exception:
        return {"status": "ignored", "reason": "malformed_json"}

    event_type = _detect_event_type(str(body.get("event") or ""), body)
    phone = _extract_phone(body)
    lead = _find_lead_by_phone(phone) if phone else None
    return _process_non_inbound_event(event_type, phone, lead)


@router.get("/health")
async def webhook_health():
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "supabase_connected": _supabase is not None,
    }


def _validate_api_key(request: Request):
    apikey = request.headers.get("apikey", "")
    if EVOLUTION_API_KEY and apikey != EVOLUTION_API_KEY:
        log.warning("Webhook Evolution API: apikey inválida")
        raise HTTPException(status_code=401, detail="Invalid apikey")


def _process_non_inbound_event(event_type: str, phone: str, lead: Optional[dict]):
    empresa_id = (lead or {}).get("empresa_id") or (lead or {}).get("nome") or (f"unknown_{phone}" if phone else "unknown")
    if _supabase:
        _supabase.log_outreach(
            empresa_id,
            {
                "canal": "whatsapp",
                "tipo": event_type,
                "mensagem": "",
                "status": _map_event_status(event_type),
            },
        )
    return {"status": "processed", "event_type": event_type, "phone": phone}


def _process_inbound_event(body: dict, phone: str, lead: Optional[dict], message_id: str):
    message = _extract_message(body)
    if not phone or not message:
        return {"status": "ignored", "reason": "no_content"}

    if not _supabase:
        log.error("Supabase não disponível no webhook")
        return {"status": "error", "reason": "no_db"}

    if not lead:
        _supabase.log_outreach(
            f"unknown_{phone}",
            {
                "canal": "whatsapp",
                "tipo": "inbound_reply",
                "mensagem": message,
                "status": "unmatched",
            },
        )
        return {"status": "unmatched", "phone": phone}

    empresa_id = lead.get("empresa_id") or lead.get("nome") or ""
    _supabase.log_outreach(
        empresa_id,
        {
            "canal": "whatsapp",
            "tipo": "inbound_reply",
            "mensagem": message,
            "status": "replied",
        },
    )

    intelligence_row = _supabase.get_lead_intelligence(empresa_id) or {}
    intelligence = intelligence_row.get("closing_intelligence") or {}

    intent = _classify_reply_intent(message)
    nba = _next_best_action(message, intent, intelligence)

    lead_updates = {
        "last_interaction_at": datetime.now(timezone.utc).isoformat(),
        "last_interaction_channel": "whatsapp",
        "followup_paused": True,
        "next_best_action": json.dumps(nba, ensure_ascii=False),
    }
    try:
        _supabase.update_lead(empresa_id, lead_updates)
    except Exception as exc:
        log.warning(f"Falha atualizando lead após inbound: {exc}")

    if _state_machine and lead.get("status") == "contacted":
        try:
            _state_machine.transition(lead, "replied", f"WhatsApp reply: {message[:60]}")
        except ValueError:
            pass

    agent_result = {"status": "disabled"}
    if _conversation_agent:
        agent_result = _conversation_agent.respond(
            lead=lead,
            phone=phone,
            inbound_message=message,
            intent=intent,
            next_best_action=nba,
            intelligence=intelligence,
        )

    return {
        "status": "processed",
        "empresa": lead.get("nome"),
        "intent": intent,
        "classification": nba["classification"],
        "recommended_action": nba["recommended_action"],
        "agent_reply": agent_result,
        "message_id": message_id,
    }


def _persist_raw_event(empresa_id: str, event_type: str, external_event_id: str, phone: str, payload: dict):
    if not _supabase:
        return
    try:
        _supabase.insert_lead_event(
            {
                "empresa_id": empresa_id,
                "provider": "evolution",
                "event_type": event_type,
                "external_event_id": external_event_id,
                "phone_normalized": phone,
                "payload_raw": payload,
            }
        )
    except Exception as exc:
        log.warning(f"Falha persistindo raw webhook event: {exc}")


def _detect_event_type(event_name: str, body: dict) -> str:
    data = body.get("data") or {}
    status = ((data.get("update") or {}).get("status") or "").upper()
    if event_name == "messages.update":
        if "READ" in status:
            return "read_status"
        if "DELIVER" in status or "ACK" in status:
            return "delivery_status"
        if "ERROR" in status or "FAIL" in status:
            return "error_status"
        return "status_update"

    if event_name in ("messages.upsert", ""):
        key = data.get("key") or {}
        if key.get("fromMe"):
            return "outbound_echo"
        return "inbound_message"

    return "unknown"


def _extract_message(body: dict) -> str:
    data = body.get("data") or {}
    msg_obj = data.get("message") or {}
    return (
        msg_obj.get("conversation")
        or ((msg_obj.get("extendedTextMessage") or {}).get("text"))
        or ((msg_obj.get("imageMessage") or {}).get("caption"))
        or ""
    )


def _extract_phone(body: dict) -> str:
    data = body.get("data") or {}
    key = data.get("key") or {}
    jid = key.get("remoteJid") or ""
    if "@g.us" in jid:
        return ""
    return _normalize_phone(jid)


def _normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("55") and len(digits) > 11:
        return digits[2:]
    return digits


def _find_lead_by_phone(phone: str) -> Optional[dict]:
    if not _supabase or not phone:
        return None
    try:
        from config.settings import SUPABASE_TABLES

        resp = _supabase.client.get(
            _supabase._rest_url(SUPABASE_TABLES["leads"]),
            params={
                "select": "*",
                "or": f"(decisor_telefone.ilike.%{phone}%,telefone.ilike.%{phone}%)",
                "limit": "1",
            },
        )
        results = resp.json()
        return results[0] if results else None
    except Exception as exc:
        log.error(f"Erro buscando lead por telefone: {exc}")
        return None


def _map_event_status(event_type: str) -> str:
    return {
        "delivery_status": "delivered",
        "read_status": "read",
        "error_status": "error",
    }.get(event_type, "sent")


def _classify_reply_intent(message: str) -> str:
    msg_lower = (message or "").lower().strip()

    if any(kw in msg_lower for kw in ["número errado", "numero errado", "engano", "não sou", "nao sou"]):
        return "invalid_number"
    if any(kw in msg_lower for kw in ["sem interesse", "não quero", "nao quero", "pare", "stop", "remover"]):
        return "not_interested"
    if any(kw in msg_lower for kw in ["proposta", "manda proposta", "envie proposta"]):
        return "asked_proposal"
    if any(kw in msg_lower for kw in ["fala com", "manda para", "email", "responsável", "responsavel", "não sou eu", "nao sou eu"]):
        return "redirect"
    if any(kw in msg_lower for kw in ["depois", "outra hora", "próxima semana", "proximo mes"]):
        return "talk_later"
    if any(kw in msg_lower for kw in ["sim", "interesse", "vamos conversar", "pode ligar", "pode me ligar"]):
        return "interested"
    if any(kw in msg_lower for kw in ["quanto custa", "preço", "valor", "detalhes", "me explica", "como funciona"]):
        return "info_request"
    return "insufficient_context"


def _next_best_action(message: str, intent: str, intelligence: dict) -> dict:
    scripts = intelligence.get("roteiro") or intelligence.get("pitch_1_frase") or ""
    objecao = intelligence.get("objecao_1") or ""
    resposta_obj = intelligence.get("resposta_1") or ""

    if intent in ("interested", "asked_proposal"):
        return {
            "classification": "respondeu_interessado" if intent == "interested" else "pediu_proposta",
            "urgency": "high",
            "recommended_action": "agendar_reuniao" if intent == "interested" else "enviar_proposta_curta",
            "suggested_reply": scripts or "Perfeito! Posso te enviar uma proposta objetiva e já sugerir agenda.",
            "confidence": 0.9,
        }
    if intent == "talk_later":
        return {
            "classification": "pediu_para_falar_depois",
            "urgency": "medium",
            "recommended_action": "reagendar_followup",
            "suggested_reply": "Combinado. Qual melhor dia/horário para eu te chamar?",
            "confidence": 0.86,
        }
    if intent == "not_interested":
        return {
            "classification": "recusou",
            "urgency": "low",
            "recommended_action": "encerrar_cadencia",
            "suggested_reply": "Perfeito, obrigado pelo retorno. Vou encerrar os contatos por aqui.",
            "confidence": 0.95,
        }
    if intent == "redirect":
        return {
            "classification": "redirecionou_contato",
            "urgency": "medium",
            "recommended_action": "atualizar_decisor",
            "suggested_reply": "Perfeito, obrigado. Pode me informar o melhor contato para tratar desse tema?",
            "confidence": 0.8,
        }
    if intent == "invalid_number":
        return {
            "classification": "numero_invalido",
            "urgency": "medium",
            "recommended_action": "higienizar_contato",
            "suggested_reply": "Obrigado por avisar. Vou remover este contato da nossa base.",
            "confidence": 0.98,
        }
    return {
        "classification": "sem_contexto_suficiente",
        "urgency": "medium",
        "recommended_action": "pedir_clarificacao",
        "suggested_reply": resposta_obj or objecao or f"Obrigado pelo retorno. Você pode detalhar melhor sua necessidade? Mensagem recebida: {message[:80]}",
        "confidence": 0.62,
    }
