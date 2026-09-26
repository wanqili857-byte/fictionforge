#!/usr/bin/env python3
"""bench.runner.models — 基准用的模型目录（含价格，成本核算离线可算）。

价格来源：OpenRouter `/api/v1/models`（2026-09-26 拉取），存为静态表——
理由是**可复现**：跑批当时的价格与今日价格可能不同，静态快照让历史结果可追溯。
改价请更新 `PRICES_FETCHED` 并重跑对照。

provider "openrouter" 走 https://openrouter.ai/api/v1（一个 key 多模型）；
provider "deepseek" 直连 https://api.deepseek.com（备用通道）。
"""

from dataclasses import dataclass

PRICES_FETCHED = "2026-09-26"
PRICES_SOURCE = "https://openrouter.ai/api/v1/models"


@dataclass(frozen=True)
class ModelSpec:
    alias: str          # 基准内短名（结果表用）
    provider: str       # "openrouter" | "deepseek"
    model: str          # 供应商侧模型 id
    family: str         # 家族（判断 judge 是否与被评模型同族）
    price_in: float     # 美元 / 百万 input token
    price_out: float    # 美元 / 百万 output token
    max_tokens: int = 4096
    temperature: float = 0.85

    def cost(self, tokens_in: int, tokens_out: int) -> float:
        """一次调用的美元成本。"""
        return (tokens_in * self.price_in + tokens_out * self.price_out) / 1_000_000.0


# 目录：跨家族、跨价位（约 50× 价差），全部中文可用
CATALOG = {
    "qwen-flash": ModelSpec("qwen-flash", "openrouter", "qwen/qwen3.7-flash",
                            "qwen", 0.03, 0.13),
    "glm-flash": ModelSpec("glm-flash", "openrouter", "z-ai/glm-5.3-flash",
                           "glm", 0.04, 0.50),
    "ds-flash": ModelSpec("ds-flash", "openrouter", "deepseek/deepseek-v4-flash",
                          "deepseek", 0.05, 0.09),
    "minimax": ModelSpec("minimax", "openrouter", "minimax/minimax-m2",
                         "minimax", 0.30, 1.20),
    "glm": ModelSpec("glm", "openrouter", "z-ai/glm-5.3", "glm", 0.38, 1.19),
    "kimi": ModelSpec("kimi", "openrouter", "moonshotai/kimi-k2.6",
                      "moonshot", 0.95, 4.00),
    "qwen-max": ModelSpec("qwen-max", "openrouter", "qwen/qwen3.7-max",
                          "qwen", 1.48, 4.42),
}


def get(alias: str) -> ModelSpec:
    if alias not in CATALOG:
        raise KeyError(f"未知模型别名: {alias}（可选: {sorted(CATALOG)}）")
    return CATALOG[alias]


def resolve(aliases) -> list:
    return [get(a) for a in aliases]


def families(aliases) -> set:
    return {get(a).family for a in aliases}