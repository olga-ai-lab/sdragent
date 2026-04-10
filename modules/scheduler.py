"""
Scheduler — Executa a cadência SDR automaticamente.
Usa APScheduler para rodar jobs em background.
"""

from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config.settings import CADENCIA_SDR
from modules.logger import get_logger

log = get_logger("sdr.scheduler")


class SDRScheduler:
    """
    Scheduler que gerencia 3 jobs:

    1. cadence_runner  — Roda a cada dia útil 9h BRT, verifica quais leads
                         precisam receber o próximo step da cadência
    2. discovery_runner — Roda 1x/semana (segunda 6h), busca novos leads
    3. health_check    — Roda a cada 30min, verifica se tudo está ok
    """

    def __init__(self, orchestrator):
        self.orchestrator = orchestrator
        self.scheduler = AsyncIOScheduler(timezone="America/Sao_Paulo")
        self._setup_jobs()

    def _setup_jobs(self):
        # Job 1: Cadência diária — 9h BRT, seg-sex
        self.scheduler.add_job(
            self._run_cadence,
            CronTrigger(hour=9, minute=0, day_of_week="mon-fri"),
            id="cadence_runner",
            name="Cadência SDR diária",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        # Job 2: Discovery semanal — segunda 6h BRT
        self.scheduler.add_job(
            self._run_discovery,
            CronTrigger(hour=6, minute=0, day_of_week="mon"),
            id="discovery_runner",
            name="Discovery semanal",
            replace_existing=True,
            misfire_grace_time=7200,
        )

        # Job 3: Health check — a cada 30min
        self.scheduler.add_job(
            self._health_check,
            IntervalTrigger(minutes=30),
            id="health_check",
            name="Health check",
            replace_existing=True,
        )

        log.info("Scheduler configurado: cadence(9h seg-sex), discovery(seg 6h), health(30min)")

    async def _run_cadence(self):
        """
        Verifica quais leads precisam do próximo step da cadência.
        Lógica:
        - Busca leads com status 'contacted' ou 'HOT'/'WARM'
        - Para cada lead, verifica último outreach
        - Se dias desde último outreach == próximo step da cadência, executa
        """
        log.info("═══ CADENCE RUNNER INICIADO ═══")

        try:
            db = self.orchestrator.supabase
            if not db:
                log.error("Supabase não disponível")
                return

            # Buscar leads em outreach ativo
            leads_hot = db.get_leads(status="HOT", limit=50)
            leads_contacted = db.get_leads(status="contacted", limit=50)
            all_leads = leads_hot + leads_contacted

            if not all_leads:
                log.info("Nenhum lead para cadência hoje")
                return

            executed = 0
            for lead in all_leads:
                empresa_id = lead.get("empresa_id", lead.get("nome", ""))
                history = db.get_outreach_history(empresa_id)

                # Determinar próximo step
                next_step = self._get_next_step(history)
                if not next_step:
                    continue

                # Verificar se é dia de executar
                if self._should_execute_today(history, next_step):
                    log.info(f"Executando dia {next_step.dia} para {lead.get('nome')}")
                    result = self.orchestrator.outreach.execute_outreach_step(lead, next_step)

                    # Log no Supabase
                    db.log_outreach(empresa_id, {
                        "canal": next_step.canal,
                        "tipo": next_step.tipo,
                        "mensagem": result.get("mensagem", ""),
                        "status": result.get("status", "unknown"),
                    })

                    # Transition se primeiro contato
                    if lead.get("status") in ("HOT", "WARM"):
                        from modules.state_machine import LeadStateMachine
                        sm = LeadStateMachine(db)
                        try:
                            sm.transition(lead, "contacted", f"Cadência dia {next_step.dia}")
                        except ValueError:
                            pass

                    executed += 1

            log.info(f"═══ CADENCE RUNNER COMPLETO: {executed} ações ═══")

        except Exception as e:
            log.error(f"Erro no cadence runner: {e}", exc_info=True)

    async def _run_discovery(self):
        """Discovery semanal — busca novos leads via Apify."""
        log.info("═══ DISCOVERY RUNNER INICIADO ═══")
        try:
            results = self.orchestrator.run_full_pipeline(
                icps=["ICP1"],
                cidades=["São Paulo", "Rio de Janeiro"],
                max_per_query=20,
            )
            log.info(f"═══ DISCOVERY COMPLETO: {len(results)} leads ═══")
        except Exception as e:
            log.error(f"Erro no discovery: {e}", exc_info=True)

    async def _health_check(self):
        """Verifica saúde do sistema."""
        checks = {
            "supabase": False,
            "claude_api": False,
            "scheduler_jobs": len(self.scheduler.get_jobs()),
        }

        # Check Supabase
        try:
            if self.orchestrator.supabase:
                self.orchestrator.supabase.get_leads(limit=1)
                checks["supabase"] = True
        except Exception:
            pass

        # Check Claude API
        try:
            self.orchestrator.claude.call("classifier", "ping", max_tokens=5)
            checks["claude_api"] = True
        except Exception:
            pass

        status = "healthy" if all([checks["supabase"], checks["claude_api"]]) else "degraded"
        log.info(f"Health: {status}", extra=checks)

    def _get_next_step(self, history: list[dict]):
        """Determina o próximo step da cadência com base no histórico."""
        if not history:
            return CADENCIA_SDR[0]  # dia 1

        dias_executados = set()
        for entry in history:
            # Mapear tipo de outreach para dia da cadência
            for step in CADENCIA_SDR:
                if step.tipo == entry.get("tipo") and step.canal == entry.get("canal"):
                    dias_executados.add(step.dia)

        # Encontrar próximo step não executado
        for step in CADENCIA_SDR:
            if step.dia not in dias_executados:
                return step

        return None  # Cadência completa

    def _should_execute_today(self, history: list[dict], next_step) -> bool:
        """Verifica se o timing é correto para executar o step."""
        if not history:
            return True  # Primeiro contato — executa imediatamente

        # Pegar data do primeiro contato
        first_contact = min(
            (h.get("sent_at", "") for h in history if h.get("sent_at")),
            default=None,
        )
        if not first_contact:
            return True

        try:
            first_dt = datetime.fromisoformat(first_contact.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            days_elapsed = (now - first_dt).days

            # Executar se já passaram os dias necessários
            return days_elapsed >= next_step.dia - 1
        except (ValueError, TypeError):
            return True

    def start(self):
        """Inicia o scheduler."""
        self.scheduler.start()
        log.info("Scheduler ATIVO")

        jobs = self.scheduler.get_jobs()
        for job in jobs:
            log.info(f"  Job: {job.name} | Trigger: {job.trigger}")

    def stop(self):
        """Para o scheduler."""
        self.scheduler.shutdown(wait=False)
        log.info("Scheduler PARADO")

    def trigger_now(self, job_id: str):
        """Executa um job imediatamente (para testes)."""
        job = self.scheduler.get_job(job_id)
        if job:
            job.modify(next_run_time=datetime.now(timezone.utc))
            log.info(f"Job {job_id} triggered manually")
        else:
            log.error(f"Job {job_id} não encontrado")
