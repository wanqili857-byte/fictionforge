"""
env_state.py — 环境状态（Tier 4，纯规则）。

管控：
  - 时间推进（日夜循环）
  - 天气（受区域变异等级影响）
  - 地点可通行性（道路封锁 / 电网状态 / 通信状态）
  - 物资可用性（商店货架 / 食物 / 水）
  - 恐慌聚合状态（来自 vault + 世界模拟器输出的补充）

不调用 LLM。所有规则基于 vault 数据和前馈参数。
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TimeState:
    """当前时间状态。"""
    absolute_day: int = 1
    hour: float = 8.0       # 24h 制
    season: str = "夏末"     # 夏末 / 秋初 / 不确定
    is_night: bool = False

    def advance(self, hours: float):
        """推进时间。更新日夜状态。"""
        new_hour = self.hour + hours
        if new_hour >= 24:
            days_to_add = int(new_hour // 24)
            self.absolute_day += days_to_add
            new_hour = new_hour % 24
        self.hour = new_hour
        self.is_night = self.hour < 6.0 or self.hour >= 19.0

    def day_period(self) -> str:
        """返回时间段描述。"""
        if self.hour < 6:
            return "凌晨"
        elif self.hour < 9:
            return "早晨"
        elif self.hour < 12:
            return "上午"
        elif self.hour < 14:
            return "中午"
        elif self.hour < 17:
            return "下午"
        elif self.hour < 19:
            return "傍晚"
        else:
            return "夜晚"

    def fmt(self) -> str:
        return f"第{self.absolute_day}天 {self.day_period()} ({self.hour:.0f}:00)"


@dataclass
class LocationState:
    """单个地点状态。"""
    name: str
    accessible: bool = True
    power: str = "正常"        # 正常 / 闪烁 / 中断
    communication: str = "正常"  # 正常 / 延迟 / 中断
    mutation_level: int = 0    # 0-3
    anomaly_active: list[str] = field(default_factory=list)
    panic_level: int = 0       # 0-10
    supply_status: str = "充足"  # 充足 / 紧张 / 短缺 / 空


@dataclass
class WeatherState:
    """天气状态。"""
    condition: str = "晴"      # 晴 / 多云 / 阴 / 小雨 / 大雨 / 雾 / 风暴
    wind: str = "微风"
    temperature: str = "28°C"

@dataclass
class ResourceState:
    """物资状态。"""
    food: str = "充足"         # 充足 / 紧张 / 短缺
    water: str = "正常"        # 正常 / 浑浊 / 断供
    fuel: str = "正常"         # 正常 / 紧张 / 短缺
    medical: str = "充足"
    notes: list[str] = field(default_factory=list)


@dataclass
class EnvState:
    """环境状态聚合。所有字段有默认值，构造时根据 vault 参数覆盖。"""
    time: TimeState = field(default_factory=TimeState)
    weather: WeatherState = field(default_factory=WeatherState)
    locations: dict[str, LocationState] = field(default_factory=dict)
    resources: ResourceState = field(default_factory=ResourceState)

    # 聚合恐慌指数（0-10），来自世界模拟器输出
    aggregated_panic: int = 0

    # 当前所在全局阶段
    story_stage: str = "缓冲期（异常初现）"

    def get_location(self, name: str) -> LocationState:
        """获取地点状态，不存在则创建。"""
        if name not in self.locations:
            self.locations[name] = LocationState(name=name)
        return self.locations[name]

    def apply_region_effects(self, region_effects: dict):
        """从 vault 的 region_effects 数据更新各地点状态。"""
        for region_name, effects in region_effects.items():
            loc = self.get_location(region_name)
            if isinstance(effects, dict):
                loc.mutation_level = effects.get("mutation_level", 0)
                loc.anomaly_active = effects.get("active_anomalies", [])
                # 根据变异等级推断基础设施
                if loc.mutation_level >= 3:
                    loc.power = "中断"
                    loc.communication = "中断"
                    loc.accessible = False
                elif loc.mutation_level >= 2:
                    loc.communication = "延迟"
                if loc.mutation_level >= 1:
                    loc.supply_status = "紧张"
                    loc.panic_level = min(10, loc.mutation_level * 3)

    def advance_time(self, hours: float):
        """推进时间。"""
        self.time.advance(hours)

    def to_dict(self) -> dict:
        """输出为字典，供世界模拟器 / Narrator 使用。"""
        return {
            "time": {
                "absolute_day": self.time.absolute_day,
                "period": self.time.day_period(),
                "hour": self.time.hour,
                "is_night": self.time.is_night,
                "formatted": self.time.fmt(),
            },
            "weather": {
                "condition": self.weather.condition,
                "wind": self.weather.wind,
                "temperature": self.weather.temperature,
            },
            "locations": {
                k: {
                    "accessible": v.accessible,
                    "power": v.power,
                    "communication": v.communication,
                    "mutation_level": v.mutation_level,
                    "anomalies": v.anomaly_active,
                    "panic_level": v.panic_level,
                    "supplies": v.supply_status,
                }
                for k, v in self.locations.items()
            },
            "resources": {
                "food": self.resources.food,
                "water": self.resources.water,
                "fuel": self.resources.fuel,
                "medical": self.resources.medical,
            },
            "aggregated_panic": self.aggregated_panic,
            "story_stage": self.story_stage,
        }

    @classmethod
    def from_vault_reader(cls, vault_reader, chapter_num: int) -> EnvState:
        """从 vault 数据构建初始环境状态。"""
        state = cls()
        ch_data = vault_reader.load_chapter_state(chapter_num) if vault_reader else {}
        if not ch_data:
            return state

        # 时间
        ts = ch_data.get("time_span", {})
        if isinstance(ts, dict):
            if ts.get("absolute_day"):
                state.time.absolute_day = int(ts["absolute_day"])

        # 世界阶段
        ws = ch_data.get("world_state", {})
        if isinstance(ws, dict):
            if ws.get("stage"):
                state.story_stage = ws["stage"]
            if ws.get("region_effects"):
                state.apply_region_effects(ws["region_effects"])
            # 恐慌从 public_events 推断
            if ws.get("public_events"):
                state.aggregated_panic = min(10, max(1, len(ws["public_events"]) * 2))

        return state
