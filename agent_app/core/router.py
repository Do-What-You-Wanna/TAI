"""混合分配器（Router）：决定「哪个子任务 → 哪个模型」。

策略（优先级从高到低）：
1. 人工规则覆盖：由用户/配置指定的强约束最先生效。
2. LLM 路由：让路由模型把“任务 → 所需技能”拆出来。
3. 性价比评分：Router 依据技能用 ModelRegistry 选出最合适模型。
4. 回退：首选模型失败时自动降级到次优。

本 demo 里规则覆盖做得简洁（本地离线 → 云端；敏感 → 本地），
LLM 路由可通过路由模型调用，也可由 RuleBased 拆解作为降级方案。
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class SubTask:
    """一个被拆出的子任务。"""
    index: int
    title: str
    skill: str          # 所需技能标签，用于模型匹配


@dataclass
class Assignment:
    """一个分配结果：子任务 + 选中的模型。"""
    task: SubTask
    model_key: str
    reason: str
    score: float


# 敏感关键词：命中则强制使用本地模型（避免数据外发云端）
_SENSITIVE_KEYWORDS = ("密码", "密钥", "token", "隐私", "身份证", "银行卡", "机密", "secret", "password")


def _is_sensitive(text: str) -> bool:
    low = text.lower()
    return any(k in low for k in _SENSITIVE_KEYWORDS)


# 内置规则拆解时，依据关键词为句子打技能标签（降级用的粗粒度判断）
_SKILL_KEYWORDS = {
    "规划": ("计划", "规划", "大纲", "安排", "路线", "plan"),
    "写作": ("撰写", "写", "报告", "文章", "内容", "文案", "write", "doc"),
    "代码": ("代码", "实现", "函数", "bug", "修复", "调试", "code", "function"),
    "校对": ("校对", "润色", "审阅", "检查", "proofread", "review"),
    "翻译": ("翻译", "英文", "中文", "译文", "translate"),
}


def _guess_skill(text: str) -> str:
    """根据句中关键词粗略猜测所需技能标签。"""
    low = text.lower()
    for skill, kws in _SKILL_KEYWORDS.items():
        if any(k in low for k in kws):
            return skill
    return "通用"


class Router:
    """混合分配器。"""

    def __init__(self, registry, route_provider=None):
        """
        registry          ModelRegistry 实例
        route_provider    BaseProvider，负责“任务 → 子任务/技能”的 LLM 拆分；
                          为 None 时使用内置 RuleBased 拆解。
        """
        self.registry = registry
        self.route_provider = route_provider

    # ---- 任务拆分 ----
    def split(self, task_text: str) -> list[SubTask]:
        """把一段主任务拆成若干子任务。

        优先用 LLM（route_provider），失败/不可用时回退到内置规则拆解。
        """
        if self.route_provider is not None:
            try:
                prompt = (
                    "你是任务拆解助手。请把下面的用户任务拆成不超过3个子任务。\n"
                    '只输出JSON，格式：{"subtasks":[{"index":1,"title":"...","skill":"技能标签"}]}\n'
                    f"技能标签可选：规划/写作/代码/校对/翻译/数学/检索 等。\n任务：{task_text}"
                )
                data = self.route_provider.chat_json(prompt)
                subs = [
                    SubTask(int(s.get("index", i + 1)), str(s.get("title", "")),
                            str(s.get("skill", "")))
                    for i, s in enumerate(data.get("subtasks", []))
                ]
                if subs:
                    return subs
            except Exception:
                pass  # LLM 拆分失败，走内置规则
        return self._rule_based_split(task_text)

    @staticmethod
    def _rule_based_split(task_text: str) -> list[SubTask]:
        """内置规则拆解：按标点拆句并打技能标签（降级方案）。"""
        parts = [p.strip() for p in re.split(r"[。；;，,\n]", task_text) if p.strip()]
        if not parts:
            parts = [task_text]
        return [
            SubTask(i + 1, p, _guess_skill(p))
            for i, p in enumerate(parts[:3])
        ]

    # ---- 硬性规则覆盖 ----
    def _rule_override(self, task: SubTask, main_task_text: str) -> str | None:
        """返回强制指定的模型 key；无强制则返回 None。"""
        # 敏感内容 → 只允许本地模型
        if _is_sensitive(task.title) or _is_sensitive(main_task_text):
            return self._first_local_key() or None
        return None

    def _first_local_key(self) -> str | None:
        for m in self.registry.all():
            if m.source == "local":
                return m.key
        return None

    # ---- 分配 ----
    def assign(self, tasks: list[SubTask], main_task_text: str, exclude: dict[int, list[str]] | None = None):
        """为每个子任务分配模型。

        exclude: {task_index: [已失败模型key]}，用于回退时排除。
        返回 Assignment 列表。
        """
        exclude = exclude or {}
        assignments: list[Assignment] = []
        for task in tasks:
            # 1) 规则覆盖
            forced = self._rule_override(task, main_task_text)
            if forced:
                assignments.append(Assignment(
                    task, forced,
                    "规则覆盖：命中敏感内容/约束，强制本地模型", -1.0))
                continue
            # 2) 性价比选择 + 排除已失败的模型（回退）
            banned = exclude.get(task.index, [])
            model = self.registry.best_model([task.skill], exclude=banned)
            score = self.registry.cost_per_score(model, [task.skill])
            assignments.append(Assignment(task, model.key, "LLM路由+性价比评分", score))
        return assignments