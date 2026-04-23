"""
Daily SDR Digest — compila e envia resumo diário via WhatsApp para o SDR.

Formato da mensagem:
  Cabeçalho com data e resumo de pipeline
  → Top HOT leads com score, deal value e próximo passo da cadência
  → Leads WARM que vencem prazo de follow-up hoje
  → Dica de abordagem para o lead #1

Disparado via:
  python orchestrator.py --mode digest --sdr-phone 5511999999999
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from config.settings import (
    CADENCIA_SDR,
    EVOLUTION_API_KEY,
    EVOLUTION_API_URL,
    EVOLUTION_INSTANCE,
)
from modules.logger import get_logger

log = get_logger("sdr.digest")

# Número máximo de leads no digest
MAX_HOT = 5
MAX_WARM_DUE = 3


class DailyDigest:
    """
    Gera e envia o digest diário para o SDR.

    Pode ser alimentado:
    - Por uma lista de leads em memória (pipeline recente)
    - Por um SupabaseClient (produção)
    """

    def __init__(self, supabase=None):
        self.supabase = supabase

    # ───────────────────────────────────────────
    # GERAÇÃO DO DIGEST
    # ───────────────────────────────────────────

    def build(
        self,
        leads: Optional[list[dict]] = None,
        remetente: str = "Fernanda",
    ) -> str:
        """
        Constrói a mensagem de digest.

        Args:
            leads: lista de leads já processados (score, status, deal_value_est)
            remetente: nome do SDR para personalizar a mensagem

        Returns:
            Texto formatado para WhatsApp.
        """
        if leads is None:
            leads = self._load_from_supabase()

        today = date.today()
        hot = [l for l in leads if l.get("status") == "HOT"]
        warm = [l for l in leads if l.get("status") == "WARM"]
        cold = [l for l in leads if l.get("status") == "COLD"]
        total_arr = sum(l.get("deal_value_est", 0) for l in hot + warm)

        lines: list[str] = []

        # ── Cabeçalho ──────────────────────────────────────────────────
        lines.append(f"📋 *DIGEST SDR 88i — {today.strftime('%d/%m/%Y')}*")
        lines.append(f"Bom dia, {remetente}! Aqui está sua agenda de hoje.")
        lines.append("")
        lines.append(
            f"📊 Pipeline: 🔥 {len(hot)} HOT  ⚡ {len(warm)} WARM  ❄️ {len(cold)} COLD"
        )
        if total_arr:
            lines.append(f"💰 ARR potencial (HOT+WARM): R${total_arr:,.0f}")
        lines.append("─" * 35)

        # ── Top HOT leads ──────────────────────────────────────────────
        hot_sorted = sorted(hot, key=lambda x: x.get("score", 0), reverse=True)
        if hot_sorted:
            lines.append(f"\n🔥 *TOP {min(MAX_HOT, len(hot_sorted))} HOT LEADS — ABORDAR HOJE*")
            for i, lead in enumerate(hot_sorted[:MAX_HOT], 1):
                lines.append(self._format_hot_lead(i, lead))
        else:
            lines.append("\n⚠️ Nenhum lead HOT no momento.")

        # ── WARM com follow-up vencendo ────────────────────────────────
        warm_due = self._due_today(warm)
        if warm_due:
            lines.append(f"\n⚡ *WARM COM FOLLOW-UP HOJE*")
            for lead in warm_due[:MAX_WARM_DUE]:
                lines.append(self._format_warm_lead(lead))

        # ── Dica de abordagem para o lead #1 ──────────────────────────
        if hot_sorted:
            top = hot_sorted[0]
            dica = self._generate_approach_tip(top)
            if dica:
                lines.append(f"\n💡 *DICA PARA {top.get('nome', '?').upper()}*")
                lines.append(dica)

        # ── Próximos passos da cadência ────────────────────────────────
        lines.append(f"\n📅 *CADÊNCIA DO DIA*")
        step_hoje = self._cadencia_step_today()
        if step_hoje:
            lines.append(f"  Dia {step_hoje.dia}: {step_hoje.descricao} ({step_hoje.canal.upper()})")
        else:
            lines.append("  Revisar cadência manualmente.")

        lines.append("")
        lines.append("_88i Seguradora Digital · SDR Agent OlgaAI_")

        return "\n".join(lines)

    # ───────────────────────────────────────────
    # ENVIO VIA WHATSAPP
    # ───────────────────────────────────────────

    def send(
        self,
        phone: str,
        leads: Optional[list[dict]] = None,
        remetente: str = "Fernanda",
    ) -> dict:
        """
        Gera e envia o digest para o WhatsApp do SDR.

        Args:
            phone: número do SDR (ex: 5511999999999)
            leads: lista de leads processados
            remetente: nome do SDR
        """
        message = self.build(leads=leads, remetente=remetente)

        # Modo simulado se Evolution API não configurada
        if not all([EVOLUTION_API_URL, EVOLUTION_API_KEY, EVOLUTION_INSTANCE]):
            print("\n" + "=" * 50)
            print("📋 [SIMULADO] DAILY DIGEST SDR 88i")
            print("=" * 50)
            print(message)
            print("=" * 50)
            return {"status": "simulated", "chars": len(message)}

        # Envio real via Evolution API
        import httpx

        phone_clean = phone.replace("+", "").replace("-", "").replace(" ", "")
        if not phone_clean.startswith("55"):
            phone_clean = "55" + phone_clean

        url = f"{EVOLUTION_API_URL}/message/sendText/{EVOLUTION_INSTANCE}"
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.post(
                    url,
                    json={"number": phone_clean, "text": message},
                    headers={"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"},
                )
                resp.raise_for_status()
                log.info(f"Digest enviado para {phone_clean}")
                return {"status": "sent", "phone": phone_clean}
        except Exception as e:
            log.error(f"Erro enviando digest: {e}")
            return {"status": "error", "error": str(e)}

    # ───────────────────────────────────────────
    # FORMATADORES
    # ───────────────────────────────────────────

    def _format_hot_lead(self, rank: int, lead: dict) -> str:
        nome = lead.get("nome", "?")
        score = lead.get("score", 0)
        decisor = lead.get("decisor_nome", "")
        cargo = lead.get("decisor_cargo", "")
        arr = lead.get("deal_value_est", 0)
        sinal = lead.get("sinal_dor", "")
        telefone = lead.get("decisor_telefone") or lead.get("telefone") or ""
        linkedin = lead.get("decisor_linkedin") or ""

        parts = [f"\n*{rank}. {nome}* — {score} pts"]
        if decisor:
            parts.append(f"   👤 {decisor}" + (f" ({cargo})" if cargo else ""))
        if arr:
            parts.append(f"   💰 ARR est: R${arr:,.0f}/ano")
        if sinal and sinal != "nenhum":
            sinal_emoji = {"sinistro_midia": "🚨", "empresa_nova_sem_seguro": "⚠️", "alto_risco": "🔶"}.get(sinal, "📌")
            motivo = lead.get("sinal_dor_motivo", sinal)
            parts.append(f"   {sinal_emoji} {motivo}")
        if telefone:
            parts.append(f"   📱 {telefone}")
        if linkedin:
            parts.append(f"   🔗 {linkedin}")

        return "\n".join(parts)

    def _format_warm_lead(self, lead: dict) -> str:
        nome = lead.get("nome", "?")
        score = lead.get("score", 0)
        decisor = lead.get("decisor_nome", "")
        return f"\n  ⚡ *{nome}* ({score} pts)" + (f" — {decisor}" if decisor else "")

    def _generate_approach_tip(self, lead: dict) -> str:
        """Gera dica de abordagem baseada nos dados do lead."""
        sinal = lead.get("sinal_dor", "nenhum")
        icp = lead.get("icp_tipo", "ICP1")
        entregadores = lead.get("ai_entregadores_est") or lead.get("entregadores_est") or ""
        segmento = lead.get("ai_segmento") or ""

        tip_parts: list[str] = []

        if sinal == "sinistro_midia":
            tip_parts.append("🚨 Empresa tem sinal de sinistro/ausência de seguro — aborde com urgência regulatória.")
        elif sinal == "empresa_nova_sem_seguro":
            tip_parts.append("⚠️ Empresa nova sem seguro — foco na facilidade de ativação em 48h e evitar multa.")
        elif sinal == "alto_risco":
            tip_parts.append("🔶 Operação de risco detectada — pergunte sobre a cobertura atual antes de pitchar.")

        if icp == "ICP1" and entregadores:
            tip_parts.append(f"📦 Mencione os {entregadores} entregadores e o risco de R$1k/evento sem cobertura.")

        if segmento and "food" in segmento.lower():
            tip_parts.append("🍔 Food delivery — conecte com cases de iFood-like que já fecharam com a 88i.")

        decisor_cargo = lead.get("decisor_cargo") or ""
        if "ceo" in decisor_cargo.lower() or "fundador" in decisor_cargo.lower():
            tip_parts.append("🎯 Fala com o fundador/CEO — use linguagem de risco legal, não produto.")
        elif "operac" in decisor_cargo.lower() or "logistic" in decisor_cargo.lower():
            tip_parts.append("🎯 Decisor de operações — foco em API, integração simples e sem papel.")

        return "\n".join(f"  {t}" for t in tip_parts) if tip_parts else ""

    # ───────────────────────────────────────────
    # HELPERS
    # ───────────────────────────────────────────

    def _due_today(self, leads: list[dict]) -> list[dict]:
        """
        Retorna leads WARM cujo próximo contato vence hoje.
        Usa `ultimo_contato` + intervalo da cadência (3 dias default para WARM).
        """
        today = date.today()
        due = []
        for lead in leads:
            ultimo = lead.get("ultimo_contato") or lead.get("status_changed_at") or ""
            if not ultimo:
                continue
            try:
                dt = datetime.fromisoformat(str(ultimo)[:19]).date()
                # WARM follow-up a cada 3-4 dias
                if (today - dt).days >= 3:
                    due.append(lead)
            except (ValueError, TypeError):
                pass
        return sorted(due, key=lambda x: x.get("score", 0), reverse=True)

    def _cadencia_step_today(self):
        """Retorna o step da cadência correspondente ao dia atual (1-14)."""
        today_num = (date.today().toordinal() % 14) + 1  # ciclo de 14 dias
        for step in CADENCIA_SDR:
            if step.dia == today_num:
                return step
        return None

    def _load_from_supabase(self) -> list[dict]:
        """Carrega leads HOT e WARM do Supabase para o digest."""
        if not self.supabase:
            log.warning("Supabase não configurado — digest vazio")
            return []
        try:
            hot = self.supabase.get_leads(status="HOT", limit=20) or []
            warm = self.supabase.get_leads(status="WARM", limit=20) or []
            return hot + warm
        except Exception as e:
            log.error(f"Erro carregando leads do Supabase: {e}")
            return []
