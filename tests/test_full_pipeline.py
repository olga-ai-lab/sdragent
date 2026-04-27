"""
Teste completo do SDR Agent — executa todas as camadas sem APIs pagas.

Uso:
    python tests/test_full_pipeline.py
    python tests/test_full_pipeline.py --phone 5511961490565  # envia digest

O que é testado:
    [1] Supabase — conexão e leitura de leads existentes
    [2] PainSignalDetector — detecção de sinais de dor
    [3] LeadMerger — fusão cross-source
    [4] ScoringEngine — scoring + deal value (sem IA)
    [5] Daily Digest — geração de mensagem (+ envio se --phone)
    [6] SupabaseClient.upsert_lead — grava lead de teste e apaga
    [7] Outreach — personalização de mensagem (sem envio)
    [8] StateMachine — transições de estado
"""

import sys
import os
import json
import argparse
from datetime import datetime, timezone

# carrega .env se existir
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
except ImportError:
    pass  # sem python-dotenv, variáveis de ambiente já devem estar setadas

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


def ok(msg): print(f"  {GREEN}✅ {msg}{RESET}")
def fail(msg): print(f"  {RED}❌ {msg}{RESET}")
def info(msg): print(f"  {CYAN}ℹ️  {msg}{RESET}")
def warn(msg): print(f"  {YELLOW}⚠️  {msg}{RESET}")
def header(msg): print(f"\n{BOLD}{CYAN}{'─'*55}{RESET}\n{BOLD}  {msg}{RESET}\n{BOLD}{CYAN}{'─'*55}{RESET}")

# ─── Lead de teste canônico ───────────────────────────────────────
TEST_LEAD = {
    "empresa_id":           "test_88i_keeta_2026",
    "nome":                 "Keeta Brasil (Meituan)",
    "site":                 "keeta.com/br",
    "icp_tipo":             "ICP1",
    "cidade":               "São Paulo",
    "uf":                   "SP",
    "pais":                 "Brazil",
    "source":               "test",
    "status":               "discovered",
    "ai_segmento":          "food delivery",
    "ai_tem_entregadores":  True,
    "ai_entregadores_est":  "50000",
    "ai_porte":             "grande",
    "ai_plataforma_digital": True,
    "ai_seguro_detectado":  "nao",
    "ai_confianca":         0.92,
    "data_inicio_atividade":"2024-03-01",
    "decisor_nome":         "Eduardo Fischer",
    "decisor_cargo":        "CEO",
    "decisor_telefone":     "11961490565",
    "decisor_linkedin":     "https://linkedin.com/in/eduardofischer",
    "descricao_linkedin":   "Plataforma de delivery do Meituan, operando no Brasil com entregadores.",
    "linkedin_url":         "https://linkedin.com/company/keeta-brasil",
}

RESULTS: list[dict] = []

# ─────────────────────────────────────────────────────────────────
# [1] SUPABASE
# ─────────────────────────────────────────────────────────────────

def test_supabase():
    header("[1] Supabase — conexão e leitura")
    from modules.supabase_client import SupabaseClient
    from config.settings import SUPABASE_URL, SUPABASE_KEY, SUPABASE_SERVICE_KEY

    if not SUPABASE_URL or "your-project" in SUPABASE_URL:
        warn("SUPABASE_URL não configurado — pulando")
        RESULTS.append({"test": "supabase_read", "status": "skip"})
        return None

    key_type = "service_role" if SUPABASE_SERVICE_KEY else "anon"
    info(f"URL: {SUPABASE_URL}  |  key: {key_type}")

    try:
        sb = SupabaseClient()
        leads = sb.get_leads(limit=5)
        ok(f"Conexão OK — {len(leads)} leads carregados (mostrando primeiros 3)")
        for l in leads[:3]:
            info(f"  {l.get('nome','?')} | score={l.get('score','?')} | status={l.get('status','?')}")
        RESULTS.append({"test": "supabase_read", "status": "ok", "leads": len(leads)})
        return sb
    except Exception as e:
        err = str(e)
        # Check response body for allowlist error (not surfaced in exception message)
        body = ""
        if hasattr(e, "response") and e.response is not None:  # type: ignore[attr-defined]
            try:
                body = e.response.text  # type: ignore[attr-defined]
            except Exception:
                pass
        is_allowlist = "allowlist" in (err + body).lower() or "Host not in allowlist" in body
        if is_allowlist or ("403" in err and not body):
            fail("Supabase bloqueou a conexão (403 — provável API Allowlist ativado).")
            warn("Para resolver, escolha UMA das opções:")
            warn("  A) Dashboard → Settings → API → API Allowlist → desabilitar ou add '*'")
            warn("  B) Adicionar no .env:  SUPABASE_SERVICE_KEY=<service_role key>")
            warn("     (Dashboard → Settings → API → Project API keys → service_role)")
        else:
            fail(f"Erro Supabase: {err}")
        RESULTS.append({"test": "supabase_read", "status": "error", "error": err})
        return None


# ─────────────────────────────────────────────────────────────────
# [2] PAIN SIGNAL DETECTOR
# ─────────────────────────────────────────────────────────────────

def test_pain_signals():
    header("[2] PainSignalDetector")
    from modules.pain_signals import PainSignalDetector
    pd = PainSignalDetector()

    cases = [
        ({"ai_seguro_detectado": "nao", "ai_plataforma_digital": True},       "sinistro_midia",          10),
        ({"data_inicio_atividade": "2025-06-01", "ai_seguro_detectado": "nao"}, "empresa_nova_sem_seguro", 8),
        ({"ai_tem_entregadores": True, "ai_seguro_detectado": "desconhecido"}, "alto_risco",               5),
        ({"descricao_linkedin": "empresa de logística registrou acidente"},    "sinistro_midia",           10),
        ({"ai_seguro_detectado": "sim"},                                       "nenhum",                   0),
        (TEST_LEAD,                                                             "sinistro_midia",           10),
    ]

    passed = 0
    for lead, expected_sinal, expected_score in cases:
        result = pd.detect(lead)
        if result["sinal"] == expected_sinal and result["score"] == expected_score:
            ok(f"{expected_sinal} ({expected_score}pts) — {result['motivo'][:60]}")
            passed += 1
        else:
            fail(f"Esperado {expected_sinal}/{expected_score}, got {result['sinal']}/{result['score']}")

    RESULTS.append({"test": "pain_signals", "status": "ok" if passed == len(cases) else "partial", "passed": passed, "total": len(cases)})


# ─────────────────────────────────────────────────────────────────
# [3] LEAD MERGER
# ─────────────────────────────────────────────────────────────────

def test_lead_merger():
    header("[3] LeadMerger — fusão cross-source")
    from modules.lead_merger import LeadMerger
    lm = LeadMerger()

    linkedin_lead = {
        "nome": "Keeta Brasil",
        "source": "linkedin",
        "linkedin_url": "https://linkedin.com/company/keeta",
        "employees_linkedin": 800,
        "industry_linkedin": "Food & Beverages",
        "descricao_linkedin": "Delivery platform",
    }
    google_lead = {
        "nome": "Keeta Brasil Ltda",
        "source": "apify_google_maps",
        "site": "keeta.com.br",
        "telefone": "1130001234",
        "endereco": "Av Paulista 1000, SP",
        "rating_google": 4.2,
    }

    # Teste merge direto
    merged = lm.merge(google_lead, linkedin_lead)
    assert merged.get("site") == "keeta.com.br",       "site deve vir do Google Maps"
    assert merged.get("linkedin_url"),                  "linkedin_url deve vir do LinkedIn"
    assert merged.get("rating_google") == 4.2,          "rating_google deve preservar"
    assert "linkedin" in merged.get("source", ""),      "source deve incluir linkedin"
    ok(f"Merge direto OK — source={merged['source']}, colunas={len(merged)}")

    # Teste batch com dedup
    leads = [
        {"nome": "Fast Delivery", "source": "apify_google_maps", "site": "fast.com"},
        {"nome": "Fast Delivery Ltda", "source": "linkedin", "linkedin_url": "li.com/fast"},
        {"nome": "Outra Empresa", "source": "apify_google_maps"},
    ]
    deduped = lm.merge_batch(leads)
    assert len(deduped) == 2, f"Esperado 2, got {len(deduped)}"
    ok(f"Batch dedup OK — {len(leads)} → {len(deduped)} leads")

    RESULTS.append({"test": "lead_merger", "status": "ok"})


# ─────────────────────────────────────────────────────────────────
# [4] SCORING ENGINE
# ─────────────────────────────────────────────────────────────────

def test_scoring():
    header("[4] ScoringEngine — scoring + deal value + pain signal")
    from modules.scoring import ScoringEngine
    se = ScoringEngine()

    scored = se.score_lead(dict(TEST_LEAD))

    score = scored.get("score", 0)
    status = scored.get("status")
    deal = scored.get("deal_value_est", 0)
    sinal = scored.get("sinal_dor")

    ok(f"Score: {score} pts → {status}")
    ok(f"Deal value est: R${deal:,.0f}/ano ({scored.get('deal_value_premissas','')})")
    ok(f"Sinal de dor: {sinal} — {scored.get('sinal_dor_motivo','')[:70]}")

    assert score > 0,    "score deve ser > 0"
    assert deal > 0,     "deal_value_est deve ser > 0 com 50k entregadores"
    assert sinal != None, "sinal_dor deve estar preenchido"

    # Testa batch com leads de diferentes ICPs
    batch = [
        {**TEST_LEAD, "empresa_id": "test_batch_1", "icp_tipo": "ICP1"},
        {"empresa_id": "test_batch_2", "nome": "TMS PME", "icp_tipo": "ICP3",
         "ai_entregadores_est": "200", "ai_seguro_detectado": "desconhecido"},
        {"empresa_id": "test_batch_3", "nome": "Empresa Excluída", "icp_tipo": "ICP1",
         "ai_tem_entregadores": False},
    ]
    scored_batch = se.score_batch(batch)
    assert len(scored_batch) == 3
    hot_count = sum(1 for l in scored_batch if l.get("status") == "HOT")
    ok(f"Batch: {len(scored_batch)} leads → {hot_count} HOT")

    RESULTS.append({"test": "scoring", "status": "ok", "score": score, "deal_value": deal})
    return scored


# ─────────────────────────────────────────────────────────────────
# [5] DAILY DIGEST
# ─────────────────────────────────────────────────────────────────

def test_digest(scored_lead: dict, phone: str = ""):
    header("[5] Daily Digest")
    from modules.daily_digest import DailyDigest
    from modules.scoring import ScoringEngine

    se = ScoringEngine()
    leads = se.score_batch([
        {**TEST_LEAD, "empresa_id": "test_dig_1"},
        {
            "empresa_id": "test_dig_2", "nome": "Rappi Brasil", "icp_tipo": "ICP1",
            "ai_entregadores_est": "20000", "ai_seguro_detectado": "desconhecido",
            "ai_tem_entregadores": True, "ai_porte": "grande",
            "decisor_nome": "Rodrigo Alves", "decisor_cargo": "COO",
            "decisor_telefone": "11912345678", "deal_value_est": 672000,
        },
        {
            "empresa_id": "test_dig_3", "nome": "Leve Delivery", "icp_tipo": "ICP1",
            "ai_entregadores_est": "300", "ai_seguro_detectado": "nao",
            "ai_tem_entregadores": True, "ai_porte": "pequeno",
        },
    ])

    digest = DailyDigest()
    msg = digest.build(leads=leads, remetente="Fernanda")

    print("\n" + "─" * 50)
    print(msg)
    print("─" * 50)

    assert "DIGEST SDR 88i" in msg, "Cabeçalho ausente"
    assert "HOT" in msg or "WARM" in msg, "Status ausente"
    ok(f"Digest gerado ({len(msg)} chars)")

    if phone:
        phone_clean = phone.replace("+","").replace("-","").replace(" ","")
        if not phone_clean.startswith("55"):
            phone_clean = "55" + phone_clean
        result = digest.send(phone=phone_clean, leads=leads, remetente="Fernanda")
        if result["status"] == "sent":
            ok(f"Digest enviado para +{phone_clean} via WhatsApp!")
        elif result["status"] == "simulated":
            warn("Evolution API não configurada — digest impresso acima (simulado)")
        else:
            fail(f"Erro no envio: {result.get('error','?')}")

    RESULTS.append({"test": "digest", "status": "ok", "chars": len(msg)})


# ─────────────────────────────────────────────────────────────────
# [6] SUPABASE UPSERT
# ─────────────────────────────────────────────────────────────────

def test_supabase_upsert(sb):
    header("[6] SupabaseClient — upsert e limpeza")
    if sb is None:
        warn("Supabase não disponível — pulando")
        RESULTS.append({"test": "supabase_upsert", "status": "skip"})
        return

    from modules.scoring import ScoringEngine
    se = ScoringEngine()
    scored_lead = se.score_lead(dict(TEST_LEAD))

    try:
        sb.upsert_lead(scored_lead)
        ok(f"Lead '{scored_lead['nome']}' persistido — score={scored_lead['score']}, deal=R${scored_lead.get('deal_value_est',0):,.0f}")

        # Verificar que foi salvo
        leads = sb.get_leads(limit=200)
        saved = next((l for l in leads if l.get("empresa_id") == TEST_LEAD["empresa_id"]), None)
        if saved:
            ok(f"Confirmado no Supabase — score={saved.get('score')}, status={saved.get('status')}")
        else:
            warn("Lead não encontrado na query (pode ser RLS policy)")

        RESULTS.append({"test": "supabase_upsert", "status": "ok"})
    except Exception as e:
        fail(f"Erro upsert: {e}")
        RESULTS.append({"test": "supabase_upsert", "status": "error", "error": str(e)})
    finally:
        # Limpar lead de teste
        try:
            sb.client.delete(
                sb._rest_url("companies_88i_pipeline"),
                params={"empresa_id": f"eq.{TEST_LEAD['empresa_id']}"},
            )
            info("Lead de teste removido")
        except Exception:
            warn("Não foi possível limpar lead de teste (verifique manualmente)")


# ─────────────────────────────────────────────────────────────────
# [6b] CRM UPSERT — leads_bamaq (Pipeline 6)
# ─────────────────────────────────────────────────────────────────

def test_crm_upsert(sb):
    header("[6b] CRM — upsert em leads_bamaq + verificação de stage")
    if sb is None:
        warn("Supabase não disponível — pulando")
        RESULTS.append({"test": "crm_upsert", "status": "skip"})
        return

    from modules.scoring import ScoringEngine
    scored = ScoringEngine().score_lead(dict(TEST_LEAD))

    try:
        sb.upsert_crm_lead(scored)
        ok(f"CRM upsert OK — empresa_id={scored['empresa_id']}, stage esperado=43")

        crm = sb.get_crm_lead_by_empresa_id(scored["empresa_id"])
        if crm:
            ok(f"Confirmado no CRM — pipeline_id={crm.get('pipeline_id')}, stage_id={crm.get('stage_id')}, sdr_status={crm.get('sdr_status')}")
            assert crm.get("pipeline_id") == 6, f"pipeline_id deve ser 6, got {crm.get('pipeline_id')}"
            assert crm.get("stage_id") in (42, 43, 44), f"stage_id inesperado: {crm.get('stage_id')}"
        else:
            warn("Lead não encontrado na query CRM")

        RESULTS.append({"test": "crm_upsert", "status": "ok"})
    except Exception as e:
        fail(f"Erro CRM upsert: {e}")
        RESULTS.append({"test": "crm_upsert", "status": "error", "error": str(e)})
    finally:
        try:
            sb.client.delete(
                sb._rest_url("leads_bamaq"),
                params={"empresa_id": f"eq.{TEST_LEAD['empresa_id']}"},
            )
            info("Lead CRM de teste removido")
        except Exception:
            warn("Não foi possível limpar lead CRM de teste")


# ─────────────────────────────────────────────────────────────────
# [6c] CRM STAGE TRANSITION — HOT → meeting_booked (43 → 45)
# ─────────────────────────────────────────────────────────────────

def test_crm_stage_transition(sb):
    header("[6c] CRM — transição HOT → meeting_booked (stage 43→45)")
    if sb is None:
        warn("Supabase não disponível — pulando")
        RESULTS.append({"test": "crm_stage_transition", "status": "skip"})
        return

    lead = {**TEST_LEAD, "empresa_id": "test_88i_crm_transition", "status": "HOT"}
    try:
        sb.upsert_crm_lead(lead)
        ok("Lead inserido no CRM com stage HOT=43")

        sb.update_crm_stage(lead["empresa_id"], stage_id=45, sdr_status="meeting_booked")
        crm = sb.get_crm_lead_by_empresa_id(lead["empresa_id"])
        if crm:
            assert crm.get("stage_id") == 45, f"stage_id deve ser 45, got {crm.get('stage_id')}"
            assert crm.get("sdr_status") == "meeting_booked"
            ok(f"Stage atualizado corretamente → stage_id={crm['stage_id']}, sdr_status={crm['sdr_status']}")

        RESULTS.append({"test": "crm_stage_transition", "status": "ok"})
    except Exception as e:
        fail(f"Erro transição CRM: {e}")
        RESULTS.append({"test": "crm_stage_transition", "status": "error", "error": str(e)})
    finally:
        try:
            sb.client.delete(
                sb._rest_url("leads_bamaq"),
                params={"empresa_id": f"eq.{lead['empresa_id']}"},
            )
            info("Lead CRM de transição removido")
        except Exception:
            warn("Não foi possível limpar lead de transição")


# ─────────────────────────────────────────────────────────────────
# [6d] FIELD MAPPER — _map_agent_to_crm converte todos os campos
# ─────────────────────────────────────────────────────────────────

def test_field_mapper():
    header("[6d] Field mapper — _map_agent_to_crm")
    from modules.supabase_client import _map_agent_to_crm
    from config.settings import SDR_STAGE_MAP

    lead = {
        **TEST_LEAD,
        "status": "HOT",
        "score": 75,
        "score_breakdown": {"volume_entregadores": 30},
        "deal_value_est": 1800000,
    }
    crm = _map_agent_to_crm(lead)

    assert crm.get("pipeline_id") == 6,                 "pipeline_id deve ser 6"
    assert crm.get("company_name") == TEST_LEAD["nome"], "nome → company_name"
    assert crm.get("full_name") == TEST_LEAD["decisor_nome"], "decisor_nome → full_name"
    assert crm.get("sdr_score") == 75,                  "score → sdr_score"
    assert crm.get("stage_id") == SDR_STAGE_MAP["HOT"], "HOT → stage 43"
    assert crm.get("sdr_status") == "HOT",              "sdr_status = HOT"
    assert crm.get("empresa_id") == TEST_LEAD["empresa_id"], "empresa_id passthrough"
    assert crm.get("icp_tipo") == "ICP1",               "icp_tipo passthrough"
    assert crm.get("deal_value_est") == 1800000,        "deal_value_est passthrough"

    ok(f"Mapper OK — {len(crm)} campos mapeados, stage_id={crm['stage_id']}, pipeline_id={crm['pipeline_id']}")
    RESULTS.append({"test": "field_mapper", "status": "ok", "fields": len(crm)})


# ─────────────────────────────────────────────────────────────────
# [7] OUTREACH — personalização de mensagem
# ─────────────────────────────────────────────────────────────────

def test_outreach():
    header("[7] OutreachEngine — personalização de mensagem")
    from modules.outreach import OutreachEngine
    from modules.claude_client import ClaudeClient

    # Sem ANTHROPIC_API_KEY → vai usar template direto
    try:
        claude = ClaudeClient()
        oe = OutreachEngine(claude)
        from modules.scoring import ScoringEngine
        scored = ScoringEngine().score_lead(dict(TEST_LEAD))

        msg = oe.personalize_message("whatsapp_intro", scored, remetente="Fernanda")
        info(f"Mensagem WhatsApp Dia 1 ({len(msg)} chars):")
        for line in msg.strip().split("\n"):
            print(f"    {line}")

        msg_li = oe.personalize_message("linkedin_connect", scored)
        ok(f"LinkedIn connect ({len(msg_li)} chars)")
        ok("Templates de outreach OK")
        RESULTS.append({"test": "outreach", "status": "ok"})
    except Exception as e:
        warn(f"Outreach parcial (sem Claude API key?): {e}")
        RESULTS.append({"test": "outreach", "status": "partial", "error": str(e)})


# ─────────────────────────────────────────────────────────────────
# [8] STATE MACHINE
# ─────────────────────────────────────────────────────────────────

def test_state_machine():
    header("[8] LeadStateMachine — transições")
    from modules.state_machine import LeadStateMachine
    sm = LeadStateMachine()

    lead = {"nome": "Teste Delivery", "status": "discovered"}

    transitions = [
        ("enriched",  True),
        ("HOT",       True),
        ("contacted", True),
        ("replied",   True),
        ("meeting_booked", True),
        ("won",       True),
        ("discovered", False),  # transição inválida a partir de won
    ]

    for target, should_work in transitions:
        try:
            lead = sm.transition(lead, target, reason=f"teste auto")
            if should_work:
                ok(f"→ {target}")
            else:
                fail(f"→ {target} DEVERIA ter falhado")
        except ValueError:
            if not should_work:
                ok(f"→ {target} corretamente bloqueado")
            else:
                fail(f"→ {target} bloqueado incorretamente")

    RESULTS.append({"test": "state_machine", "status": "ok"})


# ─────────────────────────────────────────────────────────────────
# RUNNER PRINCIPAL
# ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Teste completo SDR Agent 88i")
    parser.add_argument("--phone", default="", help="Número para receber digest (ex: 5511961490565)")
    args = parser.parse_args()

    print(f"\n{BOLD}{'='*55}")
    print(" SDR AGENT 88i — SUITE DE TESTES COMPLETA")
    print(f"{'='*55}{RESET}")
    print(f"  Data: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    if args.phone:
        print(f"  Digest → {args.phone}")

    sb = test_supabase()
    test_pain_signals()
    test_lead_merger()
    scored = test_scoring()
    test_digest(scored, phone=args.phone)
    test_supabase_upsert(sb)
    test_field_mapper()
    test_crm_upsert(sb)
    test_crm_stage_transition(sb)
    test_outreach()
    test_state_machine()

    # Relatório final
    print(f"\n{BOLD}{'='*55}")
    print(" RESULTADO FINAL")
    print(f"{'='*55}{RESET}")
    for r in RESULTS:
        status = r["status"]
        name = r["test"]
        if status == "ok":
            print(f"  {GREEN}✅ {name}{RESET}")
        elif status == "skip":
            print(f"  {YELLOW}⏭️  {name} (skipped){RESET}")
        elif status == "partial":
            print(f"  {YELLOW}⚠️  {name} (partial){RESET}")
        else:
            print(f"  {RED}❌ {name}: {r.get('error','?')}{RESET}")

    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r["status"] == "ok")
    skipped = sum(1 for r in RESULTS if r["status"] == "skip")
    print(f"\n  {BOLD}{passed}/{total - skipped} testes passaram{RESET} ({skipped} skipped)\n")


if __name__ == "__main__":
    main()
