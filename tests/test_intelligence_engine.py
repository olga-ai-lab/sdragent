import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from modules.intelligence_engine import IntelligenceEngine


class DummyClaude:
    def call(self, *args, **kwargs):
        return "{}"


def test_extract_json_malformed_fenced():
    raw = """texto\n```json\n{\n  \"descricao\": \"Empresa [1]\",\n  \"tem_api\": \"sim\",\n}\n```"""
    parsed = IntelligenceEngine.extract_json(raw)
    assert parsed is not None
    assert parsed["descricao"] == "Empresa [1]"


def test_extract_linkedin_person_url():
    text = "Perfil encontrado: https://www.linkedin.com/in/jose-silva-12345/ com cargo CEO"
    assert IntelligenceEngine._extract_linkedin_person_url(text) == "https://www.linkedin.com/in/jose-silva-12345"
    assert IntelligenceEngine._extract_linkedin_person_url("sem link") is None


def test_normalize_posts_and_profile(monkeypatch):
    engine = IntelligenceEngine(claude=DummyClaude())
    engine.enable_linkedin_profile = True
    engine.enable_linkedin_posts = True

    monkeypatch.setattr("modules.intelligence_engine.APIFY_API_KEY", "token")

    async def fake_run_apify(actor_id, payload, timeout):
        if "posts" not in actor_id:
            return [{
                "fullName": "Maria Souza",
                "headline": "CEO",
                "experiences": [{"role": "CEO", "companyName": "Acme", "dateRange": "2021-atual"}],
                "educations": [{"schoolName": "USP", "degreeName": "MBA"}],
                "followers": 500,
            }]
        return [{"text": "Post 1", "numLikes": 10, "postedAt": "2026-01-01", "url": "u1", "author": {"name": "Maria"}}]

    monkeypatch.setattr(engine, "_run_apify", fake_run_apify)

    profile = asyncio.run(engine.l4_linkedin_profile("https://linkedin.com/in/maria"))
    posts = asyncio.run(engine.l5_linkedin_posts("https://linkedin.com/in/maria"))

    assert profile is not None
    assert profile["experience"][0]["title"] == "CEO"
    assert profile["education"][0]["school"] == "USP"
    assert posts and posts[0]["likes"] == 10


def test_l3_missing_credentials_returns_none(monkeypatch):
    engine = IntelligenceEngine(claude=DummyClaude())
    engine.enable_lusha_person = True
    monkeypatch.setattr("modules.intelligence_engine.LUSHA_API_KEY", "")

    result = asyncio.run(engine.l3_person_lookup({"decisor_nome": "João Silva", "site": "acme.com"}))
    assert result is None


def test_pipeline_partial_data(monkeypatch):
    engine = IntelligenceEngine(claude=DummyClaude())

    async def _none(*args, **kwargs):
        return None

    async def _posts(*args, **kwargs):
        return [{"text": "hello", "likes": 1, "postedAt": "2026-01-01"}]

    monkeypatch.setattr(engine, "l1_web_research", _none)
    monkeypatch.setattr(engine, "l1b_linkedin_search", _none)
    monkeypatch.setattr(engine, "l2_cnpj_lookup", _none)
    monkeypatch.setattr(engine, "l2b_company_lookup", _none)
    monkeypatch.setattr(engine, "l3_person_lookup", _none)
    monkeypatch.setattr(engine, "l4_linkedin_profile", _none)
    monkeypatch.setattr(engine, "l5_linkedin_posts", _posts)
    monkeypatch.setattr(engine, "l7_generate", lambda lead, ctx: None)

    lead = {"nome": "Acme", "icp_tipo": "ICP1", "decisor_linkedin": "https://linkedin.com/in/acme"}
    report = asyncio.run(engine.generate_intelligence(lead))

    assert report is not None
    assert "source_breakdown" in report
    assert isinstance(report["recent_posts"], list)


def test_build_full_intelligence_report_wrapper(monkeypatch):
    engine = IntelligenceEngine(claude=DummyClaude())
    expected = {"source": "full_l1_l7", "score": 77}
    monkeypatch.setattr(engine, "generate_intelligence_sync", lambda lead: expected)

    report = engine.build_full_intelligence_report({"nome": "Acme"})
    assert report == expected


def test_build_intelligence_alias(monkeypatch):
    engine = IntelligenceEngine(claude=DummyClaude())
    expected = {"source": "full_l1_l7"}
    monkeypatch.setattr(engine, "build_full_intelligence_report", lambda lead: expected)

    report = engine.build_intelligence({"nome": "Acme"})
    assert report == expected
