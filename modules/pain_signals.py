"""
Pain Signal Detector — detecta sinais de dor ativos no lead.

Alimenta _score_sinal_dor no ScoringEngine (10 pts).

Hierarquia de sinais (maior → menor):
  sinistro_midia       → 10 pts  (acidente na mídia ou confirmado sem seguro em plataforma)
  empresa_nova_sem_seguro → 8 pts  (< 2 anos de operação, seguro não detectado)
  alto_risco           → 5 pts   (tem entregadores, seguro incerto)
  nenhum               → 0 pts
"""

import re
from datetime import datetime, timezone
from typing import Optional


# Palavras que indicam sinistro / acidente na mídia ou no conteúdo da empresa
_SINISTRO_KEYWORDS = [
    "acidente", "sinistro", "falecimento", "morte", "óbito", "atropelamento",
    "ação trabalhista", "indenização", "multa anvisa", "fiscalização", "autuação",
    "trabalhador morreu", "entregador morreu", "rider morreu",
]

# Palavras que indicam operação de risco com entregadores
_RISCO_KEYWORDS = [
    "motoboy", "motociclista", "entregador", "delivery", "courier",
    "rider", "bike messenger", "last mile", "última milha",
]


class PainSignalDetector:
    """
    Detecta sinais de dor em um lead usando apenas dados já coletados
    (sem chamadas externas adicionais).

    Dados usados (quando disponíveis):
    - ai_seguro_detectado       → Claude AI enrichment
    - ai_plataforma_digital     → Claude AI enrichment
    - ai_tem_entregadores       → Claude AI enrichment
    - data_inicio_atividade     → BrasilAPI CNPJ
    - cnpj_data_inicio          → alias alternativo
    - descricao_linkedin        → LinkedIn discovery
    - web_content               → WebScraper
    - categoria_google          → Google Maps
    - linkedin_posts            → IntelligenceEngine L5
    - noticias_recentes         → IntelligenceEngine L1
    - contexto_estrategico      → IntelligenceEngine L1
    """

    def detect(self, lead: dict) -> dict:
        """
        Retorna dict com:
          sinal   : "sinistro_midia" | "empresa_nova_sem_seguro" | "alto_risco" | "nenhum"
          score   : int (0-10)
          motivo  : str descritivo
        """
        seguro = lead.get("ai_seguro_detectado", "desconhecido")
        plataforma = lead.get("ai_plataforma_digital", False)
        tem_entregadores = lead.get("ai_tem_entregadores")

        # ── Sinal máximo: sinistro confirmado na mídia/conteúdo ───────────
        sinistro_motivo = self._detect_sinistro(lead)
        if sinistro_motivo:
            return {
                "sinal": "sinistro_midia",
                "score": 10,
                "motivo": sinistro_motivo,
            }

        # ── Plataforma digital confirmada sem seguro ───────────────────────
        if seguro == "nao" and plataforma:
            return {
                "sinal": "sinistro_midia",
                "score": 10,
                "motivo": "Plataforma digital com entregadores e sem seguro confirmado",
            }

        # ── Empresa nova (< 2 anos) sem seguro ───────────────────────────
        empresa_nova = self._is_empresa_nova(lead)
        if empresa_nova and seguro in ("nao", "desconhecido"):
            label = empresa_nova
            return {
                "sinal": "empresa_nova_sem_seguro",
                "score": 8,
                "motivo": f"Empresa nova ({label}) sem seguro confirmado — risco de não conformidade",
            }

        # ── Seguro não detectado (sem informação de data) ─────────────────
        if seguro == "nao":
            return {
                "sinal": "empresa_nova_sem_seguro",
                "score": 8,
                "motivo": "Seguro não detectado no site/LinkedIn da empresa",
            }

        # ── Alto risco: operação com entregadores, seguro incerto ─────────
        if tem_entregadores and seguro == "desconhecido":
            return {
                "sinal": "alto_risco",
                "score": 5,
                "motivo": "Empresa com entregadores mas cobertura de seguro não verificada",
            }

        # ── Operação de risco detectada por palavras-chave ────────────────
        risco_texto = self._detect_risco_keywords(lead)
        if risco_texto and seguro != "sim":
            return {
                "sinal": "alto_risco",
                "score": 5,
                "motivo": risco_texto,
            }

        return {"sinal": "nenhum", "score": 0, "motivo": "Sem sinais claros de dor identificados"}

    # ───────────────────────────────────────────
    # DETECÇÃO DE SINISTRO
    # ───────────────────────────────────────────

    def _detect_sinistro(self, lead: dict) -> Optional[str]:
        """Procura menção a acidente/sinistro em qualquer campo de texto."""
        corpus = self._build_corpus(lead)

        for kw in _SINISTRO_KEYWORDS:
            if kw in corpus:
                return f"Menção a '{kw}' detectada no conteúdo da empresa"

        # LinkedIn posts são especialmente relevantes
        posts = lead.get("linkedin_posts") or lead.get("recent_posts") or []
        for post in posts:
            text = (post.get("text") or "").lower()
            for kw in _SINISTRO_KEYWORDS:
                if kw in text:
                    return f"Post LinkedIn menciona '{kw}'"

        return None

    # ───────────────────────────────────────────
    # DETECÇÃO DE EMPRESA NOVA
    # ───────────────────────────────────────────

    def _is_empresa_nova(self, lead: dict, max_anos: float = 2.0) -> Optional[str]:
        """
        Retorna string descritiva se empresa foi fundada há menos de max_anos anos,
        None caso contrário ou se dados insuficientes.
        """
        cnpj_obj = lead.get("cnpj")
        raw_date = (
            lead.get("data_inicio_atividade")
            or lead.get("cnpj_data_inicio")
            or (cnpj_obj.get("data_inicio_atividade") if isinstance(cnpj_obj, dict) else None)
        )
        if not raw_date:
            return None

        try:
            inicio = datetime.strptime(str(raw_date)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            hoje = datetime.now(timezone.utc)
            anos = (hoje - inicio).days / 365.25
            if anos <= max_anos:
                meses = int(anos * 12)
                return f"{meses} meses de operação" if meses < 12 else f"{anos:.1f} anos de operação"
        except (ValueError, TypeError):
            pass

        return None

    # ───────────────────────────────────────────
    # DETECÇÃO POR KEYWORDS DE RISCO
    # ───────────────────────────────────────────

    def _detect_risco_keywords(self, lead: dict) -> Optional[str]:
        """Detecta operação de risco por palavras-chave no corpus do lead."""
        corpus = self._build_corpus(lead)
        encontradas = [kw for kw in _RISCO_KEYWORDS if kw in corpus]
        if len(encontradas) >= 2:
            return f"Operação com entregadores detectada: {', '.join(encontradas[:3])}"
        return None

    # ───────────────────────────────────────────
    # HELPERS
    # ───────────────────────────────────────────

    def _build_corpus(self, lead: dict) -> str:
        """Constrói corpus de texto de todos os campos relevantes."""
        fields = [
            lead.get("descricao_linkedin", ""),
            lead.get("web_content", ""),
            lead.get("categoria_google", ""),
            lead.get("ai_segmento", ""),
            lead.get("industry_linkedin", ""),
            lead.get("noticias_recentes", ""),
            lead.get("contexto_estrategico", ""),
            lead.get("nome", ""),
        ]
        return " ".join(str(f) for f in fields if f).lower()
