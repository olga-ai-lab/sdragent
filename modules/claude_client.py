"""
Claude API Client — Wrapper com controle de custo e modelo por tarefa.
Regra de Ouro #2: modelo certo para cada tarefa.
"""

import json
import time
import httpx
from dataclasses import dataclass, field
from typing import Optional
from config.settings import ANTHROPIC_API_KEY, MODELS

# Custo por 1M tokens (input / output)
MODEL_COSTS = {
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
}

@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    calls: int = 0
    cost_usd: float = 0.0

    def add(self, model: str, inp: int, out: int, cache: int = 0):
        self.input_tokens += inp
        self.output_tokens += out
        self.cache_read += cache
        self.calls += 1
        costs = MODEL_COSTS.get(model, {"input": 3.0, "output": 15.0})
        self.cost_usd += (inp / 1_000_000 * costs["input"]) + (out / 1_000_000 * costs["output"])

    def report(self) -> dict:
        return {
            "total_calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read,
            "total_cost_usd": round(self.cost_usd, 4),
        }


class ClaudeClient:
    """
    Client para a API Anthropic com:
    - Seleção automática de modelo por task_type
    - Tracking de custo
    - Retry com backoff exponencial
    - Output forçado em JSON quando solicitado
    """

    BASE_URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str = ANTHROPIC_API_KEY):
        self.api_key = api_key
        self.usage = TokenUsage()
        self.client = httpx.Client(timeout=60.0)

    def call(
        self,
        task_type: str,
        prompt: str,
        system: str = "",
        max_tokens: int = 1024,
        json_output: bool = False,
        temperature: float = 0.0,
        retries: int = 3,
    ) -> dict | str:
        """
        Faz chamada ao Claude com modelo selecionado automaticamente.
        
        task_type: chave de MODELS (classifier, enrichment, outreach, scoring, strategy)
        json_output: se True, adiciona instrução de JSON e faz parse
        """
        model = MODELS.get(task_type, MODELS["classifier"])

        if json_output:
            system = (system + "\n\n" if system else "") + (
                "IMPORTANT: Respond ONLY with valid JSON. No markdown, no backticks, no preamble."
            )

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            payload["system"] = system

        for attempt in range(retries):
            try:
                resp = self.client.post(self.BASE_URL, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()

                # Track usage
                usage = data.get("usage", {})
                self.usage.add(
                    model,
                    usage.get("input_tokens", 0),
                    usage.get("output_tokens", 0),
                    usage.get("cache_read_input_tokens", 0),
                )

                # Extract text
                text = ""
                for block in data.get("content", []):
                    if block.get("type") == "text":
                        text += block["text"]

                if json_output:
                    text = text.strip().removeprefix("```json").removesuffix("```").strip()
                    return json.loads(text)

                return text

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    wait = 2 ** attempt
                    print(f"  ⏳ Rate limit — aguardando {wait}s...")
                    time.sleep(wait)
                    continue
                raise
            except json.JSONDecodeError:
                if attempt < retries - 1:
                    continue
                return text  # retorna texto bruto se JSON parse falhou

        raise RuntimeError(f"Claude API falhou após {retries} tentativas")

    def classify(self, text: str, categories: list[str], context: str = "") -> str:
        """Classificação simples — usa Haiku, max_tokens=50."""
        system = f"Classifique o texto em UMA das categorias: {', '.join(categories)}."
        if context:
            system += f"\nContexto: {context}"
        result = self.call("classifier", text, system=system, max_tokens=50)
        return result.strip()

    def extract_json(self, text: str, schema_hint: str, task_type: str = "enrichment") -> dict:
        """Extrai dados estruturados de texto livre."""
        prompt = f"Extraia os dados do texto abaixo no formato JSON:\n\n{text}\n\nEsquema esperado:\n{schema_hint}"
        return self.call(task_type, prompt, json_output=True, max_tokens=500)

    def personalize_message(self, template: str, lead_data: dict) -> str:
        """Personaliza mensagem de outreach com dados do lead."""
        system = (
            "Você é um SDR especialista em seguros para a 88i Seguradora Digital. "
            "Personalize a mensagem abaixo com os dados do lead. "
            "Mantenha o tom profissional mas direto. Máximo 500 caracteres. "
            "NÃO adicione emojis. NÃO use linguagem genérica de vendas."
        )
        prompt = f"Template:\n{template}\n\nDados do lead:\n{json.dumps(lead_data, ensure_ascii=False, indent=2)}"
        return self.call("outreach", prompt, system=system, max_tokens=300)

    def cost_report(self) -> dict:
        return self.usage.report()

    def close(self):
        self.client.close()
