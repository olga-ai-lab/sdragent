"""
Lead Merger — fusão de dados cross-source para o mesmo lead.

Quando a mesma empresa aparece tanto no LinkedIn quanto no Google Maps,
em vez de descartar uma das entradas fazemos merge dos campos,
priorizando a fonte mais confiável para cada tipo de dado.

Regras de prioridade:
  LinkedIn preferido  : linkedin_url, descricao_linkedin, employees_linkedin,
                        industry_linkedin, pais
  Google Maps preferido: site, telefone, endereco, rating_google,
                         reviews_count, categoria_google, place_id
  Maior valor wins    : score, ai_confianca
  Union               : sources (rastreia todas as origens)

Similaridade de nomes usa Jaccard de tokens (sem dependências externas).
"""

from __future__ import annotations

import re
from typing import Optional


# Campos que preferem o valor do LinkedIn quando disponível
_PREFER_LINKEDIN = {
    "linkedin_url", "descricao_linkedin", "employees_linkedin",
    "industry_linkedin",
}

# Campos que preferem o valor do Google Maps quando disponível
_PREFER_GOOGLE = {
    "site", "telefone", "endereco", "rating_google",
    "reviews_count", "categoria_google", "place_id",
}

# Campos onde vence o maior valor numérico
_TAKE_MAX = {"score", "ai_confianca", "web_pages_scraped"}

# Limiar de similaridade para considerar o mesmo lead (0-1)
SIMILARITY_THRESHOLD = 0.55


class LeadMerger:
    """
    Detecta e funde leads duplicados vindos de fontes diferentes.

    Uso típico:
        merger = LeadMerger()
        deduped = merger.merge_batch(all_leads)
    """

    def merge_batch(self, leads: list[dict]) -> list[dict]:
        """
        Recebe lista de leads (potencialmente de múltiplas fontes),
        detecta duplicatas e as funde.

        Returns: lista deduplicada e fundida.
        """
        merged_leads: list[dict] = []
        used: set[int] = set()

        for i, lead_a in enumerate(leads):
            if i in used:
                continue

            current = {**lead_a}
            used.add(i)

            for j, lead_b in enumerate(leads):
                if j <= i or j in used:
                    continue
                if self._same_company(current, lead_b):
                    current = self.merge(current, lead_b)
                    used.add(j)

            merged_leads.append(current)

        n_merged = len(leads) - len(merged_leads)
        if n_merged:
            print(f"  🔀 LeadMerger: {n_merged} duplicatas fundidas → {len(merged_leads)} leads únicos")

        return merged_leads

    def merge(self, lead_a: dict, lead_b: dict) -> dict:
        """
        Funde dois leads sobre a mesma empresa.

        lead_a é a base; lead_b complementa onde lead_a não tem dados.
        Campos com regras explícitas seguem a prioridade de fonte.
        """
        merged = {**lead_a}

        # 1. Campos que preferem LinkedIn (lead de qualquer fonte que tenha)
        for field in _PREFER_LINKEDIN:
            li_val = self._from_linkedin_source(field, lead_a, lead_b)
            if li_val is not None:
                merged[field] = li_val

        # 2. Campos que preferem Google Maps
        for field in _PREFER_GOOGLE:
            gm_val = self._from_google_source(field, lead_a, lead_b)
            if gm_val is not None:
                merged[field] = gm_val

        # 3. Campos numéricos: vence o maior
        for field in _TAKE_MAX:
            val_a = lead_a.get(field)
            val_b = lead_b.get(field)
            if val_a is not None and val_b is not None:
                try:
                    merged[field] = max(float(val_a), float(val_b))
                except (TypeError, ValueError):
                    pass
            elif val_b is not None and val_a is None:
                merged[field] = val_b

        # 4. Preenche campos vazios com valor de lead_b
        for key, val in lead_b.items():
            if key not in merged or not merged[key]:
                merged[key] = val

        # 5. Rastrear todas as fontes
        sources: set[str] = set()
        for lead in (lead_a, lead_b):
            raw = lead.get("source") or lead.get("sources") or ""
            if isinstance(raw, list):
                sources.update(raw)
            elif raw:
                sources.update(raw.split("+"))
        merged["source"] = "+".join(sorted(sources)) if sources else "unknown"
        merged["sources"] = list(sources)

        return merged

    # ───────────────────────────────────────────
    # DETECÇÃO DE DUPLICATA
    # ───────────────────────────────────────────

    def _same_company(self, lead_a: dict, lead_b: dict) -> bool:
        """
        Retorna True se os dois leads representam a mesma empresa.
        Critérios (OR lógico, do mais ao menos confiável):
          1. linkedin_url idêntico (normalizado)
          2. site idêntico (domínio)
          3. Similaridade de nome ≥ SIMILARITY_THRESHOLD
        """
        # LinkedIn URL exato
        li_a = self._norm_linkedin(lead_a.get("linkedin_url", ""))
        li_b = self._norm_linkedin(lead_b.get("linkedin_url", ""))
        if li_a and li_b and li_a == li_b:
            return True

        # Domínio do site
        dom_a = self._extract_domain(lead_a.get("site", ""))
        dom_b = self._extract_domain(lead_b.get("site", ""))
        if dom_a and dom_b and dom_a == dom_b:
            return True

        # Similaridade de nome
        sim = self._name_similarity(
            lead_a.get("nome", ""),
            lead_b.get("nome", ""),
        )
        return sim >= SIMILARITY_THRESHOLD

    # ───────────────────────────────────────────
    # HELPERS DE FONTE
    # ───────────────────────────────────────────

    def _from_linkedin_source(self, field: str, lead_a: dict, lead_b: dict) -> Optional[object]:
        """Retorna o valor do campo da fonte LinkedIn, ou None."""
        for lead in (lead_a, lead_b):
            if lead.get("source", "").startswith("linkedin") and lead.get(field):
                return lead[field]
        # Fallback: qualquer lead que tenha o campo
        for lead in (lead_a, lead_b):
            if lead.get(field):
                return lead[field]
        return None

    def _from_google_source(self, field: str, lead_a: dict, lead_b: dict) -> Optional[object]:
        """Retorna o valor do campo da fonte Google Maps, ou None."""
        for lead in (lead_a, lead_b):
            if "google" in lead.get("source", "") and lead.get(field):
                return lead[field]
        for lead in (lead_a, lead_b):
            if lead.get(field):
                return lead[field]
        return None

    # ───────────────────────────────────────────
    # SIMILARIDADE DE NOMES
    # ───────────────────────────────────────────

    def _name_similarity(self, name_a: str, name_b: str) -> float:
        """
        Similaridade Jaccard entre tokens dos nomes.
        Remove stopwords comuns, capitalização e pontuação.
        """
        tokens_a = self._tokenize(name_a)
        tokens_b = self._tokenize(name_b)
        if not tokens_a or not tokens_b:
            return 0.0
        intersection = tokens_a & tokens_b
        union = tokens_a | tokens_b
        return len(intersection) / len(union)

    _STOPWORDS = {"de", "da", "do", "das", "dos", "e", "the", "a", "o", "ltda", "eireli", "sa", "sas", "me", "epp"}

    def _tokenize(self, name: str) -> set[str]:
        name = re.sub(r"[^\w\s]", " ", name.lower())
        return {t for t in name.split() if t and t not in self._STOPWORDS and len(t) > 1}

    # ───────────────────────────────────────────
    # HELPERS DE URL
    # ───────────────────────────────────────────

    def _norm_linkedin(self, url: str) -> str:
        if not url:
            return ""
        return url.lower().rstrip("/").split("?")[0]

    def _extract_domain(self, url: str) -> str:
        if not url:
            return ""
        url = re.sub(r"^https?://", "", url.lower())
        url = re.sub(r"^www\.", "", url)
        return url.split("/")[0].split("?")[0]
