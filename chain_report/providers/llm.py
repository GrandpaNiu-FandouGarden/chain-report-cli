"""LLM provider abstraction.

The hosted providers implemented here use the OpenAI Chat Completions wire format.
This keeps the first CLI version small while still supporting DeepSeek, MiniMax,
OpenAI-compatible relays and Ollama.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

from chain_report.config import PROVIDER_SPECS, get_env_value


@dataclass
class LLMConfig:
    provider: str
    model: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    temperature: float = 0.2
    timeout: int = 180


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, config: LLMConfig):
        self.config = config

    def generate(self, prompt: str) -> str:
        provider = self.config.provider
        if provider == "mock":
            return self._mock_generate(prompt)
        return self._chat_completions(prompt)

    def _mock_generate(self, prompt: str) -> str:
        return """# 钢铁产业链研究周报（样例）

> 当前为 Mock 模型输出，用于验证 CLI 流程、配置读取和 Markdown 落盘；未调用真实大模型。

## 一、本周核心结论

- 钢铁产业链仍需围绕成材需求、原料成本、库存变化和政策扰动进行跟踪。
- 对供应链金融业务而言，重点不是简单判断价格涨跌，而是识别客户库存贬值、回款节奏和货权控制风险。

## 二、数据观察

- 已完成 Wind 数据接口调用流程占位。
- 后续接入真实 Wind 数据后，本节将按品种展示价格、库存、PMI 等指标变化。

## 三、业务风险提示

- 关注钢价快速下跌导致的抵质押货值波动。
- 关注客户账期拉长、库存积压和订单交付延迟。

## 四、后续动作建议

- 对重点客户补充库存、订单、回款闭环核查。
- 对价格波动较大的品种提高盯市频率。
"""

    def _chat_completions(self, prompt: str) -> str:
        spec = PROVIDER_SPECS.get(self.config.provider)
        if not spec:
            raise LLMError(f"Unsupported provider: {self.config.provider}")
        base_url = (self.config.base_url or spec.get("base_url") or "").rstrip("/")
        if not base_url:
            raise LLMError("base_url is required for this provider")
        api_key = self.config.api_key or get_env_value(spec.get("key_env"))
        if not api_key and not spec.get("key_optional"):
            raise LLMError(f"API key missing for provider: {self.config.provider}")
        if not api_key:
            api_key = "EMPTY"

        url = f"{base_url}/chat/completions"
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": "你是严谨的产业链研究报告助手。只根据给定数据写作，不编造数据。输出中文 Markdown。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": self.config.temperature,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="ignore")[:1000]
            raise LLMError(f"LLM HTTP {e.code}: {detail}") from e
        except Exception as e:
            raise LLMError(f"LLM request failed: {e}") from e

        try:
            data = json.loads(body)
            return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            raise LLMError(f"LLM response parse failed: {e}; body={body[:1000]}") from e
