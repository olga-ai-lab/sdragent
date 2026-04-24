"""
Tests — SDR Agent 88i
Rodar: pytest tests/ -v
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.scoring import ScoringEngine
from modules.state_machine import LeadStateMachine, VALID_TRANSITIONS


# ═══════════════════════════════════════════════════════════════
# SCORING ENGINE
# ═══════════════════════════════════════════════════════════════

class TestScoringEngine:

    def setup_method(self):
        self.engine = ScoringEngine()

    def test_hot_lead_keeta(self):
        """Keeta = empresa nova, sem seguro, 100k entregadores, plataforma digital."""
        lead = {
            "nome": "Keeta (Meituan Brasil)",
            "icp_tipo": "ICP1",
            "ai_entregadores_est": "100000",
            "ai_seguro_detectado": "nao",
            "ai_plataforma_digital": True,
            "ai_porte": "grande",
            "decisor_cargo": "CEO",
        }
        result = self.engine.score_lead(lead)
        assert result["score"] >= 70, f"Keeta deveria ser HOT, score={result['score']}"
        assert result["status"] == "HOT"

    def test_warm_lead_jadlog(self):
        """Jadlog = grande operação, seguro desconhecido, decisor não mapeado."""
        lead = {
            "nome": "Jadlog",
            "icp_tipo": "ICP1",
            "ai_entregadores_est": "5000",
            "ai_seguro_detectado": "desconhecido",
            "ai_plataforma_digital": False,
            "ai_porte": "grande",
        }
        result = self.engine.score_lead(lead)
        assert 40 <= result["score"] < 70, f"Jadlog deveria ser WARM, score={result['score']}"
        assert result["status"] == "WARM"

    def test_excluded_saas_pure(self):
        """SaaS puro sem entregadores deve ser excluído."""
        lead = {
            "nome": "RoutEasy",
            "icp_tipo": "ICP1",
            "ai_tem_entregadores": False,
            "ai_seguro_detectado": "desconhecido",
        }
        result = self.engine.score_lead(lead)
        assert result["status"] == "excluded"
        assert result["score"] == 0

    def test_score_clamp_0_100(self):
        """Score nunca deve sair do range 0-100."""
        lead_max = {
            "nome": "Empresa Perfeita",
            "icp_tipo": "ICP1",
            "ai_entregadores_est": "100000",
            "ai_seguro_detectado": "nao",
            "ai_plataforma_digital": True,
            "ai_porte": "grande",
            "decisor_cargo": "CEO",
            "decisor_email": "ceo@empresa.com",
            "decisor_telefone": "11999999999",
            "decisor_linkedin": "linkedin.com/in/ceo",
            "ai_confianca": 0.95,
        }
        result = self.engine.score_lead(lead_max)
        assert 0 <= result["score"] <= 100

    def test_volume_parsing_ranges(self):
        """Parser de volume deve lidar com formatos variados."""
        test_cases = [
            ("50000", 30),    # >50k
            ("50k", 30),      # >50k com k
            ("10000-50000", 20),  # range 10k-50k (upper bound)
            ("500", 5),       # <1k
            ("100k+", 30),    # 100k+ = >50k
        ]
        for vol, expected_min in test_cases:
            lead = {
                "nome": f"Test {vol}",
                "icp_tipo": "ICP1",
                "ai_entregadores_est": vol,
            }
            result = self.engine.score_lead(lead)
            score_vol = result.get("score_breakdown", {}).get("volume_entregadores", 0)
            assert score_vol >= expected_min - 5, f"Volume '{vol}' → score {score_vol}, esperado >= {expected_min-5}"

    def test_batch_scoring_sorted(self):
        """Batch scoring deve retornar leads ordenados por score desc."""
        leads = [
            {"nome": "A", "icp_tipo": "ICP1", "ai_entregadores_est": "100", "ai_seguro_detectado": "desconhecido"},
            {"nome": "B", "icp_tipo": "ICP1", "ai_entregadores_est": "100000", "ai_seguro_detectado": "nao"},
            {"nome": "C", "icp_tipo": "ICP1", "ai_entregadores_est": "5000", "ai_seguro_detectado": "nao"},
        ]
        results = self.engine.score_batch(leads)
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True), "Batch deve estar ordenado por score desc"

    def test_icp2_scoring(self):
        """ICP2 deve usar scoring diferente (volume despachos, API, etc)."""
        lead = {
            "nome": "Nuvemshop",
            "icp_tipo": "ICP2",
            "ai_volume_despachos": ">1M/mes",
            "ai_integracao_api": "api_publica_doc",
            "ai_posicao_despacho": "gera_etiqueta",
            "ai_mercado_sellers": "marketplaces",
            "decisor_cargo": "CTO",
        }
        result = self.engine.score_lead(lead)
        assert result["score"] >= 70, f"Nuvemshop ICP2 deveria ser HOT, score={result['score']}"

    def test_bonus_contact_info(self):
        """Bonus por email/telefone/LinkedIn verificados."""
        lead_sem = {
            "nome": "Sem Contato",
            "icp_tipo": "ICP1",
            "ai_entregadores_est": "10000",
            "ai_seguro_detectado": "nao",
        }
        lead_com = {
            **lead_sem,
            "nome": "Com Contato",
            "decisor_email": "ceo@empresa.com",
            "decisor_telefone": "11999999999",
            "decisor_linkedin": "linkedin.com/in/ceo",
            "ai_confianca": 0.9,
        }
        result_sem = self.engine.score_lead(lead_sem)
        result_com = self.engine.score_lead(lead_com)
        assert result_com["score"] > result_sem["score"], "Lead com contato deve ter score maior"


# ═══════════════════════════════════════════════════════════════
# STATE MACHINE
# ═══════════════════════════════════════════════════════════════

class TestStateMachine:

    def setup_method(self):
        self.sm = LeadStateMachine()

    def test_valid_transitions(self):
        """Transições válidas devem funcionar."""
        lead = {"nome": "Test", "status": "discovered"}

        lead = self.sm.transition(lead, "enriched", "test")
        assert lead["status"] == "enriched"

        lead = self.sm.transition(lead, "HOT", "score 85")
        assert lead["status"] == "HOT"

        lead = self.sm.transition(lead, "contacted", "outreach dia 1")
        assert lead["status"] == "contacted"

        lead = self.sm.transition(lead, "replied", "WhatsApp reply")
        assert lead["status"] == "replied"

        lead = self.sm.transition(lead, "meeting_booked", "calendário")
        assert lead["status"] == "meeting_booked"

        lead = self.sm.transition(lead, "won", "contrato assinado")
        assert lead["status"] == "won"

    def test_invalid_transition_raises(self):
        """Transições inválidas devem levantar ValueError."""
        lead = {"nome": "Test", "status": "discovered"}

        with pytest.raises(ValueError):
            self.sm.transition(lead, "won")  # discovered → won não é válido

        with pytest.raises(ValueError):
            self.sm.transition(lead, "contacted")  # discovered → contacted não é válido

    def test_won_is_final(self):
        """Won é estado final — nenhuma transição possível."""
        lead = {"nome": "Test", "status": "won"}
        transitions = self.sm.get_available_transitions("won")
        assert transitions == []

    def test_excluded_is_final(self):
        """Excluded é estado final."""
        transitions = self.sm.get_available_transitions("excluded")
        assert transitions == []

    def test_status_history_tracked(self):
        """Histórico de transições deve ser registrado."""
        lead = {"nome": "Test", "status": "discovered"}
        lead = self.sm.transition(lead, "enriched", "auto")
        lead = self.sm.transition(lead, "HOT", "score 80")

        history = lead.get("status_history", [])
        assert len(history) == 2
        assert history[0]["from"] == "discovered"
        assert history[0]["to"] == "enriched"
        assert history[1]["from"] == "enriched"
        assert history[1]["to"] == "HOT"

    def test_null_status_history_treated_as_empty(self):
        """Supabase can return status_history null; transition should initialize a list."""
        lead = {"nome": "Test", "status": "contacted", "status_history": None}
        updated = self.sm.transition(lead, "replied", "WhatsApp reply")
        assert updated["status"] == "replied"
        assert len(updated["status_history"]) == 1
        assert updated["status_history"][0]["from"] == "contacted"
        assert updated["status_history"][0]["to"] == "replied"

    def test_lost_can_reactivate(self):
        """Lead perdido pode ser reativado (nurture ou contacted)."""
        lead = {"nome": "Test", "status": "lost"}
        transitions = self.sm.get_available_transitions("lost")
        assert "nurture" in transitions
        assert "contacted" in transitions

    def test_bulk_transition(self):
        """Bulk transition deve separar sucesso e falhas."""
        leads = [
            {"nome": "A", "status": "enriched"},
            {"nome": "B", "status": "enriched"},
            {"nome": "C", "status": "won"},  # won → HOT é inválido
        ]
        success, failures = self.sm.bulk_transition(leads, "HOT", "batch score")
        assert len(success) == 2
        assert len(failures) == 1
        assert failures[0]["nome"] == "C"


# ═══════════════════════════════════════════════════════════════
# REPLY INTENT CLASSIFIER
# ═══════════════════════════════════════════════════════════════

class TestReplyClassifier:

    def test_positive_intents(self):
        from modules.webhook_server import _classify_reply_intent

        assert _classify_reply_intent("Sim, tenho interesse") == "interested"
        assert _classify_reply_intent("Pode me ligar amanhã") == "interested"
        assert _classify_reply_intent("Bora, vamos conversar") == "interested"

    def test_negative_intents(self):
        from modules.webhook_server import _classify_reply_intent

        assert _classify_reply_intent("Não, sem interesse") == "not_interested"
        assert _classify_reply_intent("Pare de me mandar mensagem") == "not_interested"

    def test_info_request(self):
        from modules.webhook_server import _classify_reply_intent

        assert _classify_reply_intent("Quanto custa?") == "info_request"
        assert _classify_reply_intent("Me explica como funciona") == "info_request"

    def test_redirect(self):
        from modules.webhook_server import _classify_reply_intent

        assert _classify_reply_intent("Fala com o João que é o responsável") == "redirect"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
