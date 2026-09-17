"""模型管理器（ModelRegistry）：登记模型、增删改、主/备用、技能匹配、性价比评分。

正式版功能相对 demo 的扩展：
- 支持增删改、设置主模型。
- 支持持久化序列化（to/from dict），配合 config.py。
- 性价比权重可配置（不再写死模块常量）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class ModelInfo:
    """一个可用模型的基本信息。"""
    key: str                     # 唯一标识
    name: str                    # 显示名
    source: str                  # local(本地Ollama)/cloud(云端)
    endpoint: str                # 访问地址/模型名
    skills: list[str] = field(default_factory=list)   # 擅长技能标签
    cost: float = 1.0            # 相对单位成本，基准为 1
    capability: float = 50.0     # 通用能力分 0~100
    is_primary: bool = False     # 是否主模型
    api_key_ref: str = ""        # 云端时引用的凭证存放键

    @classmethod
    def field_names(cls) -> list[str]:
        """返回可持久化的字段名列表（供 config 序列化对齐使用）。"""
        return ["key", "name", "source", "endpoint", "skills", "cost",
                "capability", "is_primary", "api_key_ref"]

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.field_names()}

    @classmethod
    def from_dict(cls, d: dict) -> "ModelInfo":
        return cls(**{k: d.get(k) for k in cls.field_names()})


class ModelRegistry:
    """模型登记与选取管理器。"""

    _DEFAULT_WEIGHTS = {"capability": 0.7, "cost": 0.3}

    def __init__(self, weights: dict | None = None) -> None:
        self._models: dict[str, ModelInfo] = {}
        # 可用 weight：capability/cost，由配置驱动
        self.weights = self._DEFAULT_WEIGHTS if not weights else {
            **self._DEFAULT_WEIGHTS, **weights}

    # ---- 基础增删改查 ----
    def register(self, model: ModelInfo) -> None:
        self._models[model.key] = model

    def get(self, key: str) -> ModelInfo | None:
        return self._models.get(key)

    def all(self) -> list[ModelInfo]:
        return list(self._models.values())

    def remove(self, key: str) -> bool:
        if key in self._models:
            del self._models[key]
            return True
        return False

    def update(self, model: ModelInfo) -> None:
        if model.key not in self._models:
            raise KeyError(f"模型不存在：{model.key}")
        self._models[model.key] = model

    def primary(self) -> ModelInfo | None:
        """返回主模型；无 mark 则返回第一个 local 或第一个模型。"""
        marked = next((m for m in self._models.values() if m.is_primary), None)
        if marked:
            return marked
        return next((m for m in self._models.values() if m.source == "local"), None) \
            or next(iter(self._models.values()), None)

    def set_primary(self, key: str) -> None:
        if key not in self._models:
            raise KeyError(f"模型不存在：{key}")
        for m in self._models.values():
            m.is_primary = (m.key == key)

    # ---- 持久化 ----
    def to_list_dicts(self) -> list[dict]:
        return [m.to_dict() for m in self._models.values()]

    def from_list_dicts(self, items: list[dict]) -> None:
        self._models = {it["key"]: ModelInfo.from_dict(it) for it in items}

    # ---- 评分 ----
    @staticmethod
    def _skills_overlap(required: list[str], model_skills: list[str]) -> float:
        if not required or not model_skills:
            return 0.3  # 无明确技能需求时当作通用
        req = {r.lower() for r in required}
        own = {s.lower() for s in model_skills}
        return len(req & own) / len(req)

    def cost_per_score(self, model: ModelInfo, required: list[str]) -> float:
        """性价比评分（越高越好）。权重来自配置。"""
        match = self._skills_overlap(required, model.skills)
        capability = model.capability * (0.6 + 0.4 * match)
        cost_bonus = 1.0 / max(model.cost, 1e-6)
        w_cap = self.weights.get("capability", 0.7)
        w_cost = self.weights.get("cost", 0.3)
        score = w_cap * capability + w_cost * 100.0 * math.log1p(cost_bonus)
        return round(score, 2)

    def best_model(self, required: list[str], exclude: list[str] | None = None) -> ModelInfo:
        """返回最匹配模型；exclude 用于失败回退（排除已失败模型）。"""
        exclude = exclude or []
        candidates = [m for m in self._models.values() if m.key not in exclude]
        if not candidates:
            raise RuntimeError("没有可用模型（已全部排除）")
        ordered = sorted(
            candidates,
            key=lambda m: (self.cost_per_score(m, required), m.source == "local"),
            reverse=True,
        )
        return ordered[0]


def build_default_registry(weights: dict | None = None) -> ModelRegistry:
    """构造默认模型表（本地主模型 + 云端代码助手 + 云端文档助手）。"""
    reg = ModelRegistry(weights=weights)
    reg.register(ModelInfo(
        key="main", name="qwen2.5:14b(主)", source="local", endpoint="qwen2.5:14b",
        skills=["规划", "写作", "通用"], cost=1.0, capability=70, is_primary=True))
    reg.register(ModelInfo(
        key="code", name="code-helper", source="cloud", endpoint="code-helper",
        skills=["代码"], cost=2.0, capability=85))
    reg.register(ModelInfo(
        key="doc", name="doc-writer", source="cloud", endpoint="doc-writer",
        skills=["写作", "校对"], cost=1.5, capability=80))
    return reg