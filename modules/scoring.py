"""
Scoring Engine — Modelo de pontuação 0-100 por ICP.
Regra de Ouro #3: IA só onde há ambiguidade. Regras fixas primeiro.
"""

from typing import Optional
from config.settings import (
    SCORING_ICP1, SCORING_ICP2, SCORING_ICP3,
    ICP_DEFINITIONS, EXCLUSION_FILTERS,
)
from modules.claude_client import ClaudeClient


class ScoringEngine:

    def __init__(self, claude: Optional[ClaudeClient] = None):
        self.claude = claude
        self.scoring_models = {
            "ICP1": SCORING_ICP1,
            "ICP2": SCORING_ICP2,
            "ICP3": SCORING_ICP3,
        }

    def score_lead(self, lead: dict) -> dict:
        """
        Calcula score do lead com base no ICP.
        Retorna lead atualizado com score, status (HOT/WARM/COLD), breakdown.
        """
        icp = lead.get("icp_tipo", "ICP1")
        model = self.scoring_models.get(icp, SCORING_ICP1)

        # Passo 1: Check de exclusão
        exclusion = self._check_exclusion(lead)
        if exclusion:
            lead["status"] = "excluded"
            lead["exclusion_reason"] = exclusion
            lead["score"] = 0
            return lead

        # Passo 2: Scoring determinístico
        score = 0
        breakdown = {}

        for dim_key, dim_config in model.items():
            dim_score = self._score_dimension(dim_key, dim_config, lead)
            breakdown[dim_key] = dim_score
            score += dim_score

        # Passo 3: Ajustes bonus
        bonus = self._calculate_bonus(lead)
        score += bonus
        if bonus > 0:
            breakdown["bonus"] = bonus

        # Clamp 0-100
        score = max(0, min(100, score))

        # Passo 4: Classificação
        status = self._classify_status(score)

        lead["score"] = score
        lead["status"] = status
        lead["score_breakdown"] = breakdown
        lead["score_icp"] = icp

        return lead

    def _score_dimension(self, dim_key: str, config: dict, lead: dict) -> int:
        """Score uma dimensão individual."""
        regras = config["regras"]

        # ─── ICP1 Dimensions ───
        if dim_key == "volume_entregadores":
            return self._score_volume_entregadores(lead, regras)

        if dim_key == "status_seguro":
            return self._score_seguro(lead, regras)

        if dim_key == "lei_14297_aplica":
            if lead.get("ai_plataforma_digital"):
                return regras.get("plataforma_digital", 15)
            return regras.get("operadora_fisica", 10)

        if dim_key == "decisor_mapeado":
            return self._score_decisor(lead, regras)

        if dim_key == "porte_receita":
            return self._score_porte(lead, regras)

        if dim_key == "sinal_dor":
            return self._score_sinal_dor(lead, regras)

        # ─── ICP2 Dimensions ───
        if dim_key == "volume_despachos":
            val = lead.get("ai_volume_despachos", "desconhecido")
            return regras.get(val, regras.get("desconhecido", 8))

        if dim_key == "integracao_api":
            val = lead.get("ai_integracao_api", "sem_api")
            return regras.get(val, 5)

        if dim_key == "posicao_despacho":
            val = lead.get("ai_posicao_despacho", "so_cotacao")
            return regras.get(val, 10)

        if dim_key == "mercado_alvo_sellers":
            val = lead.get("ai_mercado_sellers", "hibrido")
            return regras.get(val, 8)

        # ─── ICP3 Dimensions ───
        if dim_key == "num_transportadoras_clientes":
            val = lead.get("ai_num_clientes_tms", "desconhecido")
            return regras.get(val, 8)

        if dim_key == "integracao_rota_despacho":
            val = lead.get("ai_integracao_rota", "so_gestao")
            return regras.get(val, 10)

        if dim_key == "porte_transportadoras":
            val = lead.get("ai_porte_clientes", "misto")
            return regras.get(val, 15)

        if dim_key == "citado_reuniao_88i":
            empresas_citadas = config.get("empresas_citadas", [])
            nome = lead.get("nome", "")
            for citada in empresas_citadas:
                if citada.lower() in nome.lower():
                    return regras.get("sim", 15)
            return regras.get("nao", 0)

        if dim_key == "abertura_parcerias":
            val = lead.get("ai_abertura_parcerias", "verificar")
            return regras.get(val, 5)

        return 0

    # ───────────────────────────────────────────
    # DIMENSION SCORERS
    # ───────────────────────────────────────────

    def _score_volume_entregadores(self, lead: dict, regras: dict) -> int:
        est = lead.get("ai_entregadores_est", "")
        if not est:
            return regras.get("desconhecido", 8)

        try:
            # Parse ranges like "500-1000" or numbers like "50000"
            est_str = str(est).replace(",", "").replace(".", "").replace("+", "")
            if "-" in est_str:
                parts = est_str.split("-")
                num = int(parts[1])  # usar upper bound
            elif "k" in est_str.lower():
                num = int(float(est_str.lower().replace("k", "")) * 1000)
            else:
                num = int(est_str)

            if num >= 50000: return regras.get(">50k", 30)
            if num >= 10000: return regras.get("10k-50k", 20)
            if num >= 1000: return regras.get("1k-10k", 10)
            return regras.get("<1k", 5)
        except (ValueError, TypeError):
            return regras.get("desconhecido", 8)

    def _score_seguro(self, lead: dict, regras: dict) -> int:
        seguro = lead.get("ai_seguro_detectado", "desconhecido")
        mapping = {
            "nao": "zero",
            "sim": "completo",
            "parcial": "parcial",
            "basico": "basico",
            "desconhecido": "desconhecido",
        }
        return regras.get(mapping.get(seguro, "desconhecido"), 12)

    def _score_decisor(self, lead: dict, regras: dict) -> int:
        cargo = (lead.get("decisor_cargo") or lead.get("ai_decisor_sugerido") or "").lower()
        if any(k in cargo for k in ["ceo", "coo", "cfo", "fundador", "founder"]):
            return regras.get("ceo_coo_cfo", regras.get("ceo_cto_head_parcerias", 15))
        if any(k in cargo for k in ["diretor", "director", "vp", "vice"]):
            return regras.get("dir_vp", 12)
        if any(k in cargo for k in ["head", "gerente", "manager"]):
            return regras.get("gerente_head", 8)
        if any(k in cargo for k in ["product", "produto"]):
            return regras.get("product", 10)
        return regras.get("nao_mapeado", regras.get("nao_mapeado", 3))

    def _score_porte(self, lead: dict, regras: dict) -> int:
        porte = (lead.get("ai_porte") or "").lower()
        if porte == "grande": return regras.get(">100M", 10)
        if porte == "medio": return regras.get("10-100M", 7)
        if porte == "pequeno": return regras.get("1-10M", 4)
        if porte == "micro": return regras.get("<1M", 0)
        return regras.get("desconhecido", 3)

    def _score_sinal_dor(self, lead: dict, regras: dict) -> int:
        # Empresa nova sem seguro = forte sinal
        seguro = lead.get("ai_seguro_detectado", "")
        if seguro == "nao":
            return regras.get("empresa_nova_sem_seguro", 8)
        # TODO: check mídia/sinistros via web search
        return regras.get("nenhum", 0)

    # ───────────────────────────────────────────
    # BONUS & EXCLUSION
    # ───────────────────────────────────────────

    def _calculate_bonus(self, lead: dict) -> int:
        bonus = 0
        # Bonus: email/telefone verificado
        if lead.get("decisor_email"):
            bonus += 3
        if lead.get("decisor_telefone"):
            bonus += 2
        if lead.get("decisor_linkedin"):
            bonus += 2
        # Bonus: alta confiança AI
        if lead.get("ai_confianca", 0) > 0.8:
            bonus += 3
        return min(bonus, 10)  # cap bonus em 10

    def _check_exclusion(self, lead: dict) -> Optional[str]:
        """Verifica filtros de exclusão."""
        # AI já detectou risco
        if lead.get("ai_risco_exclusao"):
            return lead["ai_risco_exclusao"]
        # Sem entregadores (ICP1)
        if lead.get("icp_tipo") == "ICP1" and lead.get("ai_tem_entregadores") is False:
            return "Empresa sem entregadores — não é ICP1"
        return None

    def _classify_status(self, score: int) -> str:
        if score >= 70:
            return "HOT"
        if score >= 40:
            return "WARM"
        return "COLD"

    # ───────────────────────────────────────────
    # BATCH SCORING
    # ───────────────────────────────────────────

    def score_batch(self, leads: list[dict]) -> list[dict]:
        """Score e ordena batch de leads."""
        scored = [self.score_lead(lead) for lead in leads]
        scored.sort(key=lambda x: x.get("score", 0), reverse=True)

        # Stats
        hot = sum(1 for l in scored if l.get("status") == "HOT")
        warm = sum(1 for l in scored if l.get("status") == "WARM")
        cold = sum(1 for l in scored if l.get("status") == "COLD")
        excluded = sum(1 for l in scored if l.get("status") == "excluded")

        print(f"\n📊 SCORING COMPLETO:")
        print(f"   🔥 HOT: {hot}  |  ⚡ WARM: {warm}  |  ❄️ COLD: {cold}  |  ❌ Excluídos: {excluded}")
        print(f"   Top 5:")
        for l in scored[:5]:
            print(f"     {l.get('score', 0):3d} pts — {l.get('nome', '?')} [{l.get('status')}]")

        return scored
