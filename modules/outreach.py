"""
Outreach Module — Cadência SDR 14 dias com WhatsApp, Email, LinkedIn.
Z-API para WhatsApp · SMTP para email · LinkedIn (manual ou API).
"""

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Optional
import httpx
from config.settings import (
    EVOLUTION_API_URL, EVOLUTION_API_KEY, EVOLUTION_INSTANCE,
    API_ENDPOINTS, CADENCIA_SDR, PRODUTOS_88I,
)
from modules.claude_client import ClaudeClient


# ═══════════════════════════════════════════════════════════════
# TEMPLATES DE MENSAGEM
# ═══════════════════════════════════════════════════════════════

TEMPLATES = {
    # ─── WhatsApp ───
    "whatsapp_intro": """Olá {decisor_nome}, tudo bem?

Sou {remetente} da 88i Seguradora Digital. Notei que a {empresa} opera com entregadores e gostaria de compartilhar como estamos ajudando empresas do setor a se adequar à Lei 14.297 — que exige seguro AP desde o primeiro entregador.

{hook_personalizado}

Posso te explicar em 5 minutos como funciona?""",

    "whatsapp_valor": """Oi {decisor_nome}, passando rapidamente.

A multa por evento de alocação sem seguro é de R$1.000. Com {entregadores_est} entregadores, a exposição da {empresa} pode chegar a valores significativos.

A 88i ativa a cobertura AP em 48h via API, sem burocracia. {case_referencia}

Tem 5min essa semana para uma conversa rápida?""",

    "whatsapp_dados": """Oi {decisor_nome},

Dado interessante do setor: {dado_mercado}

A {empresa} se beneficia diretamente disso. Posso te mostrar como em uma call de 15min?""",

    "whatsapp_urgencia": """Oi {decisor_nome},

A fiscalização da Lei 14.297 tem se intensificado. Empresas como {concorrente_referencia} já se adequaram.

Seria uma pena a {empresa} ser pega de surpresa. A ativação é em 48h.

Último follow-up antes de fechar esta semana — posso reservar 15min?""",

    "whatsapp_breakup": """Oi {decisor_nome},

Tentei contato algumas vezes sobre adequação à Lei 14.297 para a {empresa}. Entendo que o timing pode não ser ideal.

Vou parar de insistir, mas fico disponível quando fizer sentido. Só responder esta mensagem.

Abs, {remetente} — 88i Seguradora Digital""",

    # ─── LinkedIn ───
    "linkedin_connect": """Olá {decisor_nome}! Acompanho o trabalho da {empresa} no setor de delivery. Na 88i Seguradora, atendemos empresas do segmento com soluções de AP compulsório para entregadores. Aceita conectar?""",

    "linkedin_artigo": """Oi {decisor_nome}, compartilho um insight que pode ser relevante para a {empresa}: {insight_personalizado}. Se quiser, posso detalhar como empresas similares estão se adequando.""",

    # ─── Email ───
    "email_case": """Assunto: Como empresas de delivery estão evitando R${valor_multa} em multas — Lei 14.297

Olá {decisor_nome},

A Lei 14.297/2022 exige que plataformas com entregadores ofereçam seguro AP ativo durante o período de trabalho. A multa é de R$1.000 por evento de alocação sem cobertura.

{case_detalhado}

A 88i ativa a cobertura em 48h via API. Sem burocracia. Sem papel.

{hook_produto}

Posso explicar em 15 minutos como funciona para a {empresa}?

Abs,
{remetente}
88i Seguradora Digital""",
}


class OutreachEngine:

    def __init__(self, claude: ClaudeClient):
        self.claude = claude
        self.client = httpx.Client(timeout=30.0)

    # ───────────────────────────────────────────
    # PERSONALIZAÇÃO COM IA
    # ───────────────────────────────────────────

    def personalize_message(self, template_key: str, lead: dict, remetente: str = "Fernanda") -> str:
        """Personaliza template com dados do lead + IA."""
        template = TEMPLATES.get(template_key, "")
        if not template:
            return ""
        intelligence = self._extract_intelligence(lead)

        # Substituições diretas (regras fixas — Regra #3)
        msg = template.format(
            decisor_nome=(lead.get("decisor_nome") or lead.get("nome") or "").split()[0],
            empresa=lead.get("nome", ""),
            remetente=remetente,
            entregadores_est=lead.get("ai_entregadores_est") or "diversos",
            hook_personalizado=intelligence.get("abertura") or self._get_hook(lead),
            case_referencia=self._get_case_ref(lead),
            dado_mercado=self._get_market_data(lead),
            concorrente_referencia=self._get_competitor_ref(lead),
            insight_personalizado="",
            valor_multa=self._estimate_fine(lead),
            case_detalhado=self._get_case_detail(lead),
            hook_produto=self._get_product_hook(lead),
        )

        # 1) Mensagem 100% dinâmica (se intelligence robusta)
        dynamic_msg = self._dynamic_message_from_intelligence(lead, intelligence, remetente)
        if dynamic_msg:
            return dynamic_msg.strip()

        # 2) Template + preenchimento com intelligence
        msg = self._inject_intelligence(msg, intelligence)

        # Se a mensagem precisa de personalização adicional, usa Claude
        if "{" in msg or len(msg) < 50:
            msg = self.claude.personalize_message(template, lead)

        return msg.strip()

    # ───────────────────────────────────────────
    # EVOLUTION API — ENVIO WHATSAPP
    # ───────────────────────────────────────────

    def send_whatsapp(self, phone: str, message: str) -> dict:
        """Envia mensagem via Evolution API. Se não configurada, simula o envio."""
        phone_clean = phone.replace("+", "").replace("-", "").replace(" ", "").replace("(", "").replace(")", "")
        if not phone_clean.startswith("55"):
            phone_clean = "55" + phone_clean

        # ── MODO SIMULADO — Evolution API não configurada ──
        if not all([EVOLUTION_API_URL, EVOLUTION_API_KEY, EVOLUTION_INSTANCE]):
            print(f"\n  📱 [SIMULADO] WhatsApp para: +{phone_clean}")
            print(f"  {'─' * 50}")
            for line in message.strip().splitlines():
                print(f"  {line}")
            print(f"  {'─' * 50}\n")
            return {"status": "simulated", "phone": phone_clean}

        # ── PRODUÇÃO — Evolution API configurada ──
        url = f"{EVOLUTION_API_URL}/message/sendText/{EVOLUTION_INSTANCE}"

        try:
            resp = self.client.post(
                url,
                json={
                    "number": phone_clean,
                    "text": message,
                },
                headers={
                    "apikey": EVOLUTION_API_KEY,
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            return {"status": "sent", "response": resp.json()}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    # ───────────────────────────────────────────
    # EMAIL — via SMTP ou API
    # ───────────────────────────────────────────

    def send_email(self, to_email: str, subject: str, body: str) -> dict:
        """Placeholder para envio de email — integrar com SMTP ou SendGrid."""
        # TODO: Integrar com SendGrid ou SMTP
        print(f"  📧 [SIMULADO] Email para: {to_email}")
        print(f"     Assunto: {subject}")
        return {"status": "simulated", "to": to_email, "subject": subject}

    # ───────────────────────────────────────────
    # LINKEDIN — Instruções para ação manual
    # ───────────────────────────────────────────

    def prepare_linkedin(self, lead: dict, template_key: str) -> dict:
        """Gera mensagem LinkedIn pronta para envio manual."""
        msg = self.personalize_message(template_key, lead)
        linkedin_url = lead.get("decisor_linkedin") or ""
        return {
            "canal": "linkedin",
            "url": linkedin_url,
            "mensagem": msg,
            "instrucao": f"Enviar via LinkedIn para {lead.get('decisor_nome', '?')}",
        }

    # ───────────────────────────────────────────
    # EXECUTAR STEP DA CADÊNCIA
    # ───────────────────────────────────────────

    def execute_outreach_step(self, lead: dict, step, remetente: str = "Fernanda") -> dict:
        """Executa um step da cadência SDR."""
        msg = self.personalize_message(step.template_key, lead, remetente)

        result = {
            "empresa_id": lead.get("empresa_id", lead.get("nome")),
            "dia": step.dia,
            "canal": step.canal,
            "tipo": step.tipo,
            "mensagem": msg,
        }

        if step.canal == "whatsapp":
            phone = lead.get("decisor_telefone") or lead.get("telefone", "")
            if phone:
                send_result = self.send_whatsapp(phone, msg)
                result["status"] = send_result["status"]  # "sent" | "simulated" | "error"
            else:
                result["status"] = "no_phone"

        elif step.canal == "email":
            email = lead.get("decisor_email") or ""
            if email:
                subject = msg.split("\n")[0].replace("Assunto: ", "") if "Assunto:" in msg else f"88i — {lead.get('nome', '')}"
                send_result = self.send_email(email, subject, msg)
                result["status"] = send_result["status"]
            else:
                result["status"] = "no_email"

        elif step.canal == "linkedin":
            linkedin_data = self.prepare_linkedin(lead, step.template_key)
            result["status"] = "prepared"
            result["linkedin"] = linkedin_data

        return result

    # ───────────────────────────────────────────
    # HELPERS
    # ───────────────────────────────────────────

    def _get_hook(self, lead: dict) -> str:
        icp = lead.get("icp_tipo", "ICP1")
        if icp == "ICP1":
            return PRODUTOS_88I["ap_compulsorio"]["hook"]
        if icp == "ICP2":
            return PRODUTOS_88I["mercadoria_last_mile"]["hook"]
        return PRODUTOS_88I["perda_renda"]["hook"]

    def _get_product_hook(self, lead: dict) -> str:
        icp = lead.get("icp_tipo", "ICP1")
        if icp == "ICP1":
            return f"Cobertura completa: {', '.join(PRODUTOS_88I['ap_compulsorio']['coberturas'][:3])}."
        return PRODUTOS_88I["mercadoria_last_mile"]["hook"]

    def _get_case_ref(self, lead: dict) -> str:
        segmento = lead.get("ai_segmento") or lead.get("segmento") or ""
        if "food" in segmento or "delivery" in segmento:
            return "Empresas de food delivery já estão com cobertura ativa pela 88i."
        return "Diversas operadoras do setor já ativaram a cobertura AP conosco."

    def _get_case_detail(self, lead: dict) -> str:
        return "A 88i já cobre mais de 150k trabalhadores de app com AP compulsório. Ativação em 48h, sem papel."

    def _get_market_data(self, lead: dict) -> str:
        return "67% das plataformas de delivery no Brasil ainda não possuem cobertura AP adequada à Lei 14.297."

    def _get_competitor_ref(self, lead: dict) -> str:
        return "grandes plataformas do setor"

    def _estimate_fine(self, lead: dict) -> str:
        est = lead.get("ai_entregadores_est") or lead.get("entregadores_est") or ""
        try:
            num = int(str(est).replace("+", "").replace("k", "000").split("-")[-1])
            fine = num * 1000
            if fine > 1_000_000:
                return f"{fine/1_000_000:.1f}M"
            return f"{fine:,}".replace(",", ".")
        except (ValueError, TypeError):
            return "milhares"

    def close(self):
        self.client.close()

    def _extract_intelligence(self, lead: dict) -> dict:
        """Busca closing_intelligence em múltiplos formatos de payload."""
        if isinstance(lead.get("closing_intelligence"), dict):
            return lead["closing_intelligence"]
        if isinstance(lead.get("lead_intelligence"), dict):
            nested = lead["lead_intelligence"].get("closing_intelligence")
            if isinstance(nested, dict):
                return nested
        if isinstance(lead.get("analise_ia"), dict):
            nested = lead["analise_ia"].get("closing_intelligence")
            if isinstance(nested, dict):
                return nested
        return {}

    def _dynamic_message_from_intelligence(self, lead: dict, intelligence: dict, remetente: str) -> str:
        abertura = intelligence.get("abertura")
        pitch = intelligence.get("pitch_1_frase")
        timing = intelligence.get("timing")
        pontos = intelligence.get("pontos_conexao")
        evitar = intelligence.get("o_que_evitar")
        if not (abertura and pitch):
            return ""
        empresa = lead.get("nome", "sua empresa")
        decisor = (lead.get("decisor_nome") or lead.get("nome") or "tudo bem").split()[0]
        return (
            f"Olá {decisor}, tudo bem?\n\n"
            f"{abertura}\n"
            f"{pitch}\n"
            f"{pontos or ''}\n\n"
            f"Timing sugerido: {timing or 'curto e objetivo'}.\n"
            f"Evitar: {evitar or 'mensagem genérica'}.\n\n"
            f"Faz sentido conversarmos sobre como aplicar isso na {empresa}?\n\n"
            f"Abs, {remetente} — 88i"
        )

    def _inject_intelligence(self, msg: str, intelligence: dict) -> str:
        extras = []
        if intelligence.get("pitch_1_frase"):
            extras.append(intelligence["pitch_1_frase"])
        if intelligence.get("timing"):
            extras.append(f"Timing: {intelligence['timing']}")
        if intelligence.get("pontos_conexao"):
            extras.append(f"Ponto de conexão: {intelligence['pontos_conexao']}")
        if not extras:
            return msg
        return f"{msg}\n\n" + "\n".join(extras)
