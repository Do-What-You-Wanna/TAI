"""模型管理器：登记模型、技能匹配、性价比评分。

核心职责：
1. 维护可用模型列表（本地 Ollama / 云端 OpenAI 兼容 / 内置 Mock）。
2. 按「所需技能」与「性价比分」选出最合适的模型执行子任务。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# 性价比打分中「能力」与「成本」各自的权重（可由界面/配置调整）
W_CAPABILITY = 0.7  # 能力权重
W_COST = 0.3        # 成本权重


@dataclass
class ModelInfo:
    """一个可用模型的基本信息。"""
    key: str                     # 唯一标识，如 "local_qwen"
    name: str                    # 显示名，如 "qwen2.5:14b"
    source: str                  # 来源：local(本地Ollama)/cloud(云端)
    endpoint: str                # 访问地址/模型名
    skills: list[str] = field(default_factory=list)   # 擅长技能标签
    cost: float = 1.0            # 相对单位成本，基准为 1
    capability: float = 50.0     # 通用能力分 0~100
    api_key_ref: str = ""        # 云端时引用的凭证存放键（如 credentials.md 中的键名）


class ModelRegistry:
    """登记所有可用模型，并提供「按技能 + 性价比」的选取能力。"""

    def __init__(self) -> None:
        self._models: dict[str, ModelInfo] = {}

    def register(self, model: ModelInfo) -> None:
        """注册一个模型；同 key 会覆盖。"""
        self._models[model.key] = model

    def get(self, key: str) -> ModelInfo | None:
        return self._models.get(key)

    def all(self) -> list[ModelInfo]:
        return list(self._models.values())

    @staticmethod
    def _skills_overlap(required: list[str], model_skills: list[str]) -> float:
        """计算模型技能与所需技能的重合度（0~1）。

        重叠越多说明越「专业」；空技能视为通用（给一个温和的基础值）。
        """
        if not required or not model_skills:
            return 0.3  # 无明确技能需求时，模型被当通用来用
        req = {r.lower() for r in required}
        own = {s.lower() for s in model_skills}
        return len(req & own) / len(req)

    def cost_per_score(self, model: ModelInfo, required: list[str]) -> float:
        """性价比评分（越高越好）。

        公式 = 能力权重 × 模型能力 × 专业匹配
               + 成本权重 × (基准成本 / 实际成本)
        其中专业匹配 = 平均重叠度提升能力实际价值，
        成本越高代价越大，故用“成本越接近基准越划算”来加成。
        """
        match = self._skills_overlap(required, model.skills)
        capability = model.capability * (0.6 + 0.4 * match)  # 专业匹配会拉高有效能力
        cost_bonus = 1.0 / max(model.cost, 1e-6)              # 成本越低 bonus 越大
        score = W_CAPABILITY * capability + W_COST * 100.0 * math.log1p(cost_bonus)
        return round(score, 2)

    def best_model(self, required: list[str], exclude: list[str] | None = None) -> ModelInfo:
        """返回最匹配（最专业 + 高性价比）的模型。

        先按性价比分排序，分数相同则优先本地（便于隐私/离线）。
        exclude 用于分配时排除已失败的模型，实现“回退”。
        """
        exclude = exclude or []
        candidates = [m for m in self._models.values() if m.key not in exclude]
        if not candidates:
            raise RuntimeError("没有可用模型（已全部排除）")
        # reverse=True 分数从高到低；同分时 local 的排序更靠前
        ordered = sorted(
            candidates,
            key=lambda m: (self.cost_per_score(m, required), m.source == "local"),
            reverse=True,
        )
        return ordered[0]


def build_default_registry() -> ModelRegistry:
    """构造默认模型表（本地主模型 + 云端代码助手 + 云端文档助手）。

    放在 core 层而不是 GUI 层，便于无界面环境也能自测核心链路。
    """
    reg = ModelRegistry()
    reg.register(ModelInfo(
        key="main", name="qwen2.5:14b(主)", source="local", endpoint="qwen2.5:14b",
        skills=["规划", "写作", "通用"], cost=1.0, capability=70))
    reg.register(ModelInfo(
        key="code", name="code-helper", source="cloud", endpoint="code-helper",
        skills=["代码"], cost=2.0, capability=85))
    reg.register(ModelInfo(
        key="doc", name="doc-writer", source="cloud", endpoint="doc-writer",
        skills=["写作", "校对"], cost=1.5, capability=80))
    return reg