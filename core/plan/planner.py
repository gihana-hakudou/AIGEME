"""PlanPlanner — 规划 LLM 调用封装"""

import json
import logging
from typing import Any

import litellm
import instructor
from instructor import AsyncInstructor

from core.engine.instructor_client import InstructorClient
from core.plan.models import PlanResponse, SubTask

logger = logging.getLogger(__name__)

# 最大子任务数
MAX_SUBTASKS = 10

PLAN_SYSTEM_PROMPT = """你是一个任务规划器。用户提出了一个复杂任务，请将其分解为可执行的子任务。

## 规则
1. 每个子任务应该是 RaAct 循环可以在 1-8 轮内完成的
2. 子任务之间可以声明依赖关系（depends_on）
3. 子任务描述要具体、自包含——执行者看不到主对话历史
4. 避免过度分解：3-6 个子任务为宜，最多不超过 10 个
5. 标注哪些子任务可以并行执行（无依赖关系）

## 输出格式
返回 PlanResponse，包含 reasoning、goal、strategy、subtasks。"""


class PlanPlanner:
    """规划 LLM 调用封装

    职责：
    1. 调用 LLM 生成 Plan（结构化输出）
    2. 解析 PlanResponse（JSON 解析 + Pydantic 回退）
    3. 校验计划合法性
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_base: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self._model = model
        self._api_base = api_base
        self._api_key = api_key
        self._client: AsyncInstructor = instructor.from_litellm(
            litellm.acompletion,
            mode=instructor.Mode.JSON,
        )

    @classmethod
    def from_instructor(
        cls,
        instructor: InstructorClient,
        model: str | None = None,
    ) -> "PlanPlanner":
        """从 InstructorClient 实例创建 PlanPlanner

        Args:
            instructor: InstructorClient 实例（提供默认 model/api_base/api_key）
            model: 可选覆写的模型名，优先于 instructor 的默认模型

        Returns:
            PlanPlanner 实例
        """
        return cls(
            model=model or instructor.model,
            api_base=instructor.api_base,
            api_key=instructor.api_key,
        )

    async def generate(
        self,
        user_message: str,
        history: list | None = None,
    ) -> PlanResponse | None:
        """调用 LLM 生成计划

        Args:
            user_message: 用户原始消息
            history: 主对话历史（可选，作为上下文参考）

        Returns:
            PlanResponse | None: 解析失败返回 None
        """
        messages = [
            {"role": "system", "content": PLAN_SYSTEM_PROMPT},
        ]
        if history:
            # 将 history 转换为 dict 格式
            for msg in history:
                if hasattr(msg, "content"):
                    role = self._role_of(msg)
                    messages.append({"role": role, "content": msg.content or ""})
                elif isinstance(msg, dict):
                    messages.append(msg)

        messages.append({"role": "user", "content": user_message})

        kwargs = {}
        if self._api_base:
            kwargs["api_base"] = self._api_base
        if self._api_key:
            kwargs["api_key"] = self._api_key

        try:
            response: PlanResponse = await self._client.chat.completions.create(
                model=self._model,
                response_model=PlanResponse,
                messages=messages,
                max_retries=2,
                **kwargs,
            )
        except Exception as e:
            logger.warning("Plan 结构化调用失败: %s，尝试 JSON 回退", e)
            response = await self._json_fallback(messages, **kwargs)

        if response is None:
            return None

        # 校验计划合法性
        errors = self._validate(response)
        if errors:
            logger.warning("Plan 校验失败: %s", "; ".join(errors))
            return None

        return response

    async def _json_fallback(
        self,
        messages: list[dict],
        **kwargs,
    ) -> PlanResponse | None:
        """Instructor 结构化调用失败后的 JSON 回退方案

        直接用 litellm 请求文本输出，然后尝试解析 JSON。
        """
        try:
            # 追加 JSON 格式指令
            fallback_messages = list(messages)
            fallback_messages.append({
                "role": "user",
                "content": (
                    "请以 JSON 格式输出，格式如下：\n"
                    '{"reasoning": "...", "goal": "...", "strategy": "...", '
                    '"subtasks": [{"id": "sub_1", "title": "...", "description": "...", '
                    '"depends_on": []}]}'
                ),
            })

            response = await litellm.acompletion(
                model=self._model,
                messages=fallback_messages,
                temperature=0.7,
                max_tokens=4096,
                **kwargs,
            )

            if not response.choices or not response.choices[0].message:
                return None
            content = response.choices[0].message.content or ""
            # 提取 JSON（可能被 markdown 代码块包裹）
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()

            data = json.loads(content)
            subtasks = [
                SubTask(
                    id=st["id"],
                    title=st["title"],
                    description=st.get("description", st["title"]),
                    depends_on=st.get("depends_on", []),
                )
                for st in data.get("subtasks", [])
            ]
            return PlanResponse(
                reasoning=data.get("reasoning", ""),
                goal=data.get("goal", ""),
                strategy=data.get("strategy", ""),
                subtasks=subtasks,
            )
        except Exception as e:
            logger.error("JSON 回退解析失败: %s", e)
            return None

    def _validate(self, plan: PlanResponse) -> list[str]:
        """校验计划合法性

        Checks:
        - 子任务数量限制
        - ID 唯一性
        - depends_on 引用存在的 ID
        - 循环依赖
        """
        errors: list[str] = []
        ids = [st.id for st in plan.subtasks]

        # 1. 子任务数量限制
        if not plan.subtasks:
            errors.append("没有子任务")
            return errors
        if len(plan.subtasks) > MAX_SUBTASKS:
            errors.append(f"子任务数量 {len(plan.subtasks)} 超过上限 {MAX_SUBTASKS}")

        # 2. ID 唯一性
        if len(set(ids)) != len(ids):
            errors.append("子任务 ID 不唯一")

        # 3. depends_on 引用存在
        id_set = set(ids)
        for st in plan.subtasks:
            for dep in st.depends_on:
                if dep not in id_set:
                    errors.append(f"子任务 {st.id} 依赖不存在的 ID: {dep}")

        # 4. 循环依赖检测（拓扑排序）
        if not errors:
            in_degree: dict[str, int] = {st.id: 0 for st in plan.subtasks}
            graph: dict[str, list[str]] = {st.id: [] for st in plan.subtasks}
            for st in plan.subtasks:
                for dep in st.depends_on:
                    graph[dep].append(st.id)
                    in_degree[st.id] += 1

            queue = [nid for nid, deg in in_degree.items() if deg == 0]
            visited = 0
            while queue:
                nid = queue.pop(0)
                visited += 1
                for neighbor in graph[nid]:
                    in_degree[neighbor] -= 1
                    if in_degree[neighbor] == 0:
                        queue.append(neighbor)

            if visited != len(ids):
                errors.append("子任务间存在循环依赖")

        return errors

    @staticmethod
    def _role_of(msg: Any) -> str:
        """获取消息角色名"""
        name = msg.__class__.__name__
        if name == "HumanMessage":
            return "user"
        if name == "AIMessage":
            return "assistant"
        if name == "ToolMessage":
            return "tool"
        if name == "SystemMessage":
            return "system"
        return "user"
