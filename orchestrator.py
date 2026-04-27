"""
SDR Agent Orchestrator — Pipeline completo de prospecção 88i.
OlgaAI · 100% Python · Zero dependência n8n

Pipeline:
  Discovery → Enrichment → Scoring → Outreach → Meeting Booking

Uso:
  python orchestrator.py --mode full
  python orchestrator.py --mode score-only --input leads.json
  python orchestrator.py --mode outreach --status HOT
"""

import json
import argparse
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config.settings import (
    CADENCIA_SDR, CIDADES_ALVO, ICP_DEFINITIONS,
    CLIENTES_ATIVOS_88I, DESCARTADOS,
    HUNT_DEFAULT_SOURCES, LINKEDIN_HUNT_FILTERS,
)
from modules.claude_client import ClaudeClient
from modules.supabase_client import SupabaseClient
from modules.discovery import LeadDiscovery
from modules.enrichment import LeadEnrichment
from modules.scoring import ScoringEngine
from modules.outreach import OutreachEngine
from modules.daily_digest import DailyDigest


class SDROrchestrator:
    """
    Orquestrador principal do SDR Agent.
    Coordena discovery → enrichment → scoring → outreach.
    """

    def __init__(self, dry_run: bool = False, use_supabase: bool = True, use_lusha: bool = True):
        self.dry_run = dry_run
        self.use_lusha = use_lusha

        # Initialize modules
        self.claude = ClaudeClient()
        self.discovery = LeadDiscovery()
        self.enrichment = LeadEnrichment(self.claude)
        self.scoring = ScoringEngine(self.claude)
        self.outreach = OutreachEngine(self.claude)

        self.supabase = SupabaseClient() if use_supabase else None
        self.digest = DailyDigest(supabase=self.supabase)
        self.results: list[dict] = []

    # ═══════════════════════════════════════════════════════════
    # PIPELINE COMPLETO
    # ═══════════════════════════════════════════════════════════

    def run_full_pipeline(
        self,
        icps: list[str] = None,
        cidades: list[str] = None,
        max_per_query: int = 30,
        sources: list[str] = None,
        linkedin_filters: list[dict] = None,
    ) -> list[dict]:
        """
        Pipeline completo:
        1a. Discovery — LinkedIn Company Search  (filtros de indústria/porte)
        1b. Discovery — Google Maps              (cobertura geográfica)
        2.  Enrichment (Lusha + Claude)
        3.  Scoring (regras + IA)
        4.  Persist (Supabase)
        5.  Report

        Args:
            sources: quais fontes ativar, ex: ["linkedin","google_maps"]
            linkedin_filters: lista de dicts de filtros LinkedIn; se None usa
                              LINKEDIN_HUNT_FILTERS de settings.py
        """
        start = time.time()
        sources = sources or HUNT_DEFAULT_SOURCES
        li_filters = linkedin_filters if linkedin_filters is not None else LINKEDIN_HUNT_FILTERS

        print("\n" + "=" * 70)
        print("🚀 SDR AGENT 88i — PIPELINE COMPLETO")
        print(f"   Modo:   {'DRY RUN' if self.dry_run else 'PRODUÇÃO'}")
        print(f"   Data:   {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
        print(f"   Fontes: {', '.join(sources)}")
        if "linkedin" in sources:
            print(f"   LinkedIn queries: {len(li_filters)}")
        print("=" * 70)

        # Step 1: Discovery
        print("\n📡 STEP 1/4 — DISCOVERY (LinkedIn → Google Maps)")
        leads = self.discovery.run_full_discovery(
            icps, cidades, max_per_query,
            sources=sources,
            linkedin_filters=li_filters,
        )

        if not leads:
            print("  ❌ Nenhum lead encontrado. Pipeline encerrado.")
            return []

        # Step 2: Enrichment
        print(f"\n🔄 STEP 2/4 — ENRICHMENT ({len(leads)} leads)")
        enriched = self.enrichment.enrich_batch(leads, use_lusha=self.use_lusha)

        # Step 3: Scoring
        print(f"\n🎯 STEP 3/4 — SCORING ({len(enriched)} leads)")
        scored = self.scoring.score_batch(enriched)

        # Step 4: Persist
        if self.supabase and not self.dry_run:
            print(f"\n💾 STEP 4/4 — PERSISTINDO NO SUPABASE")
            self._persist_leads(scored)
        else:
            print(f"\n💾 STEP 4/4 — SKIP (dry_run={self.dry_run})")

        # Report
        elapsed = time.time() - start
        self._print_report(scored, elapsed)

        self.results = scored
        return scored

    # ═══════════════════════════════════════════════════════════
    # MODO: SCORE-ONLY (importar leads existentes)
    # ═══════════════════════════════════════════════════════════

    def score_from_file(self, filepath: str) -> list[dict]:
        """Carrega leads de arquivo JSON/CSV e aplica scoring."""
        path = Path(filepath)
        print(f"\n📂 Carregando leads de: {path}")

        if path.suffix == ".json":
            with open(path) as f:
                leads = json.load(f)
        else:
            raise ValueError(f"Formato não suportado: {path.suffix}. Use .json")

        print(f"   {len(leads)} leads carregados")

        # Enrich + Score
        enriched = self.enrichment.enrich_batch(leads, use_lusha=self.use_lusha)
        scored = self.scoring.score_batch(enriched)

        self.results = scored
        return scored

    def score_from_pipeline(self) -> list[dict]:
        """Carrega leads existentes do Supabase e re-score."""
        if not self.supabase:
            raise RuntimeError("Supabase não configurado")

        leads = self.supabase.get_leads(limit=500)
        print(f"\n📂 {len(leads)} leads carregados do Supabase")
        scored = self.scoring.score_batch(leads)
        self.results = scored
        return scored

    # ═══════════════════════════════════════════════════════════
    # MODO: OUTREACH (executar cadência)
    # ═══════════════════════════════════════════════════════════

    def run_outreach(
        self,
        status_filter: str = "HOT",
        step_dia: int = 1,
        remetente: str = "Fernanda",
        limit: int = 10,
    ) -> list[dict]:
        """
        Executa step da cadência para leads com status filtrado.
        """
        # Buscar leads
        if self.supabase:
            leads = self.supabase.get_leads(status=status_filter, limit=limit)
        elif self.results:
            leads = [l for l in self.results if l.get("status") == status_filter][:limit]
        else:
            print("  ❌ Sem leads para outreach")
            return []

        # Encontrar step da cadência
        step = None
        for s in CADENCIA_SDR:
            if s.dia == step_dia:
                step = s
                break

        if not step:
            print(f"  ❌ Step dia {step_dia} não encontrado na cadência")
            return []

        print(f"\n📤 OUTREACH — Dia {step.dia}: {step.descricao}")
        print(f"   Canal: {step.canal} | Leads: {len(leads)} | Status: {status_filter}")
        print("─" * 50)

        results = []
        for lead in leads:
            nome = lead.get("nome", "?")
            print(f"\n  → {nome}")

            if self.dry_run:
                msg = self.outreach.personalize_message(step.template_key, lead, remetente)
                print(f"    [DRY RUN] Mensagem ({len(msg)} chars):")
                print(f"    {msg[:200]}...")
                result = {"empresa": nome, "status": "dry_run", "mensagem": msg}
            else:
                result = self.outreach.execute_outreach_step(lead, step, remetente)
                print(f"    Status: {result.get('status', '?')}")

                # Log no Supabase
                if self.supabase:
                    self.supabase.log_outreach(
                        lead.get("empresa_id", nome),
                        {
                            "canal": step.canal,
                            "tipo": step.tipo,
                            "mensagem": result.get("mensagem", ""),
                            "status": result.get("status", "unknown"),
                        }
                    )

            results.append(result)

        return results

    # ═══════════════════════════════════════════════════════════
    # MODO: SINGLE LEAD (testar com uma empresa)
    # ═══════════════════════════════════════════════════════════

    def process_single_lead(self, empresa_data: dict) -> dict:
        """Processa um único lead — útil para testes."""
        print(f"\n🔬 Processando: {empresa_data.get('nome', '?')}")

        # Enrich
        enriched = self.enrichment.enrich_lead(empresa_data, use_lusha=self.use_lusha)

        # Score
        scored = self.scoring.score_lead(enriched)

        # Preview outreach
        if scored.get("status") == "HOT":
            msg = self.outreach.personalize_message("whatsapp_intro", scored)
            scored["preview_mensagem"] = msg

        print(f"\n  📊 Score: {scored.get('score', 0)} pts [{scored.get('status')}]")
        print(f"  📋 Breakdown: {json.dumps(scored.get('score_breakdown', {}), indent=2)}")
        if scored.get("preview_mensagem"):
            print(f"\n  💬 Preview WhatsApp:\n  {scored['preview_mensagem'][:300]}...")

        return scored

    # ═══════════════════════════════════════════════════════════
    # MODO: DIGEST DIÁRIO
    # ═══════════════════════════════════════════════════════════

    def run_digest(
        self,
        sdr_phone: str = "",
        remetente: str = "Fernanda",
        leads: list[dict] = None,
    ) -> str:
        """
        Gera e (opcionalmente) envia o digest diário para o SDR.

        Args:
            sdr_phone: número WhatsApp do SDR (ex: 5511999999999)
            remetente: nome do SDR
            leads: lista de leads (usa self.results se None)
        """
        source = leads or self.results or []

        if sdr_phone:
            result = self.digest.send(sdr_phone, leads=source or None, remetente=remetente)
            print(f"\n📋 Digest {'enviado' if result['status'] == 'sent' else result['status']} para {sdr_phone}")
            return result.get("status", "")
        else:
            msg = self.digest.build(leads=source or None, remetente=remetente)
            print("\n" + msg)
            return msg

    # ═══════════════════════════════════════════════════════════
    # PERSIST & REPORT
    # ═══════════════════════════════════════════════════════════

    def _persist_leads(self, leads: list[dict]):
        """Upsert leads no Supabase — dual-write: pipeline legado + CRM."""
        saved = 0
        for lead in leads:
            if lead.get("status") == "excluded":
                continue
            try:
                self.supabase.upsert_lead(lead)
                saved += 1
            except Exception as e:
                print(f"  ⚠️  Erro salvando {lead.get('nome')}: {e}")
                continue
            try:
                self.supabase.upsert_crm_lead(lead)
            except Exception as e:
                print(f"  ⚠️  CRM upsert falhou para {lead.get('nome')}: {e}")
        print(f"  ✅ {saved}/{len(leads)} leads salvos no Supabase")

    def _print_report(self, leads: list[dict], elapsed: float):
        """Relatório final do pipeline."""
        hot = [l for l in leads if l.get("status") == "HOT"]
        warm = [l for l in leads if l.get("status") == "WARM"]
        cold = [l for l in leads if l.get("status") == "COLD"]
        excluded = [l for l in leads if l.get("status") == "excluded"]

        cost = self.claude.cost_report()

        print("\n" + "=" * 70)
        print("📊 RELATÓRIO FINAL — SDR AGENT 88i")
        print("=" * 70)
        print(f"  Total processados:   {len(leads)}")
        print(f"  🔥 HOT:              {len(hot)}")
        print(f"  ⚡ WARM:             {len(warm)}")
        print(f"  ❄️  COLD:             {len(cold)}")
        print(f"  ❌ Excluídos:        {len(excluded)}")
        print(f"  ⏱️  Tempo total:      {elapsed:.1f}s")
        print(f"\n  💰 CUSTO CLAUDE:")
        print(f"     Chamadas:         {cost['total_calls']}")
        print(f"     Input tokens:     {cost['input_tokens']:,}")
        print(f"     Output tokens:    {cost['output_tokens']:,}")
        print(f"     Custo total:      ${cost['total_cost_usd']:.4f}")
        print(f"     Custo/lead:       ${cost['total_cost_usd']/max(len(leads),1):.4f}")

        if hot:
            print(f"\n  🔥 TOP HOT LEADS:")
            for l in hot[:10]:
                print(f"     {l.get('score', 0):3d} pts — {l.get('nome', '?')}")
                if l.get("decisor_nome"):
                    print(f"            Decisor: {l['decisor_nome']} ({l.get('decisor_cargo', '?')})")

        print("=" * 70)

    # ═══════════════════════════════════════════════════════════
    # EXPORT
    # ═══════════════════════════════════════════════════════════

    def export_results(self, filepath: str = "sdr_results.json"):
        """Exporta resultados para JSON."""
        # Remover campos não serializáveis
        clean = []
        for lead in self.results:
            clean_lead = {}
            for k, v in lead.items():
                try:
                    json.dumps(v)
                    clean_lead[k] = v
                except (TypeError, ValueError):
                    clean_lead[k] = str(v)
            clean.append(clean_lead)

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(clean, f, ensure_ascii=False, indent=2)
        print(f"\n📁 Resultados exportados: {filepath}")

    def close(self):
        self.claude.close()
        self.discovery.close()
        self.enrichment.close()
        self.outreach.close()
        if self.supabase:
            self.supabase.close()


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="SDR Agent 88i — OlgaAI")
    parser.add_argument("--mode", choices=["full", "score-only", "outreach", "single", "digest"], default="full")
    parser.add_argument("--dry-run", action="store_true", help="Simular sem enviar mensagens")
    parser.add_argument("--no-supabase", action="store_true", help="Não usar Supabase")
    parser.add_argument("--no-lusha", action="store_true", help="Não usar Lusha")
    parser.add_argument("--input", type=str, help="Arquivo de entrada (JSON)")
    parser.add_argument("--icps", type=str, default="ICP1,ICP2,ICP3", help="ICPs a processar")
    parser.add_argument("--cidades", type=str, default="", help="Cidades (separadas por vírgula)")
    parser.add_argument("--max-per-query", type=int, default=30, help="Max resultados por query Apify")
    parser.add_argument("--sources", type=str, default="", help="Fontes: linkedin,google_maps (padrão: settings)")
    parser.add_argument("--outreach-status", type=str, default="HOT", help="Status para outreach")
    parser.add_argument("--outreach-dia", type=int, default=1, help="Dia da cadência")
    parser.add_argument("--outreach-limit", type=int, default=10, help="Max leads para outreach")
    parser.add_argument("--export", type=str, default="sdr_results.json", help="Arquivo de saída")
    parser.add_argument("--sdr-phone", type=str, default="", help="WhatsApp do SDR para receber o digest")
    parser.add_argument("--remetente", type=str, default="Fernanda", help="Nome do SDR (para personalização)")

    args = parser.parse_args()

    orchestrator = SDROrchestrator(
        dry_run=args.dry_run,
        use_supabase=not args.no_supabase,
        use_lusha=not args.no_lusha,
    )

    try:
        if args.mode == "full":
            icps = args.icps.split(",")
            cidades = args.cidades.split(",") if args.cidades else None
            sources = args.sources.split(",") if args.sources else None
            orchestrator.run_full_pipeline(icps, cidades, args.max_per_query, sources=sources)

        elif args.mode == "score-only":
            if args.input:
                orchestrator.score_from_file(args.input)
            else:
                orchestrator.score_from_pipeline()

        elif args.mode == "outreach":
            orchestrator.run_outreach(
                status_filter=args.outreach_status,
                step_dia=args.outreach_dia,
                limit=args.outreach_limit,
            )

        elif args.mode == "digest":
            orchestrator.run_digest(
                sdr_phone=args.sdr_phone,
                remetente=args.remetente,
            )

        elif args.mode == "single":
            # Teste com empresa hardcoded
            test_lead = {
                "nome": "Keeta (Meituan Brasil)",
                "site": "keeta.com/br",
                "icp_tipo": "ICP1",
                "cidade": "São Paulo",
                "uf": "SP",
            }
            orchestrator.process_single_lead(test_lead)

        orchestrator.export_results(args.export)

    finally:
        orchestrator.close()


if __name__ == "__main__":
    main()
