"""
SDR Agent 88i — Entry Point de Produção
OlgaAI · 100% Python

Roda:
  - FastAPI server (webhook listener + API de controle)
  - APScheduler (cadência automática + discovery semanal)
  - CLI para execuções manuais

Uso:
  # Produção (server + scheduler)
  python main.py serve

  # CLI manual
  python main.py run --mode full --dry-run
  python main.py run --mode outreach --status HOT --dia 1
  python main.py trigger cadence_runner
"""

import argparse
import asyncio
import os
import sys
from contextlib import asynccontextmanager

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from config.settings import CADENCIA_SDR, HUNT_DEFAULT_SOURCES, HUNT_LINKEDIN_MAX_RESULTS
from modules.claude_client import ClaudeClient
from modules.supabase_client import SupabaseClient
from modules.discovery import LeadDiscovery
from modules.hunter import LeadHunter
from modules.async_enrichment import AsyncEnrichment
from modules.scoring import ScoringEngine
from modules.outreach import OutreachEngine
from modules.email_client import EmailClient
from modules.state_machine import LeadStateMachine
from modules.scheduler import SDRScheduler
from modules.webhook_server import router as webhook_router, init_webhook
from modules.intelligence_engine import IntelligenceEngine
from modules.logger import get_logger

log = get_logger("sdr.main")


# ═══════════════════════════════════════════════════════════════
# PRODUCTION ORCHESTRATOR (atualizado com async + email + state)
# ═══════════════════════════════════════════════════════════════

class ProductionOrchestrator:
    """
    Versão de produção do orquestrador com:
    - Async enrichment (5x mais rápido)
    - Email real (SMTP)
    - State machine (transições protegidas)
    - Logging profissional
    - Cache de enrichment
    """

    def __init__(self, dry_run: bool = False, use_lusha: bool = True):
        self.dry_run = dry_run

        # Core modules
        self.claude = ClaudeClient()
        self.supabase = SupabaseClient()
        self.discovery = LeadDiscovery()
        self.hunter = LeadHunter()
        self.enrichment = AsyncEnrichment(self.claude, self.supabase)
        self.scoring = ScoringEngine(self.claude)
        self.outreach = OutreachEngine(self.claude)
        self.email = EmailClient()
        self.state_machine = LeadStateMachine(self.supabase)
        self.intelligence = IntelligenceEngine(self.claude)
        self.use_lusha = use_lusha

        log.info("ProductionOrchestrator inicializado", extra={"dry_run": dry_run})

    def run_full_pipeline(
        self,
        icps: list[str] = None,
        cidades: list[str] = None,
        max_per_query: int = 30,
    ) -> list[dict]:
        """Pipeline completo com async enrichment."""
        import time
        start = time.time()

        log.info("Pipeline FULL iniciado", extra={"icps": icps, "cidades": cidades})

        # 1. Discovery (sync — depende do Apify polling)
        leads = self.discovery.run_full_discovery(icps, cidades, max_per_query)
        if not leads:
            log.warning("Nenhum lead encontrado")
            return []
        log.info(f"Discovery: {len(leads)} leads")

        # 2. Async enrichment (5x mais rápido)
        enriched = self.enrichment.enrich_batch_sync(leads, use_lusha=self.use_lusha)
        log.info(f"Enrichment: {len(enriched)} leads processados")

        # 3. Scoring — já seta lead["status"] = HOT/WARM/COLD/excluded
        scored = self.scoring.score_batch(enriched)

        # 4. Persist
        if not self.dry_run:
            self._persist_leads(scored)

        # 5. Report
        elapsed = time.time() - start
        self._log_report(scored, elapsed)

        return scored

    def run_hunt_pipeline(
        self,
        sources: list[str] = None,
        icps: list[str] = None,
        cidades: list[str] = None,
        max_per_query: int = 30,
    ) -> list[dict]:
        """
        Pipeline de CAÇA de leads (substitui leitura do Supabase como fonte):

          1. LeadHunter: Apify (Google Maps + LinkedIn) → empresas
          2. Lusha: decisor por domain (dentro do hunter)
          3. AsyncEnrichment: Claude AI → segmento, porte, entregadores
          4. ScoringEngine → HOT / WARM / COLD
          5. IntelligenceEngine → sdr_lead_intelligence
          6. Supabase → persist

        Os leads vão para o Supabase e ficam prontos para o fluxo de outreach.
        """
        import time
        start = time.time()

        sources = sources or HUNT_DEFAULT_SOURCES
        log.info(
            "Pipeline HUNT iniciado",
            extra={"sources": sources, "icps": icps, "cidades": cidades},
        )

        # 1. Caçar via Apify + Lusha (hunter já deduplicou e mapeou decisores)
        raw_leads = self.hunter.run_hunt(
            sources=sources,
            icps=icps,
            cidades=cidades,
            max_per_query=max_per_query,
            use_lusha=self.use_lusha,
        )
        if not raw_leads:
            log.warning("Hunt: nenhum lead encontrado")
            return []
        log.info(f"Hunt: {len(raw_leads)} leads capturados")

        # 2. Enriquecimento Claude AI (segmento, porte, entregadores)
        enriched = self.enrichment.enrich_batch_sync(raw_leads, use_lusha=False)
        log.info(f"Enrichment: {len(enriched)} leads processados")

        # 3. Scoring — já seta lead["status"] = HOT/WARM/COLD/excluded
        scored = self.scoring.score_batch(enriched)

        # 4. Persist
        if not self.dry_run:
            self._persist_leads(scored)

        # 5. Report
        elapsed = time.time() - start
        self._log_report(scored, elapsed)

        return scored

    def run_outreach_step(
        self,
        status_filter: str = "HOT",
        step_dia: int = 1,
        limit: int = 10,
    ) -> list[dict]:
        """Executa um step da cadência."""
        leads = self.supabase.get_leads(status=status_filter, limit=limit)
        step = next((s for s in CADENCIA_SDR if s.dia == step_dia), None)

        if not step:
            log.error(f"Step dia {step_dia} não encontrado")
            return []

        log.info(f"Outreach dia {step.dia}: {step.descricao} | {len(leads)} leads")

        results = []
        for lead in leads:
            if self.dry_run:
                msg = self.outreach.personalize_message(step.template_key, lead)
                log.info(f"[DRY RUN] {lead.get('nome')}: {msg[:80]}...")
                results.append({"empresa": lead.get("nome"), "status": "dry_run"})
                continue

            # Enviar via canal do step
            result = self.outreach.execute_outreach_step(lead, step)

            # Se email, usar email client real
            if step.canal == "email" and lead.get("decisor_email"):
                msg = self.outreach.personalize_message(step.template_key, lead)
                lines = msg.split("\n")
                subject = lines[0].replace("Assunto: ", "") if lines[0].startswith("Assunto:") else f"88i — {lead.get('nome')}"
                email_result = self.email.send(lead["decisor_email"], subject, msg)
                result["email_status"] = email_result["status"]

            # Log
            self.supabase.log_outreach(
                lead.get("empresa_id", lead.get("nome", "")),
                {
                    "canal": step.canal,
                    "tipo": step.tipo,
                    "mensagem": result.get("mensagem", ""),
                    "status": result.get("status", "unknown"),
                },
            )

            # State transition
            if lead.get("status") in ("HOT", "WARM", "COLD"):
                try:
                    self.state_machine.transition(lead, "contacted", f"Cadência dia {step.dia}")
                except ValueError:
                    pass

            results.append(result)

        return results

    def _persist_leads(self, leads: list[dict]):
        saved = 0
        for lead in leads:
            if lead.get("status") == "excluded":
                continue
            try:
                clean = self._sanitize_lead(lead)
                self.supabase.upsert_lead(clean)
                self._persist_intelligence_snapshot(lead)
                saved += 1
            except Exception as e:
                log.error(f"Erro salvando {lead.get('nome')}: {e}")
        log.info(f"{saved}/{len(leads)} leads salvos no Supabase")

    # Colunas válidas no Supabase (companies_88i_pipeline)
    _VALID_LEAD_COLS = {
        "empresa_id", "nome", "site", "cnpj", "segmento", "icp_tipo",
        "status", "tier", "porte", "cidade", "uf",
        "score", "score_breakdown", "score_icp",
        "decisor_nome", "decisor_cargo", "decisor_email",
        "decisor_telefone", "decisor_linkedin", "telefone",
        "seguro_atual", "seguradora_parceira", "gap_oportunidade",
        "entregadores_est",
        "ai_segmento", "ai_tem_entregadores", "ai_entregadores_est",
        "ai_porte", "ai_plataforma_digital", "ai_seguro_detectado",
        "ai_formato_email", "ai_risco_exclusao", "ai_confianca",
        "enrichment_complete", "exclusion_reason",
        "source", "place_id", "produto_88i", "obs_estrategica", "proxima_acao",
    }

    def _sanitize_lead(self, lead: dict) -> dict:
        """
        Gera empresa_id se ausente e filtra apenas as colunas válidas da tabela
        companies_88i_pipeline, evitando 400 por campos desconhecidos.
        """
        import hashlib, re as _re

        # Garante empresa_id único e estável (slug do nome)
        if not lead.get("empresa_id"):
            raw = lead.get("nome") or lead.get("place_id") or ""
            slug = _re.sub(r"[^a-z0-9]", "", raw.lower())[:60]
            suffix = hashlib.md5(raw.encode()).hexdigest()[:8]
            lead = {**lead, "empresa_id": f"{slug}_{suffix}" if slug else suffix}

        return {k: v for k, v in lead.items() if k in self._VALID_LEAD_COLS}

    def _persist_intelligence_snapshot(self, lead: dict):
        empresa_id = lead.get("empresa_id", lead.get("nome", ""))
        if not empresa_id:
            return
        report = None
        try:
            report = self.intelligence.build_full_intelligence_report(lead)
            if report:
                log.info(
                    "Intelligence completa persistida",
                    extra={
                        "empresa_id": empresa_id,
                        "source": report.get("source", "unknown"),
                    },
                )
        except Exception as exc:
            log.warning(
                "Falha na intelligence completa; acionando fallback parcial",
                extra={"empresa_id": empresa_id, "error": str(exc)},
            )

        if not report:
            icp = lead.get("icp_tipo", "")
            score_result = self.intelligence.l6_score(
                {
                    "icp": icp,
                    "entregadores": self._parse_int(lead.get("ai_entregadores_est") or lead.get("entregadores_est")),
                    "tech": bool(lead.get("ai_plataforma_digital")),
                    "porte_str": lead.get("ai_porte") or lead.get("porte") or "Pequeno",
                    "receita": lead.get("receita_lusha") or "",
                    "n_func": self._parse_int(lead.get("num_funcionarios_lusha")),
                    "decisor": bool(lead.get("decisor_nome") or lead.get("decisor_cargo")),
                    "cargo": lead.get("decisor_cargo") or "",
                    "has_linkedin": bool(lead.get("decisor_linkedin")),
                    "has_api": False,
                    "tms": 0,
                    "reuniao_citada": any(c in (lead.get("nome", "").lower()) for c in ["pick and go", "gaudium", "machine", "intelipost"]),
                    "seguro": str(lead.get("ai_seguro_detectado", "")).lower() in ("sim", "ativo"),
                }
            )
            dv = self.intelligence.calc_deal_value(
                icp,
                self._parse_int(lead.get("ai_entregadores_est") or lead.get("entregadores_est")),
                0,
                0,
            )
            report = self.intelligence.build_report(lead, score_result, dv, closing_intelligence=None, source="pipeline_snapshot")
            log.info(
                "Intelligence parcial persistida via fallback",
                extra={"empresa_id": empresa_id, "source": "pipeline_snapshot"},
            )
        self.supabase.upsert_lead_intelligence(empresa_id, report)

    @staticmethod
    def _parse_int(raw) -> int:
        import re
        m = re.search(r"(\\d[\\d.,]*)", str(raw or ""))
        if not m:
            return 0
        return int(m.group(1).replace(".", "").replace(",", ""))

    def _log_report(self, leads: list[dict], elapsed: float):
        hot = sum(1 for l in leads if l.get("status") == "HOT")
        warm = sum(1 for l in leads if l.get("status") == "WARM")
        cold = sum(1 for l in leads if l.get("status") == "COLD")
        excluded = sum(1 for l in leads if l.get("status") == "excluded")
        cost = self.claude.cost_report()

        log.info(
            f"Pipeline completo em {elapsed:.1f}s",
            extra={
                "total": len(leads),
                "hot": hot, "warm": warm, "cold": cold, "excluded": excluded,
                "claude_calls": cost["total_calls"],
                "claude_cost_usd": cost["total_cost_usd"],
                "cost_per_lead": round(cost["total_cost_usd"] / max(len(leads), 1), 4),
                "elapsed_seconds": round(elapsed, 1),
            },
        )

    def close(self):
        self.claude.close()
        self.discovery.close()
        self.hunter.close()
        self.outreach.close()
        self.intelligence.close()
        self.supabase.close()


# ═══════════════════════════════════════════════════════════════
# FASTAPI APP
# ═══════════════════════════════════════════════════════════════

orchestrator: ProductionOrchestrator = None
scheduler: SDRScheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup e shutdown do app."""
    global orchestrator, scheduler

    log.info("═══ SDR AGENT 88i — STARTING ═══")

    # Init orchestrator
    orchestrator = ProductionOrchestrator(dry_run=False)

    # Init webhook
    init_webhook(orchestrator.supabase)

    # Init scheduler
    scheduler = SDRScheduler(orchestrator)
    scheduler.start()

    log.info("═══ SDR AGENT 88i — READY ═══")

    yield

    # Shutdown
    log.info("═══ SDR AGENT 88i — SHUTTING DOWN ═══")
    scheduler.stop()
    orchestrator.close()


app = FastAPI(
    title="SDR Agent 88i — OlgaAI",
    description="Agente SDR autônomo para prospecção de seguros Last Mile Delivery",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Incluir rotas do webhook
app.include_router(webhook_router)


# ─── API de Controle ───

@app.get("/")
async def root():
    return {
        "service": "SDR Agent 88i",
        "version": "1.0.0",
        "powered_by": "OlgaAI",
        "endpoints": {
            "webhooks": "/webhooks/zapi/receive",
            "health": "/webhooks/health",
            "trigger": "/api/trigger/{job_id}",
            "pipeline": "/api/pipeline/status",
        },
    }


@app.post("/api/trigger/{job_id}")
async def trigger_job(job_id: str):
    """Trigger manual de um job do scheduler."""
    if scheduler:
        scheduler.trigger_now(job_id)
        return {"status": "triggered", "job": job_id}
    return {"status": "error", "reason": "scheduler not running"}


@app.get("/api/pipeline/status")
async def pipeline_status():
    """Status atual do pipeline."""
    if not orchestrator:
        return {"status": "not_ready"}

    try:
        hot = orchestrator.supabase.get_leads(status="HOT", limit=100)
        warm = orchestrator.supabase.get_leads(status="WARM", limit=100)
        contacted = orchestrator.supabase.get_leads(status="contacted", limit=100)
        cost = orchestrator.claude.cost_report()

        return {
            "status": "running",
            "pipeline": {
                "hot_leads": len(hot),
                "warm_leads": len(warm),
                "contacted": len(contacted),
            },
            "claude_usage": cost,
            "scheduler_jobs": len(scheduler.scheduler.get_jobs()) if scheduler else 0,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post("/api/outreach/execute")
async def execute_outreach(status: str = "HOT", dia: int = 1, limit: int = 5):
    """Executa outreach manualmente via API."""
    if orchestrator:
        results = orchestrator.run_outreach_step(status, dia, limit)
        return {"status": "ok", "executed": len(results)}
    return {"status": "error"}


@app.post("/api/hunt")
async def execute_hunt(
    sources: str = "google_maps,linkedin",
    icps: str = "ICP1,ICP2,ICP3",
    cidades: str = "",
    max_per_query: int = 30,
    dry_run: bool = False,
):
    """
    Caça leads via Apify (Google Maps + LinkedIn) + Lusha.

    Parâmetros:
      sources       : fontes separadas por vírgula (google_maps, linkedin)
      icps          : ICPs alvo (ICP1, ICP2, ICP3)
      cidades       : cidades alvo separadas por vírgula (padrão: config)
      max_per_query : máximo de resultados por query Apify
      dry_run       : se true, não salva no Supabase
    """
    if not orchestrator:
        return {"status": "error", "reason": "orchestrator not ready"}

    import asyncio

    def _run():
        orch = ProductionOrchestrator(dry_run=dry_run)
        try:
            return orch.run_hunt_pipeline(
                sources=sources.split(","),
                icps=icps.split(","),
                cidades=cidades.split(",") if cidades else None,
                max_per_query=max_per_query,
            )
        finally:
            orch.close()

    leads = await asyncio.to_thread(_run)

    hot = sum(1 for l in leads if l.get("status") == "HOT")
    warm = sum(1 for l in leads if l.get("status") == "WARM")
    cold = sum(1 for l in leads if l.get("status") == "COLD")

    return {
        "status": "ok",
        "total": len(leads),
        "hot": hot,
        "warm": warm,
        "cold": cold,
        "dry_run": dry_run,
    }


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def cli():
    parser = argparse.ArgumentParser(description="SDR Agent 88i — OlgaAI")
    subparsers = parser.add_subparsers(dest="command")

    # serve
    serve_parser = subparsers.add_parser("serve", help="Rodar server + scheduler")
    serve_parser.add_argument("--host", default="0.0.0.0")
    serve_parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))

    # run
    run_parser = subparsers.add_parser("run", help="Execução manual")
    run_parser.add_argument("--mode", choices=["full", "outreach", "score"], default="full")
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.add_argument("--no-lusha", action="store_true")
    run_parser.add_argument("--icps", default="ICP1,ICP2,ICP3")
    run_parser.add_argument("--cidades", default="")
    run_parser.add_argument("--status", default="HOT")
    run_parser.add_argument("--dia", type=int, default=1)
    run_parser.add_argument("--limit", type=int, default=10)

    # hunt
    hunt_parser = subparsers.add_parser(
        "hunt",
        help="Caçar leads via Apify (Google Maps + LinkedIn) + Lusha",
    )
    hunt_parser.add_argument(
        "--sources",
        default=",".join(HUNT_DEFAULT_SOURCES),
        help="Fontes separadas por vírgula: google_maps,linkedin",
    )
    hunt_parser.add_argument("--icps", default="ICP1,ICP2,ICP3")
    hunt_parser.add_argument("--cidades", default="")
    hunt_parser.add_argument("--limit", type=int, default=HUNT_LINKEDIN_MAX_RESULTS)
    hunt_parser.add_argument("--dry-run", action="store_true")
    hunt_parser.add_argument("--no-lusha", action="store_true")

    # trigger
    trigger_parser = subparsers.add_parser("trigger", help="Trigger job do scheduler")
    trigger_parser.add_argument("job_id")

    args = parser.parse_args()

    if args.command == "serve":
        log.info(f"Starting server on {args.host}:{args.port}")
        uvicorn.run(
            "main:app",
            host=args.host,
            port=args.port,
            reload=False,
            log_level="info",
        )

    elif args.command == "run":
        orch = ProductionOrchestrator(dry_run=args.dry_run, use_lusha=not args.no_lusha)
        try:
            if args.mode == "full":
                icps = args.icps.split(",")
                cidades = args.cidades.split(",") if args.cidades else None
                orch.run_full_pipeline(icps, cidades)
            elif args.mode == "outreach":
                orch.run_outreach_step(args.status, args.dia, args.limit)
        finally:
            orch.close()

    elif args.command == "hunt":
        orch = ProductionOrchestrator(
            dry_run=args.dry_run,
            use_lusha=not args.no_lusha,
        )
        try:
            results = orch.run_hunt_pipeline(
                sources=args.sources.split(","),
                icps=args.icps.split(","),
                cidades=args.cidades.split(",") if args.cidades else None,
                max_per_query=args.limit,
            )
            hot = sum(1 for l in results if l.get("status") == "HOT")
            warm = sum(1 for l in results if l.get("status") == "WARM")
            cold = sum(1 for l in results if l.get("status") == "COLD")
            print(
                f"\n{'='*50}\n"
                f"HUNT CONCLUÍDO: {len(results)} leads\n"
                f"  HOT:  {hot}\n"
                f"  WARM: {warm}\n"
                f"  COLD: {cold}\n"
                f"{'='*50}"
            )
        finally:
            orch.close()

    elif args.command == "trigger":
        # Para trigger, precisa do server rodando
        import httpx
        resp = httpx.post(f"http://localhost:8000/api/trigger/{args.job_id}")
        print(resp.json())

    else:
        parser.print_help()


if __name__ == "__main__":
    cli()
