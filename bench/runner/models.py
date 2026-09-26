#!/usr/bin/env python3
"""bench.runner.models — 基准用的模型目录（含价格，成本核算离线可算）。

价格来源：OpenRouter `/api/v1/models`（2026-09-26 拉取），存为静态表——
理由是**可复现**：跑批当时的价格与今日价格可能不同，静态快照让历史结果可追溯。
改价请更新 `PRICES_FETCHED` 并重跑对照。

provider 通道：
- "openrouter"  https://openrouter.ai/api/v1（一个 key 多模型）
- "deepseek"    直连 https://api.deepseek.com（备用通道）
- "ark"         火山方舟（Ark）coding plan，OpenAI 兼容端点
                https://ark.cn-beijing.volces.com/api/coding/v3
- "dashscope"   阿里云百炼 OpenAI 兼容端点（聚合多家：qwen/kimi/glm/minimax…）
                https://dashscope.aliyuncs.com/compatible-mode/v1

**计费口径（重要）**：
- `billing="per_token"`：`cost()` 是真实美元支出，price 必须为正
- `billing="subscription"`：订阅额度，边际成本 0（方舟 coding plan）
- `billing="free_quota"`：免费额度内边际成本 0，烧完即停（百炼三方模型）
  ——两种 0 成本口径在报表里都**不得**显示为 $0（会被读成「免费」而非
  「边际成本为 0」），可比量是 token 用量
"""

from dataclasses import dataclass

PRICES_FETCHED = "2026-09-26"
PRICES_SOURCE = "https://openrouter.ai/api/v1/models"


@dataclass(frozen=True)
class ModelSpec:
    alias: str          # 基准内短名（结果表用）
    provider: str       # "openrouter" | "deepseek" | "ark"
    model: str          # 供应商侧模型 id
    family: str         # 家族（判断 judge 是否与被评模型同族）
    price_in: float     # 美元 / 百万 input token
    price_out: float    # 美元 / 百万 output token
    max_tokens: int = 4096
    temperature: float = 0.85
    billing: str = "per_token"   # "per_token"（按量）| "subscription"（订阅）

    def cost(self, tokens_in: int, tokens_out: int) -> float:
        """一次调用的美元成本（订阅/免费额度通道恒为 0，边际成本）。"""
        if self.billing in ("subscription", "free_quota"):
            return 0.0
        return (tokens_in * self.price_in + tokens_out * self.price_out) / 1_000_000.0


# 通道状态：跑批前先看这里。**不要**到跑批中途才发现额度墙——
# 首轮实测就是这么丢掉 kimi 的（6 章跑到第 3 章撞 402，留下一个「部分完成」的废行）。
CHANNELS = {
    "openrouter": {
        "available": False,
        "note": "额度耗尽（402），2026-09-26 起停用；历史结果仍有效（manifest 记了当时通道）",
    },
    "deepseek": {"available": True, "note": "直连，备用通道"},
    "ark": {
        "available": True,
        "note": "火山方舟 coding plan（订阅制，边际成本 0）。"
                "注意：本账号无 Kimi 通道——kimi-k2.x 在方舟上返回 UnsupportedModel",
    },
    "dashscope": {
        "available": False,   # 2026-09-26 晚实测：账号 Arrearage（欠费墙），全模型拒答
        "note": "阿里云百炼（聚合渠道）：qwen 免费额度 + 三方模型按厂商各发免费额度。"
                "实测可用 kimi-k3（k2.6 免费额度已耗尽）；MiniMax/deepseek-v4 三方额度"
                "也已耗尽；stepfun 未开通。⚠️ 当前账号欠费待处理，处理后置回 True",
    },
}


def channel_status(provider: str) -> dict:
    return CHANNELS.get(provider, {"available": False, "note": "未知通道"})


# 目录：跨家族、跨价位（约 50× 价差），全部中文可用
CATALOG = {
    # ── OpenRouter 通道（已停用，保留别名以记录历史 roster）──────────
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

    # ── 火山方舟 coding plan 通道（订阅制，2026-09-26 逐个实测可用）────
    # 实测不可用（UnsupportedModel）：glm-4-7、qwen3-*、qwen2-5-*、
    # doubao-seed-1-6/1-8、doubao-seed-2-0-lite。别照抄方舟全量模型表。
    # max_tokens 给大：方舟的思考型模型把 reasoning token 也算进 completion
    # （实测 doubao-seed-2-1-pro 写 867 字烧掉 1139 个 reasoning token），
    # 给小了正文会被截断。
    "ark-db-pro": ModelSpec("ark-db-pro", "ark", "doubao-seed-2-1-pro-260915",
                            "doubao", 0.0, 0.0, max_tokens=8192,
                            billing="subscription"),
    "ark-db-turbo": ModelSpec("ark-db-turbo", "ark", "doubao-seed-2-1-turbo-260628",
                              "doubao", 0.0, 0.0, max_tokens=8192,
                              billing="subscription"),
    "ark-db-lite": ModelSpec("ark-db-lite", "ark", "doubao-seed-2-1-lite-260915",
                             "doubao", 0.0, 0.0, max_tokens=8192,
                             billing="subscription"),
    "ark-db-mini": ModelSpec("ark-db-mini", "ark", "doubao-seed-2-0-mini-260428",
                             "doubao", 0.0, 0.0, max_tokens=8192,
                             billing="subscription"),
    "ark-db-code": ModelSpec("ark-db-code", "ark", "doubao-seed-2-0-code-preview-260215",
                             "doubao", 0.0, 0.0, max_tokens=8192,
                             billing="subscription"),
    "ark-glm-flash": ModelSpec("ark-glm-flash", "ark", "glm-5-3-flash-260828",
                               "glm", 0.0, 0.0, max_tokens=16384,
                               billing="subscription"),
    "ark-glm": ModelSpec("ark-glm", "ark", "glm-5-2-260617",
                         "glm", 0.0, 0.0, max_tokens=16384,
                         billing="subscription"),
    "ark-ds-flash": ModelSpec("ark-ds-flash", "ark", "deepseek-v4-1-flash-260910",
                              "deepseek", 0.0, 0.0, max_tokens=8192,
                              billing="subscription"),
    "ark-ds-pro": ModelSpec("ark-ds-pro", "ark", "deepseek-v4-pro-ga-260813",
                            "deepseek", 0.0, 0.0, max_tokens=8192,
                            billing="subscription"),

    # ── 阿里云百炼通道（免费额度，2026-09-26 逐个实测可用）──────────────
    # 聚合渠道的价值：一个 key 补齐方舟没有的厂商（moonshot/qwen 官方直营）。
    # 实测不可用：kimi-k2.6 / MiniMax-M2.5 / deepseek-v4-flash（免费额度耗尽）、
    # stepfun（未开通）。免费额度按模型独立计算，烧完即停——跑批前先探活。
    # ⚠️ 2026-09-26 晚：本账号全模型 Arrearage（欠费墙，qwen/glm/kimi 全拒答），
    # 通道标停用，待账务处理后翻回 True。
    # ⚠️ kimi-k3 实测拒 temperature=0.85（InvalidParameter），范围待校准，暂取 0.7。
    "dash-qwen-flash": ModelSpec("dash-qwen-flash", "dashscope",
                                 "qwen3.7-flash-2026-07-15",
                                 "qwen", 0.0, 0.0, max_tokens=8192,
                                 billing="free_quota"),
    "dash-qwen-max": ModelSpec("dash-qwen-max", "dashscope",
                               "qwen3.7-max-2026-06-08",
                               "qwen", 0.0, 0.0, max_tokens=8192,
                               billing="free_quota"),
    "dash-kimi-k3": ModelSpec("dash-kimi-k3", "dashscope", "kimi-k3",
                              "moonshot", 0.0, 0.0, max_tokens=8192,
                              temperature=0.7, billing="free_quota"),
}


def get(alias: str) -> ModelSpec:
    if alias not in CATALOG:
        raise KeyError(f"未知模型别名: {alias}（可选: {sorted(CATALOG)}）")
    return CATALOG[alias]


def resolve(aliases) -> list:
    return [get(a) for a in aliases]


def families(aliases) -> set:
    return {get(a).family for a in aliases}