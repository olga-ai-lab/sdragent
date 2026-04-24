"""
OlgaAI SDR Agent — Configuração Central
88i Seguradora Digital · Pipeline Last Mile Delivery
"""

import os
from dataclasses import dataclass, field
from typing import Optional


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}

# ═══════════════════════════════════════════════════════════════
# API KEYS (carregar de .env)
# ═══════════════════════════════════════════════════════════════

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
# Service-role key bypasses API allowlist and RLS — use only server-side
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
LUSHA_API_KEY = os.getenv("LUSHA_API_KEY", "")
APIFY_API_KEY = os.getenv("APIFY_API_KEY", "")
EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")
EVOLUTION_INSTANCE = os.getenv("EVOLUTION_INSTANCE", "")

# Feature flags — intelligence premium (defaults seguros)
ENABLE_WEB_RESEARCH = _env_bool("ENABLE_WEB_RESEARCH", False)
ENABLE_LUSHA_PERSON = _env_bool("ENABLE_LUSHA_PERSON", False)
ENABLE_LINKEDIN_PROFILE = _env_bool("ENABLE_LINKEDIN_PROFILE", False)
ENABLE_LINKEDIN_POSTS = _env_bool("ENABLE_LINKEDIN_POSTS", False)

# ═══════════════════════════════════════════════════════════════
# HUNT CONFIG — Apify actors para caça de leads
# ═══════════════════════════════════════════════════════════════

# Actor Apify para busca de empresas no LinkedIn por keyword
APIFY_ACTOR_LINKEDIN_COMPANIES = os.getenv(
    "APIFY_ACTOR_LINKEDIN_COMPANIES",
    "curious_coder/linkedin-company-search-scraper",
)

# Actor Apify para busca de perfis LinkedIn (L4)
APIFY_ACTOR_LINKEDIN_PROFILE = os.getenv(
    "APIFY_ACTOR_LINKEDIN_PROFILE",
    "dev_fusion~linkedin-profile-scraper",
)

# Actor Apify para posts LinkedIn (L5)
APIFY_ACTOR_LINKEDIN_POSTS = os.getenv(
    "APIFY_ACTOR_LINKEDIN_POSTS",
    "harvestapi~linkedin-profile-posts",
)

# Sources padrão para hunt (google_maps e/ou linkedin)
HUNT_DEFAULT_SOURCES = os.getenv("HUNT_DEFAULT_SOURCES", "google_maps,linkedin").split(",")

# Máximo de resultados por query no LinkedIn hunt
HUNT_LINKEDIN_MAX_RESULTS = int(os.getenv("HUNT_LINKEDIN_MAX_RESULTS", "50"))

# ─────────────────────────────────────────────────────────────────────────────
# LINKEDIN HUNT FILTERS — gerado via metodologia XLSX 88i_pipeline_v4_icp_2
# Fonte: Pipeline de 145 empresas mapeadas | ICP1=91 | ICP2=19 | ICP3=13
# Screenshot LinkedIn: query "delivery" + Brasil + Transporte/armazenamento
#   + 51-200 funcionários → expandido para cobrir todos os portes e ICPs
#
# companySize codes: A=1-10  B=11-50  C=51-200  D=201-500
#                   E=501-1k  F=1k-5k  G=5k-10k  H=10k+
# ─────────────────────────────────────────────────────────────────────────────
LINKEDIN_HUNT_FILTERS: list[dict] = [

    # ═══════════════════════════════════════════════════════════════════
    # ICP1 — PLATAFORMAS DELIVERY (AP Compulsório + Perda de Renda)
    # Ref: 91 empresas no pipeline | 21 HOT | 61 WARM | score médio 62
    # Empresas-alvo: Rappi, Keeta, 99Food, Mottu, Lalamove, Total Express,
    #   Jadlog, Shopee Logística, Zé Delivery, Uello, 99Entrega, Loggi...
    # ═══════════════════════════════════════════════════════════════════

    # ── Q01: Food delivery — plataformas médias e grandes ──────────────
    # Cobre: Rappi, Keeta, 99Food, aiqfome, James Delivery, Zé Delivery
    # Porte: Médio-Grande (201–10k+ func) — score médio ICP1 = 62
    {
        "_description": "Food delivery plataformas médias e grandes — tier HOT/WARM",
        "keywords": "food delivery plataforma entregadores parceiros",
        "location": "Brazil",
        "industry": [
            "Transportation, Logistics, Supply Chain and Storage",
            "Food and Beverage Services",
        ],
        "companySize": ["D", "E", "F", "G", "H"],
        "icp_tipo": "ICP1",
    },

    # ── Q02: Moto delivery / courier urbano — porte pequeno e médio ────
    # Cobre: Lalamove, Flash Courier, Jet Motoboy, Logmoto, Motoboy BR,
    #   Rapiddo, Zoom Entregas, Expresso Rápido, Logística Urbana
    # Porte: Pequeno-Médio (11–500 func) → pipeline tem 35 Pequenos em ICP1
    {
        "_description": "Moto delivery, motoboy, courier urbano — porte pequeno e médio",
        "keywords": "moto delivery motoboy courier urbano entregador",
        "location": "Brazil",
        "industry": [
            "Transportation, Logistics, Supply Chain and Storage",
        ],
        "companySize": ["B", "C", "D", "E"],
        "icp_tipo": "ICP1",
    },

    # ── Q03: Logtech last mile — transportadora e-commerce ─────────────
    # Cobre: Total Express, Jadlog, CB Full, ASAP Log, J&T Express,
    #   Carriers Logística, TudoEntregue, B2Log, Box Delivery, Send4
    # Porte: Médio-Grande (51–5k func)
    {
        "_description": "Logtech last mile e transportadora e-commerce",
        "keywords": "last mile logtech transportadora entrega expressas",
        "location": "Brazil",
        "industry": [
            "Transportation, Logistics, Supply Chain and Storage",
            "Technology, Information and Internet",
        ],
        "companySize": ["C", "D", "E", "F", "G"],
        "icp_tipo": "ICP1",
    },

    # ── Q04: On-demand app / plataforma motoristas autônomos ───────────
    # Cobre: 99Entrega Moto, Shippify, Logbee, Vuxx, Levoo, Eu Entrego,
    #   Mobibuzz, dLieve, Plataforma Delivery B2B
    # Baseado no filtro original da screenshot: "delivery" + Brasil
    {
        "_description": "Plataformas on-demand e motoristas autônomos — filtro base do screenshot",
        "keywords": "delivery app motoristas autônomos plataforma gig",
        "location": "Brazil",
        "industry": [
            "Transportation, Logistics, Supply Chain and Storage",
            "Technology, Information and Internet",
        ],
        "companySize": ["B", "C", "D", "E"],
        "icp_tipo": "ICP1",
    },

    # ── Q05: Operador logístico 3PL + multinacionais ───────────────────
    # Cobre: Sequoia Logística, Luft Logistics, FM Logistic, DHL Supply
    #   Chain, XPO Logistics, Direct Log, Tegma
    # Porte: Grande (1k–10k+ func)
    {
        "_description": "Operadores logísticos 3PL e multinacionais com frota last mile",
        "keywords": "operador logístico 3PL armazenagem distribuição last mile",
        "location": "Brazil",
        "industry": [
            "Transportation, Logistics, Supply Chain and Storage",
        ],
        "companySize": ["E", "F", "G", "H"],
        "icp_tipo": "ICP1",
    },

    # ── Q06: Marketplace + logística própria ───────────────────────────
    # Cobre: Shopee Logística, Shopee Express, Amazon DSP, Mercado Livre
    #   Logistics, Magazine Luiza Entregas
    # Porte: Grande (5k–10k+)
    {
        "_description": "Marketplace com logística própria e rede de entregadores parceiros",
        "keywords": "marketplace logística própria entregadores parceiros DSP",
        "location": "Brazil",
        "industry": [
            "Transportation, Logistics, Supply Chain and Storage",
            "Technology, Information and Internet",
        ],
        "companySize": ["F", "G", "H"],
        "icp_tipo": "ICP1",
    },

    # ── Q07: Locadora de motos / micromobilidade ────────────────────────
    # Cobre: Mottu (130k motos / 40k entregadores), Tembici (45k e-bikes),
    #   Pedala, Ecobike Courier, Ecobike Cargo
    # Nicho de alto potencial: seguro do VEÍCULO ≠ AP do entregador
    {
        "_description": "Locadora motos entregadores e micromobilidade — gap AP x seguro veículo",
        "keywords": "locadora motos entregadores aluguel moto frota bike courier",
        "location": "Brazil",
        "industry": [
            "Transportation, Logistics, Supply Chain and Storage",
        ],
        "companySize": ["B", "C", "D", "E", "F"],
        "icp_tipo": "ICP1",
    },

    # ── Q08: Quick commerce / dark store / supermercado online ─────────
    # Cobre: Daki, Shopper, James Delivery (Q-Commerce Boticário)
    # Operações de entrega ultrarrápida com entregadores próprios
    {
        "_description": "Quick commerce, dark store, delivery ultrarrápido com entregadores",
        "keywords": "quick commerce dark store delivery rápido supermercado online",
        "location": "Brazil",
        "industry": [
            "Food and Beverage Services",
            "Transportation, Logistics, Supply Chain and Storage",
        ],
        "companySize": ["C", "D", "E", "F"],
        "icp_tipo": "ICP1",
    },

    # ── Q09: Courier especializado (saúde, farmácia, malotes) ──────────
    # Cobre: Delivery Laboratorial SP, Entregas Farmacêuticas Urgentes,
    #   Transporte SAMU/Saúde, Transporte Malotes Corporativos
    # Motoboys especializados = alto risco de sinistro
    {
        "_description": "Courier especializado saúde, farmácia e malotes — motoboys alto risco",
        "keywords": "delivery farmacêutico laboratorial urgente motoboy saúde",
        "location": "Brazil",
        "industry": [
            "Hospitals and Health Care",
            "Transportation, Logistics, Supply Chain and Storage",
        ],
        "companySize": ["A", "B", "C", "D"],
        "icp_tipo": "ICP1",
    },

    # ── Q10: Cargo aéreo + last mile / transportadoras regionais ───────
    # Cobre: Gollog (GOL), Azul Cargo Express, Braspress, Patrus,
    #   Jamef, Rodonaves, CB Logística
    # Transportadoras com frota própria e entregadores expostos
    {
        "_description": "Cargo aéreo + last mile e transportadoras regionais com frota",
        "keywords": "cargo aéreo transportadora regional frota entregadores carga fracionada",
        "location": "Brazil",
        "industry": [
            "Transportation, Logistics, Supply Chain and Storage",
        ],
        "companySize": ["D", "E", "F", "G", "H"],
        "icp_tipo": "ICP1",
    },


    # ═══════════════════════════════════════════════════════════════════
    # ICP2 — EMBARCADORES / GERADORA DE ETIQUETA (Mercadoria Last Mile)
    # Ref: 19 empresas no pipeline | 6 HOT | 12 WARM | score médio 68
    # Empresas-alvo: Bling, Nuvemshop, Olist, VTEX, Mandaê, Melhor Envio,
    #   Frenet, Tiny ERP, Anymarket, Kangu, Posta Já
    # ═══════════════════════════════════════════════════════════════════

    # ── Q11: ERP e-commerce com módulo de etiqueta/frete ───────────────
    # Cobre: Bling ERP, Tiny ERP, Olist (Hub+ERP), Skyhub/TOTVS
    # Geram etiqueta → ponto de integração ad valorem 0,88%
    {
        "_description": "ERP e-commerce com etiqueta e frete — Bling, Tiny, Olist tier",
        "keywords": "ERP e-commerce etiqueta envio gestão loja online",
        "location": "Brazil",
        "industry": [
            "Software Development",
            "Technology, Information and Internet",
        ],
        "companySize": ["C", "D", "E", "F", "G"],
        "icp_tipo": "ICP2",
    },

    # ── Q12: Plataforma e-commerce + gateway frete ─────────────────────
    # Cobre: Nuvemshop, VTEX, Shopify Brasil, Anymarket, Frete Rápido,
    #   Melhor Envio, Frenet, Mandaê, Backlogi
    # Integração API no checkout = seguro ad valorem automático
    {
        "_description": "Plataforma e-commerce e gateway frete — integração API checkout",
        "keywords": "plataforma e-commerce marketplace frete integração envio",
        "location": "Brazil",
        "industry": [
            "Technology, Information and Internet",
            "Software Development",
        ],
        "companySize": ["B", "C", "D", "E", "F", "G", "H"],
        "icp_tipo": "ICP2",
    },

    # ── Q13: Agência franqueada Correios / PUDO / pontos coleta ────────
    # Cobre: Posta Já, Correios (Agências Franqueadas), Kangu
    # Porte pequeno mas alto volume de despachos
    {
        "_description": "Agências franqueadas Correios, PUDO e pontos de coleta",
        "keywords": "agência franqueada Correios despacho encomenda ponto coleta PUDO",
        "location": "Brazil",
        "industry": [
            "Transportation, Logistics, Supply Chain and Storage",
        ],
        "companySize": ["A", "B", "C", "D"],
        "icp_tipo": "ICP2",
    },


    # ═══════════════════════════════════════════════════════════════════
    # ICP3 — TMS / SOFTWARE DE GESTÃO (Canal Parceiro Mercadoria)
    # Ref: 13 empresas no pipeline | 3 HOT | 10 WARM | score médio 63
    # HOT: Pick and Go, Gaudium/Machine, Intelipost
    # WARM: RoutEasy, Logcomex, Rotafácil, Brinelog, Devilry, LogPlan
    # ═══════════════════════════════════════════════════════════════════

    # ── Q14: TMS roteirização PME ──────────────────────────────────────
    # Cobre: Pick and Go, Rotafácil, RoutEasy, Brinelog
    # PME transportadoras = canal parceiro para seguro mercadoria
    {
        "_description": "TMS roteirização para PME transportadoras — Pick and Go, RoutEasy tier",
        "keywords": "TMS roteirização transportadora software gestão entregas",
        "location": "Brazil",
        "industry": [
            "Software Development",
            "Technology, Information and Internet",
        ],
        "companySize": ["A", "B", "C", "D"],
        "icp_tipo": "ICP3",
    },

    # ── Q15: TMS gestão frete / frota / last mile ──────────────────────
    # Cobre: Intelipost, Gaudium/Machine, Logcomex, Devilry, LogPlan,
    #   Translogística, Flixlog
    # Integração TMS → seguro automático na emissão de CT-e/DACTE
    {
        "_description": "TMS gestão frete e frota — Intelipost, Gaudium, Logcomex tier",
        "keywords": "TMS gestão frete frota logística software SaaS transportadora",
        "location": "Brazil",
        "industry": [
            "Software Development",
            "Technology, Information and Internet",
            "Transportation, Logistics, Supply Chain and Storage",
        ],
        "companySize": ["B", "C", "D", "E"],
        "icp_tipo": "ICP3",
    },
]

# ═══════════════════════════════════════════════════════════════
# MODELOS CLAUDE — Regra de Ouro #2: modelo certo pra tarefa
# ═══════════════════════════════════════════════════════════════

MODELS = {
    "classifier": "claude-haiku-4-5-20251001",     # triagem, classificação → $0.80/1M
    "enrichment": "claude-haiku-4-5-20251001",      # extração de dados web → barato
    "outreach": "claude-sonnet-4-20250514",         # personalização de mensagens → qualidade
    "scoring": "claude-haiku-4-5-20251001",         # scoring com regras → determinístico + IA fallback
    "strategy": "claude-sonnet-4-20250514",         # decisões de abordagem → precisa raciocínio
}

# ═══════════════════════════════════════════════════════════════
# PRODUTOS 88i
# ═══════════════════════════════════════════════════════════════

PRODUTOS_88I = {
    "ap_compulsorio": {
        "nome": "Acidentes Pessoais (AP) Compulsório",
        "lei": "Lei 14.297/2022",
        "multa": "R$1.000 por evento de alocação sem seguro",
        "coberturas": [
            "Morte Acidental",
            "Invalidez Permanente (parcial/total)",
            "DMHO — Despesa Médico-Hospitalar Odontológica",
            "DITA — Diária de Incapacidade Temporária",
            "Auxílio Funeral",
        ],
        "hook": "A Lei 14.297 exige AP desde o primeiro entregador. Cada evento sem cobertura = R$1.000 de multa. A 88i ativa em 48h via API.",
    },
    "perda_renda": {
        "nome": "Perda de Renda (B2B2C Facultativo)",
        "modalidades": {
            "lucro_cessante_mei": {
                "desc": "DITA + Furto/Roubo/Colisão automóvel ou celular",
                "ticket": "R$9,90 – R$19,90/mês (1.000 km/mês)",
                "publico": "Motoristas app, entregadores com moto própria, MEI",
            },
            "basico": {
                "desc": "Apenas DITA — diária durante incapacidade por acidente",
                "ticket": "Menor ticket — via PLG/push notification",
                "publico": "Trabalhadores autônomos, garçons, domésticas, faz-tudo",
            },
            "premium": {
                "desc": "AP exclusivo + DITA + Impedimento ao Trabalho (colisão automóvel)",
                "ticket": "Ticket mais alto — melhor LTV",
                "publico": "Motoristas app premium, autônomos alta frequência",
            },
        },
        "hook": "Por menos de R$1/dia, você protege até R$150/dia da sua renda. Só paga enquanto trabalha. Um toque no app.",
    },
    "mercadoria_last_mile": {
        "nome": "Seguro Mercadoria Last Mile (B2B2C Facultativo)",
        "taxa": "Ad valorem 0,88% (vs 1% Correios = 12% mais barato)",
        "beneficio": "Sem averbação, sem escolta, sem nota fiscal",
        "hook": "0,88% ad valorem vs 1% dos Correios. 12% mais barato. Sem papel, sem seguradora tradicional. Integração API em 1 dia.",
    },
}

# ═══════════════════════════════════════════════════════════════
# ICP DEFINITIONS
# ═══════════════════════════════════════════════════════════════

ICP_DEFINITIONS = {
    "ICP1": {
        "nome": "Plataformas com entregadores — AP Compulsório + Perda de Renda",
        "produto": ["ap_compulsorio", "perda_renda"],
        "abordagem": "B2B Compulsório (Lei 14.297)",
        "canal": "AE dedicado + LinkedIn CEO/COO + SDR IA sequência 14 dias",
        "segmentos": [
            "Food Delivery (entregadores)",
            "Moto delivery / Motoboy",
            "Motoristas de aplicativo",
            "Quick commerce",
            "Marketplace (sellers como operadores)",
            "Locadora motos entregadores",
        ],
        "cnaes": ["5320-2/02", "4930-2/01", "4930-2/02", "6391-7/00", "6203-1/00"],
        "apify_queries": [
            "empresa entrega motoboy delivery",
            "courier urbano entrega rápida",
            "operadora last mile logística",
            "transporte amostras laboratorial",
            "delivery farmacêutico urgente motoboy",
        ],
    },
    "ICP2": {
        "nome": "Embarcadores / Geradora de Etiqueta — Mercadoria Last Mile",
        "produto": ["mercadoria_last_mile"],
        "abordagem": "B2B2C Facultativo — seguro no checkout",
        "canal": "API integration + Parceria plataformas e-commerce",
        "segmentos": [
            "Geradora de etiqueta / ERP e-commerce",
            "Agência franqueada Correios",
            "Plataformas e-commerce (embarcadores)",
        ],
        "cnaes": ["4712-1/00", "4711-3/02"],
        "apify_queries": [
            "agência franqueada correios",
            "despacho encomendas e-commerce etiqueta",
        ],
    },
    "ICP3": {
        "nome": "TMS / Software de Gestão — Canal Parceiro Mercadoria",
        "produto": ["mercadoria_last_mile"],
        "abordagem": "B2B2C Facultativo — parceria canal",
        "canal": "Parceria tech — API + marketplace",
        "segmentos": [
            "TMS para PME transportadoras",
            "Software de roteirização",
        ],
        "cnaes": [],
        "apify_queries": [
            "sistema gestão transporte transportadora",
            "software roteirização entrega PME",
        ],
    },
}

# ═══════════════════════════════════════════════════════════════
# SCORING MODELS (0-100 pts)
# ═══════════════════════════════════════════════════════════════

SCORING_ICP1 = {
    "volume_entregadores": {
        "peso": 30,
        "regras": {
            ">50k": 30, "10k-50k": 20, "1k-10k": 10, "<1k": 5, "desconhecido": 8
        },
        "obrigatorio": True,
    },
    "status_seguro": {
        "peso": 20,
        "regras": {
            "zero": 20, "basico": 15, "parcial": 10, "completo": 0, "desconhecido": 12
        },
    },
    "lei_14297_aplica": {
        "peso": 15,
        "regras": {
            "plataforma_digital": 15, "operadora_fisica": 10, "nao_aplica": 0
        },
    },
    "decisor_mapeado": {
        "peso": 15,
        "regras": {
            "ceo_coo_cfo": 15, "dir_vp": 12, "gerente_head": 8, "nao_mapeado": 3
        },
    },
    "porte_receita": {
        "peso": 10,
        "regras": {
            ">100M": 10, "10-100M": 7, "1-10M": 4, "<1M": 0, "desconhecido": 3
        },
    },
    "sinal_dor": {
        "peso": 10,
        "regras": {
            "sinistro_midia": 10, "empresa_nova_sem_seguro": 8, "alto_risco": 5, "nenhum": 0
        },
    },
}

SCORING_ICP2 = {
    "volume_despachos": {
        "peso": 30,
        "regras": {
            ">1M/mes": 30, "100k-1M": 20, "10k-100k": 12, "<10k": 5, "desconhecido": 8
        },
    },
    "integracao_api": {
        "peso": 25,
        "regras": {
            "api_publica_doc": 25, "api_restrita": 15, "sem_api": 5
        },
    },
    "posicao_despacho": {
        "peso": 20,
        "regras": {
            "gera_etiqueta": 20, "so_cotacao": 10, "so_rastreio": 0
        },
    },
    "decisor_mapeado": {
        "peso": 15,
        "regras": {
            "ceo_cto_head_parcerias": 15, "product": 10, "nao_mapeado": 3
        },
    },
    "mercado_alvo_sellers": {
        "peso": 10,
        "regras": {
            "marketplaces": 10, "hibrido": 8, "ecommerce_proprio": 6
        },
    },
}

SCORING_ICP3 = {
    "num_transportadoras_clientes": {
        "peso": 30,
        "regras": {
            ">500": 30, "100-500": 20, "<100": 10, "desconhecido": 8
        },
    },
    "integracao_rota_despacho": {
        "peso": 25,
        "regras": {
            "gera_docs_transporte": 25, "so_gestao": 10
        },
    },
    "porte_transportadoras": {
        "peso": 20,
        "regras": {
            "pme_focado": 20, "misto": 15, "enterprise": 10
        },
    },
    "citado_reuniao_88i": {
        "peso": 15,
        "regras": {
            "sim": 15, "nao": 0
        },
        "empresas_citadas": ["Pick and Go", "Gaudium", "Machine", "Intelipost"],
    },
    "abertura_parcerias": {
        "peso": 10,
        "regras": {
            "api_aberta_marketplace": 10, "verificar": 5, "nao": 0
        },
    },
}

# ═══════════════════════════════════════════════════════════════
# FILTROS DE EXCLUSÃO — NÃO ENTRA NO PIPELINE
# ═══════════════════════════════════════════════════════════════

EXCLUSION_FILTERS = [
    {"tipo": "Farmácia / Supermercado", "motivo": "Compra entrega — não opera"},
    {"tipo": "SaaS de roteirização puro", "motivo": "Não tem entregadores"},
    {"tipo": "Gateway de frete puro", "motivo": "Sem entrega própria"},
    {"tipo": "Transportadora carga pesada", "motivo": "Caminhão — regulação diferente"},
    {"tipo": "Cargo aéreo puro", "motivo": "Sem trecho terrestre last mile"},
    {"tipo": "ERP sem módulo frete/etiqueta", "motivo": "Sem ponto de integração"},
    {"tipo": "Empregador CLT", "motivo": "Trabalhador tem INSS — outro produto"},
]

# ═══════════════════════════════════════════════════════════════
# CIDADES ALVO — Apify Google Maps
# ═══════════════════════════════════════════════════════════════

CIDADES_ALVO = [
    "São Paulo", "Rio de Janeiro", "Belo Horizonte", "Curitiba",
    "Porto Alegre", "Fortaleza", "Goiânia", "Campinas",
    "Salvador", "Recife", "Brasília", "Florianópolis",
]

# ═══════════════════════════════════════════════════════════════
# APIs EXTERNAS
# ═══════════════════════════════════════════════════════════════

API_ENDPOINTS = {
    "brasil_api_cnpj": "https://brasilapi.com.br/api/cnpj/v1/{cnpj}",
    "lusha_enrich": "https://api.lusha.com/v1/person/enrich",
    "apify_google_maps": "https://api.apify.com/v2/acts/drobnikj~crawler-google-places/runs",
    "evolution_send_text": "{evolution_url}/message/sendText/{instance}",
    "evolution_send_media": "{evolution_url}/message/sendMedia/{instance}",
}

# ═══════════════════════════════════════════════════════════════
# CADÊNCIA DE OUTREACH (14 dias)
# ═══════════════════════════════════════════════════════════════

@dataclass
class OutreachStep:
    dia: int
    canal: str
    tipo: str
    template_key: str
    descricao: str

CADENCIA_SDR = [
    OutreachStep(1, "linkedin", "connection", "linkedin_connect", "Convite LinkedIn personalizado"),
    OutreachStep(1, "whatsapp", "intro", "whatsapp_intro", "Mensagem introdutória WhatsApp"),
    OutreachStep(3, "whatsapp", "followup_1", "whatsapp_valor", "Follow-up com proposta de valor"),
    OutreachStep(5, "email", "case_study", "email_case", "Email com case de sucesso"),
    OutreachStep(7, "whatsapp", "followup_2", "whatsapp_dados", "Follow-up com dados do mercado"),
    OutreachStep(10, "linkedin", "message", "linkedin_artigo", "Mensagem LinkedIn com artigo/insight"),
    OutreachStep(12, "whatsapp", "urgencia", "whatsapp_urgencia", "Mensagem de urgência/escassez"),
    OutreachStep(14, "whatsapp", "breakup", "whatsapp_breakup", "Mensagem final — breakup email"),
]

# ═══════════════════════════════════════════════════════════════
# SUPABASE TABLES
# ═══════════════════════════════════════════════════════════════

SUPABASE_TABLES = {
    "leads": "companies_88i_pipeline",
    "outreach_log": "sdr_outreach_log",
    "enrichment_cache": "sdr_enrichment_cache",
    "meetings": "sdr_meetings_booked",
    "lead_intelligence": "sdr_lead_intelligence",
    "lead_events": "sdr_lead_events",
}

# ═══════════════════════════════════════════════════════════════
# CLIENTES ATIVOS (não abordar como prospect)
# ═══════════════════════════════════════════════════════════════

CLIENTES_ATIVOS_88I = ["iFood", "Uber Flash", "Loggi"]

# Empresas citadas na reunião 88i como descartadas (SaaS puro)
DESCARTADOS = ["RoutEasy", "Melhor Envio", "Intelipost", "Frenet"]
