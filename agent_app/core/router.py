"""混合分配器（Router）：决定「哪个子任务 → 哪个模型」。

决策优先级（高 → 低）：
1. 人工规则覆盖（敏感内容 → 强制本地；可用模型白名单限制）。
2. LLM 路由（把“任务 → 子任务/所需技能”拆出）。
3. 性价比评分（按技能经 ModelRegistry 选最高分）。
4. 回退（首选失败 → 排除后取次优）。
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


# 内置规则拆解时，依据关键词为句子打技能标签（降级用的粗粒度判断）
_SKILL_KEYWORDS = {
    "规划": ("计划", "规划", "大纲", "安排", "路线", "plan"),
    "写作": ("撰写", "写", "报告", "文章", "内容", "文案", "write", "doc"),
    "代码": ("代码", "实现", "函数", "bug", "修复", "调试", "code", "function"),
    "校对": ("校对", "润色", "审阅", "检查", "proofread", "review"),
    "翻译": ("翻译", "英文", "中文", "译文", "translate"),
}

_DEFAULT_SENSITIVE = ("密码", "密钥", "token", "隐私", "身份证",
                      "银行卡", "机密", "secret", "password")


def _guess_skill(text: str) -> str:
    """根据句中关键词粗略猜测所需技能标签（规则拆解降级用）。"""
    low = text.lower()
    for skill, kws in _SKILL_KEYWORDS.items():
        if any(k in low for k in kws):
            return skill
    return "通用"


class Router:
    """混合分配器。"""

    def __init__(self, registry, route_provider=None, sensitive_keywords=None):
        """
        registry            ModelRegistry 实例
        route_provider      BaseProvider，负责 LLM 拆分；None 时用内置规则拆解
        sensitive_keywords  敏感关键词元组，命中则强制本地；默认用内建清单
        """
        self.registry = registry
        self.route_provider = route_provider
        self._sensitive = tuple(sensitive_keywords) if sensitive_keywords \
            else _DEFAULT_SENSITIVE

    def _is_sensitive(self, text: str) -> bool:
        low = text.lower()
        return any(k.lower() in low for k in self._sensitive)

    # ---- 任务拆分 ----
    def split(self, task_text: str) -> list[SubTask]:
        """把主任务拆成若干子任务。优先 LLM，失败回退内置规则。"""
        if self.route_provider is not None:
            try:
                prompt = (
                    "你是任务拆解助手。请把下面的用户任务拆成不超过3个子任务。\n"
                    '只输出JSON，格式：{"subtasks":[{"index":1,"title":"...","skill":"技能标签"}]}\n'
                    "技能标签可选：规划/写作/代码/校对/翻译/数学/检索 等。\n任务：" + task_text)
                data = self.route_provider.chat_json(prompt)
                subs = [
                    SubTask(int(s.get("index", i + 1)), str(s.get("title", "")),
                            str(s.get("skill", "")) or _guess_skill(str(s.get("title", ""))))
                    for i, s in enumerate(data.get("subtasks", []))
                ]
                if subs:
                    self._reindex(subs)
                    return subs
            except Exception:
                pass  # LLM 拆分失败，走内置规则
        return self._rule_based_split(task_text)

    @staticmethod
    def _reindex(tasks: list[SubTask]) -> None:
        for i, t in enumerate(tasks):
            t.index = i + 1

    @staticmethod
    def _rule_based_split(task_text: str) -> list[SubTask]:
        parts = [p.strip() for p in re.split(r"[。；;，,\n]", task_text) if p.strip()]
        if not parts:
            parts = [task_text]
        return [
            SubTask(i + 1, p, _guess_skill(p))
            for i, p in enumerate(parts[:3])
        ]

    # ---- 分配 ----
    def assign(self, tasks: list[SubTask], main_task_text: str,
               exclude: dict[int, list[str]] | None = None,
               available_keys: list[str] | None = None) -> list[Assignment]:
        """为每个子任务分配模型。

        exclude:          {task_index: [已失败模型key]}，用于回退。
        available_keys:   当前可用模型 key 白名单（离线模型会被 Orchestrator 剔除）。
        """
        exclude = exclude or {}
        avail = available_keys or [m.key for m in self.registry.all()]
        candidates = [m for m in self.registry.all() if m.key in avail]

        assignments: list[Assignment] = []
        for task in tasks:
            # 1) 敏感内容 → 强制本地（用户数据不外发）
            if self._is_sensitive(task.title) or self._is_sensitive(main_task_text):
                local = next((m for m in candidates if m.source == "local"), None)
                if local is not None:
                    assignments.append(Assignment(
                        task, local.key, "规则覆盖：命中敏感内容，强制本地模型", -1.0))
                    continue
            # 2) 受白名单与“已失败排除”约束，选性价比最高模型（同分优先本地）
            banned = exclude.get(task.index, [])
            pool = [m for m in candidates if m.key not in banned]
            if not pool:
                assignments.append(Assignment(
                    task, "", "无可用模型（候选已全部失败或排除）", -1.0))
                continue
            ranked = sorted(
                pool,
                key=lambda m: (self.registry.cost_per_score(m, [task.skill]),
                               m.source == "local"),
                reverse=True)
            model = ranked[0]
            assignments.append(Assignment(
                task, model.key, "LLM路由+性价比评分",
                self.registry.cost_per_score(model, [task.skill])))
        return assignments