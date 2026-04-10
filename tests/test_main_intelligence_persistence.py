import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import ProductionOrchestrator


class DummySupabase:
    def __init__(self):
        self.calls = []

    def upsert_lead_intelligence(self, empresa_id, report):
        self.calls.append((empresa_id, report))


def _make_orchestrator_for_test():
    orch = ProductionOrchestrator.__new__(ProductionOrchestrator)
    orch.supabase = DummySupabase()
    return orch


def test_persist_intelligence_uses_full_report_when_available():
    orch = _make_orchestrator_for_test()

    class FullEngine:
        def build_full_intelligence_report(self, lead):
            return {"source": "full_l1_l7", "score": 91}

    orch.intelligence = FullEngine()

    lead = {"empresa_id": "acme-1", "nome": "Acme", "icp_tipo": "ICP1"}
    orch._persist_intelligence_snapshot(lead)

    assert len(orch.supabase.calls) == 1
    empresa_id, report = orch.supabase.calls[0]
    assert empresa_id == "acme-1"
    assert report["source"] == "full_l1_l7"
    assert report["score"] == 91


def test_persist_intelligence_falls_back_to_snapshot_when_full_fails():
    orch = _make_orchestrator_for_test()

    class FallbackEngine:
        def build_full_intelligence_report(self, lead):
            raise RuntimeError("boom")

        def l6_score(self, payload):
            class ScoreResult:
                score = 42
                breakdown = {"fit": 10}

            return ScoreResult()

        def calc_deal_value(self, icp, entregadores, entregas_mes, num_clientes):
            return {"deal_value_est": 1000, "deal_value_premissas": "premissas"}

        def build_report(self, lead, score_result, dv, closing_intelligence=None, source="python_os"):
            return {
                "source": source,
                "score": score_result.score,
                "score_breakdown": score_result.breakdown,
                "deal_value_est": dv["deal_value_est"],
            }

    orch.intelligence = FallbackEngine()

    lead = {"empresa_id": "acme-2", "nome": "Acme", "icp_tipo": "ICP1", "ai_entregadores_est": "500"}
    orch._persist_intelligence_snapshot(lead)

    assert len(orch.supabase.calls) == 1
    empresa_id, report = orch.supabase.calls[0]
    assert empresa_id == "acme-2"
    assert report["source"] == "pipeline_snapshot"
    assert report["score"] == 42
