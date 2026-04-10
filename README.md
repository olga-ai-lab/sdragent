# SDR Agent 88i — OlgaAI

**Agente SDR 100% Python para prospecção de seguros Last Mile Delivery.**
**Produção-ready. Sem n8n, sem Make, sem Zapier.**

Pipeline: Discovery → Scraping → Enrichment → Scoring → Outreach → Webhook → Meeting

---

## Arquitetura

```
main.py                     ← Entry point: FastAPI + Scheduler + CLI
├── config/settings.py      ← ICP, produtos, scoring, APIs, cadência
├── modules/
│   ├── claude_client.py    ← Claude API (custo/modelo por tarefa)
│   ├── discovery.py        ← Apify Google Maps + BrasilAPI CNPJ
│   ├── scraper.py          ← Web scraper async (BeautifulSoup)
│   ├── enrichment.py       ← Lusha + Claude AI (sync)
│   ├── async_enrichment.py ← Pipeline async (scraper+Lusha+Claude paralelo)
│   ├── scoring.py          ← Modelo 0-100 pts (regras + IA fallback)
│   ├── outreach.py         ← WhatsApp (Z-API) + templates personalizados
│   ├── email_client.py     ← Email real via SMTP
│   ├── webhook_server.py   ← FastAPI endpoints (Z-API callbacks)
│   ├── scheduler.py        ← APScheduler (cadência automática)
│   ├── state_machine.py    ← Ciclo de vida do lead (transições protegidas)
│   ├── supabase_client.py  ← CRM persistence
│   └── logger.py           ← Logging JSON estruturado + rotação
├── tests/test_core.py      ← 19 testes (scoring, state, classifier)
├── docs/supabase_migration.sql
├── Dockerfile
├── docker-compose.yml
└── orchestrator.py         ← CLI legado (versão v1)
```

---

## Quick Start

```bash
# 1. Clone e configure
cp .env.example .env
# Editar .env com suas chaves

# 2. Criar tabelas no Supabase
# Rodar docs/supabase_migration.sql no SQL Editor do Supabase

# 3. Rodar testes
pip install -r requirements.txt
pytest tests/ -v

# 4. Modo desenvolvimento (dry run)
python main.py run --mode full --dry-run --no-lusha

# 5. Produção — server + scheduler
python main.py serve

# 6. Docker
docker compose up -d
```

---

## 3 Modos de Operação

### 1. Server (produção)
```bash
python main.py serve --port 8000
```
Sobe o FastAPI + Scheduler. O agente roda sozinho:
- **9h seg-sex**: executa próximo step da cadência para cada lead
- **Segunda 6h**: discovery semanal de novos leads
- **A cada 30min**: health check (Supabase + Claude API)
- **24/7**: webhook escutando respostas do WhatsApp

### 2. CLI (execução manual)
```bash
# Pipeline completo
python main.py run --mode full --icps ICP1,ICP2

# Só outreach
python main.py run --mode outreach --status HOT --dia 3 --limit 10
```

### 3. Docker
```bash
docker compose up -d
docker logs sdr-agent-88i -f
```

---

## Endpoints da API

| Método | Endpoint | O que faz |
|--------|----------|-----------|
| POST | `/webhooks/zapi/receive` | Recebe mensagens do WhatsApp (Z-API) |
| POST | `/webhooks/zapi/status` | Status de entrega (sent/delivered/read) |
| GET | `/webhooks/health` | Health check |
| POST | `/api/trigger/{job_id}` | Trigger manual de job do scheduler |
| GET | `/api/pipeline/status` | KPIs do pipeline (HOT/WARM/contacted) |
| POST | `/api/outreach/execute` | Executa outreach via API |

---

## Pipeline Async (5x mais rápido)

O enrichment v2 roda em paralelo:
```
Lead → ┬─ Web Scraper (async)  ─┐
       ├─ Lusha API (async)     ├─ Claude AI (sync) → Score → Persist
       └─ Cache check           ─┘

500 leads sync:  ~25 min (2-3s/lead)
500 leads async: ~5 min  (0.6s/lead)
```

---

## State Machine

```
discovered → enriched → HOT ─→ contacted → replied → meeting_booked → won
                       WARM ─→            → no_response → nurture
                       COLD ─→                          → archived
                                                        → lost → (reativar)
```
Transições protegidas — código impede `discovered → won` ou `won → HOT`.

---

## Cadência SDR (14 dias) — Automática

| Dia | Canal | Ação |
|-----|-------|------|
| 1 | LinkedIn | Convite personalizado |
| 1 | WhatsApp | Mensagem introdutória |
| 3 | WhatsApp | Follow-up com proposta de valor |
| 5 | Email | Case de sucesso (SMTP real) |
| 7 | WhatsApp | Dados do mercado |
| 10 | LinkedIn | Insight/artigo |
| 12 | WhatsApp | Urgência/escassez |
| 14 | WhatsApp | Breakup message |

O Scheduler executa automaticamente cada step no dia correto.
Webhook captura respostas e atualiza status em tempo real.

---

## Webhook WhatsApp — Fluxo de Resposta

```
Lead responde no WhatsApp
  → Z-API envia webhook para /webhooks/zapi/receive
    → Identifica lead pelo telefone
      → Classifica intenção (interested/not_interested/info_request/redirect)
        → Atualiza status: contacted → replied
          → Log no Supabase
```

Classificação de intenção usa regras fixas (Regra de Ouro #3) — sem IA.

---

## Custos Estimados

| Componente | Custo | Obs |
|------------|-------|-----|
| Claude (Haiku) | ~$0.001/lead | Enrichment + scoring |
| Claude (Sonnet) | ~$0.003/lead | Personalização outreach |
| Apify Maps | $20-40 total | 500+ empresas |
| Lusha | $50-150/mês | Email + telefone decisores |
| BrasilAPI | Gratuito | CNPJ + CNAEs |
| Z-API WhatsApp | R$100/mês | Envio + recebimento |
| VPS/Railway | ~$5-10/mês | Docker container |
| **Total mensal** | **~$100-200/mês** | Operação contínua |

---

## Deploy em Produção

### Railway (recomendado)
```bash
# 1. Push para GitHub
git init && git add . && git commit -m "SDR Agent 88i v1.0"
git remote add origin https://github.com/olga-ai/sdr-agent-88i.git
git push -u origin main

# 2. No Railway: New Project → Deploy from GitHub
# 3. Adicionar variáveis de ambiente (.env)
# 4. Configurar webhook URL no Z-API: https://SEU-APP.railway.app/webhooks/zapi/receive
```

### Fly.io
```bash
fly launch
fly secrets set ANTHROPIC_API_KEY=sk-ant-xxx SUPABASE_URL=https://...
fly deploy
```

### VPS (Ubuntu)
```bash
git clone https://github.com/olga-ai/sdr-agent-88i.git
cd sdr-agent-88i
cp .env.example .env && nano .env
docker compose up -d
```

---

## Comparativo: Python Agent vs n8n

| Dimensão | n8n Workflow | Python Agent |
|----------|-------------|--------------|
| **Roda sozinho** | Sim (triggers) | Sim (scheduler + webhook) |
| **Controle de custo IA** | Limitado | Total (modelo/token por tarefa) |
| **Velocidade enrichment** | Síncrono | Async 5x mais rápido |
| **Webhook listener** | Node dedicado | FastAPI nativo |
| **State machine** | Não tem | Transições protegidas |
| **Testes** | Manual | 19 pytest automatizados |
| **Deploy** | Precisa n8n server | Docker em qualquer VPS |
| **Versionamento** | JSON difícil | Git nativo |
| **Custo infra** | n8n Cloud €20+/mês | VPS $5-10/mês |
| **Manutenção** | Re-design visual | Refactor código |
| **Logs** | UI do n8n | JSON estruturado + rotação |
| **Escalabilidade** | Limitada pela UI | Sem limite |
| **Ideal para** | MVPs, não-devs | Produção, times técnicos |

---

## Scoring Model (0-100 pts)

### ICP1 — AP Compulsório

| Dimensão | Peso | Critério máximo |
|----------|------|-----------------|
| Volume entregadores | 30 | ≥50k = 30 pts |
| Status seguro atual | 20 | Zero cobertura = 20 pts |
| Lei 14.297 aplica | 15 | Plataforma digital = 15 pts |
| Decisor mapeado | 15 | CEO/COO/CFO = 15 pts |
| Porte/Receita | 10 | >R$100M = 10 pts |
| Sinal de dor | 10 | Sinistro na mídia = 10 pts |
| **Bonus** | +10 | Email + telefone + LinkedIn verificados |

**HOT** ≥ 70 pts · **WARM** ≥ 40 pts · **COLD** < 40 pts
