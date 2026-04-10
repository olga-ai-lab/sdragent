-- SDR Agent 88i — Migration incremental para camada de intelligence + eventos webhook

-- 1) Tabela consolidada de intelligence por lead
CREATE TABLE IF NOT EXISTS sdr_lead_intelligence (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    empresa_id TEXT UNIQUE NOT NULL,
    intelligence JSONB NOT NULL DEFAULT '{}'::jsonb,
    closing_intelligence JSONB,
    score INTEGER,
    score_breakdown JSONB,
    deal_value_est NUMERIC,
    deal_value_premissas TEXT,
    canal_recomendado TEXT,
    updated_at TIMESTAMPTZ DEFAULT now(),
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sdr_lead_intelligence_empresa ON sdr_lead_intelligence(empresa_id);
CREATE INDEX IF NOT EXISTS idx_sdr_lead_intelligence_score ON sdr_lead_intelligence(score DESC);

-- 2) Eventos webhook brutos + idempotência
CREATE TABLE IF NOT EXISTS sdr_lead_events (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    empresa_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    event_type TEXT NOT NULL,
    external_event_id TEXT NOT NULL,
    phone_normalized TEXT,
    payload_raw JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(provider, external_event_id)
);

CREATE INDEX IF NOT EXISTS idx_sdr_lead_events_empresa ON sdr_lead_events(empresa_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sdr_lead_events_phone ON sdr_lead_events(phone_normalized);

-- 3) Alterações incrementais na tabela de leads (não destrutivas)
ALTER TABLE companies_88i_pipeline
    ADD COLUMN IF NOT EXISTS last_interaction_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_interaction_channel TEXT,
    ADD COLUMN IF NOT EXISTS followup_paused BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS next_best_action JSONB;

-- 4) RLS/policies alinhadas com service_role
ALTER TABLE sdr_lead_intelligence ENABLE ROW LEVEL SECURITY;
ALTER TABLE sdr_lead_events ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'sdr_lead_intelligence' AND policyname = 'service_full_access'
    ) THEN
        CREATE POLICY "service_full_access" ON sdr_lead_intelligence FOR ALL USING (true) WITH CHECK (true);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'sdr_lead_events' AND policyname = 'service_full_access'
    ) THEN
        CREATE POLICY "service_full_access" ON sdr_lead_events FOR ALL USING (true) WITH CHECK (true);
    END IF;
END $$;
