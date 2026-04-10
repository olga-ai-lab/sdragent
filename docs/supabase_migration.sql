-- ═══════════════════════════════════════════════════════════
-- SDR Agent 88i — Supabase Migration
-- Projeto: CRM (rwwmnotyyetwrvqfdcem)
-- OlgaAI · Março 2026
-- ═══════════════════════════════════════════════════════════

-- 1. Pipeline de empresas
CREATE TABLE IF NOT EXISTS companies_88i_pipeline (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    empresa_id      TEXT UNIQUE,
    nome            TEXT NOT NULL,
    site            TEXT,
    cnpj            TEXT,
    segmento        TEXT,
    icp_tipo        TEXT CHECK (icp_tipo IN ('ICP1', 'ICP2', 'ICP3')),
    status          TEXT DEFAULT 'discovered' CHECK (status IN ('discovered', 'enriched', 'HOT', 'WARM', 'COLD', 'excluded', 'contacted', 'meeting_booked', 'won', 'lost')),
    tier            TEXT,
    porte           TEXT,
    cidade          TEXT,
    uf              TEXT,
    score           INTEGER DEFAULT 0,
    score_breakdown JSONB,
    score_icp       TEXT,

    -- Dados de contato / decisor
    decisor_nome    TEXT,
    decisor_cargo   TEXT,
    decisor_email   TEXT,
    decisor_telefone TEXT,
    decisor_linkedin TEXT,
    telefone        TEXT,

    -- Dados de seguro
    seguro_atual    TEXT,
    seguradora_parceira TEXT,
    gap_oportunidade TEXT,

    -- Dados de entregadores
    entregadores_est TEXT,

    -- AI enrichment
    ai_segmento         TEXT,
    ai_tem_entregadores BOOLEAN,
    ai_entregadores_est TEXT,
    ai_porte            TEXT,
    ai_plataforma_digital BOOLEAN,
    ai_seguro_detectado TEXT,
    ai_formato_email    TEXT,
    ai_risco_exclusao   TEXT,
    ai_confianca        FLOAT,
    enrichment_complete BOOLEAN DEFAULT FALSE,
    exclusion_reason    TEXT,

    -- Metadados
    source          TEXT,
    place_id        TEXT,
    produto_88i     TEXT,
    obs_estrategica TEXT,
    proxima_acao    TEXT,

    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_pipeline_status ON companies_88i_pipeline(status);
CREATE INDEX IF NOT EXISTS idx_pipeline_icp ON companies_88i_pipeline(icp_tipo);
CREATE INDEX IF NOT EXISTS idx_pipeline_score ON companies_88i_pipeline(score DESC);

-- 2. Log de outreach
CREATE TABLE IF NOT EXISTS sdr_outreach_log (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    empresa_id  TEXT NOT NULL,
    canal       TEXT NOT NULL CHECK (canal IN ('whatsapp', 'email', 'linkedin', 'phone')),
    tipo        TEXT,
    mensagem    TEXT,
    status      TEXT DEFAULT 'sent' CHECK (status IN ('sent', 'delivered', 'read', 'replied', 'bounced', 'error', 'simulated', 'prepared', 'no_phone', 'no_email', 'dry_run')),
    sent_at     TIMESTAMPTZ DEFAULT now(),
    response_at TIMESTAMPTZ,
    response    TEXT
);

CREATE INDEX IF NOT EXISTS idx_outreach_empresa ON sdr_outreach_log(empresa_id);
CREATE INDEX IF NOT EXISTS idx_outreach_sent ON sdr_outreach_log(sent_at DESC);

-- 3. Cache de enriquecimento (Regra de Ouro #7)
CREATE TABLE IF NOT EXISTS sdr_enrichment_cache (
    id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    cache_key   TEXT UNIQUE NOT NULL,
    data        JSONB NOT NULL,
    source      TEXT,
    cached_at   TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cache_key ON sdr_enrichment_cache(cache_key);

-- 4. Reuniões agendadas
CREATE TABLE IF NOT EXISTS sdr_meetings_booked (
    id              UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    empresa_id      TEXT NOT NULL,
    decisor         TEXT,
    data_reuniao    TIMESTAMPTZ,
    notas           TEXT,
    status          TEXT DEFAULT 'agendada' CHECK (status IN ('agendada', 'confirmada', 'realizada', 'cancelada', 'no_show')),
    booked_at       TIMESTAMPTZ DEFAULT now(),
    outcome         TEXT
);

-- 5. RLS (Row Level Security)
ALTER TABLE companies_88i_pipeline ENABLE ROW LEVEL SECURITY;
ALTER TABLE sdr_outreach_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE sdr_enrichment_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE sdr_meetings_booked ENABLE ROW LEVEL SECURITY;

-- Policies para service_role (agent roda com service key)
CREATE POLICY "service_full_access" ON companies_88i_pipeline FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "service_full_access" ON sdr_outreach_log FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "service_full_access" ON sdr_enrichment_cache FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "service_full_access" ON sdr_meetings_booked FOR ALL USING (true) WITH CHECK (true);

-- 6. View: KPIs do pipeline
CREATE OR REPLACE VIEW vw_sdr_pipeline_kpis AS
SELECT
    icp_tipo,
    status,
    COUNT(*) AS total,
    ROUND(AVG(score), 1) AS avg_score,
    MAX(score) AS max_score,
    SUM(CASE WHEN decisor_email IS NOT NULL THEN 1 ELSE 0 END) AS com_email,
    SUM(CASE WHEN decisor_linkedin IS NOT NULL THEN 1 ELSE 0 END) AS com_linkedin
FROM companies_88i_pipeline
WHERE status != 'excluded'
GROUP BY icp_tipo, status
ORDER BY icp_tipo, CASE status
    WHEN 'HOT' THEN 1
    WHEN 'WARM' THEN 2
    WHEN 'COLD' THEN 3
    ELSE 4
END;
