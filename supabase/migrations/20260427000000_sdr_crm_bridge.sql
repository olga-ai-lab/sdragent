-- Migration: sdr_crm_bridge
-- Adds bridge columns to leads_bamaq so the SDR agent can write to the
-- canonical CRM table (Pipeline 6 – Vendas IA) using empresa_id as the
-- dedup key.

ALTER TABLE public.leads_bamaq
  ADD COLUMN IF NOT EXISTS empresa_id           TEXT,
  ADD COLUMN IF NOT EXISTS sdr_status           TEXT DEFAULT 'discovered',
  ADD COLUMN IF NOT EXISTS ai_enrichment        JSONB,
  ADD COLUMN IF NOT EXISTS sdr_score            INTEGER,
  ADD COLUMN IF NOT EXISTS sdr_score_breakdown  JSONB;

-- Unique index so PostgREST merge-duplicates works on empresa_id
CREATE UNIQUE INDEX IF NOT EXISTS leads_bamaq_empresa_id_key
  ON public.leads_bamaq (empresa_id)
  WHERE empresa_id IS NOT NULL;

-- Belt-and-suspenders: service_role policy for server-side writes
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE tablename = 'leads_bamaq' AND policyname = 'service_role_all'
  ) THEN
    EXECUTE 'CREATE POLICY service_role_all ON public.leads_bamaq
             FOR ALL TO service_role USING (true) WITH CHECK (true)';
  END IF;
END $$;
