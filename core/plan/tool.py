"""PlanAndExecuteTool — 运行完整 PlanLoop 的执行器"""

from typing import Any

from core.tools.base import BaseTool


class PlanAndExecuteTool(BaseTool):
    """plan_and_execute 工具

    当 LLM 调用此工具时，在 execute() 内部创建 PlanLoop 并运行完整
    的 Plan → Execute → Review 循环，返回最终结果。
    """

    name = "plan_and_execute"
    description = (
        "将复杂任务分解为子计划并逐步执行。"
        "当任务需要多步骤、有依赖关系、或需要调研+整合时使用。"
        "简单问题不要使用此工具，直接回答即可。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "goal": {
                "type": "string",
                "description": "任务目标描述",
            },
        },
        "required": ["goal"],
    }
    output_type = "text"

    # 类级存储：由 ws_server 在 session 初始化时设置
    _dependencies: dict[str, dict[str, Any]] = {}

    @classmethod
    def set_session_context(
        cls,
        session_id: str,
        instructor: Any,
        registry: Any,
        prompt_assembler: Any,
        send_block: Any,
    ) -> None:
        cls._dependencies[session_id] = {
            "instructor": instructor,
            "registry": registry,
            "prompt_assembler": prompt_assembler,
            "send_block": send_block,
        }

    @classmethod
    def clear_session_context(cls, session_id: str) -> None:
        cls._dependencies.pop(session_id, None)

    async def execute(self, **kwargs: Any) -> dict:
        """执行 plan_and_execute：创建 PlanLoop 并运行完整 Plan→Execute→Review 循环"""
        goal = kwargs.get("goal", "").strip()
        if not goal:
            return {
                "status": "error",
                "error": "goal 参数不能为空",
                "error_type": "validation_error",
            }

        # 从 ToolRegistry 获取 session_id
        from core.tools.registry import get_registry
        session_id = get_registry().session_id
        deps = self._dependencies.get(session_id)
        if not deps:
            return {
                "status": "error",
                "error": "Plan 依赖未初始化",
                "error_type": "internal_error",
            }

        from core.plan.loop import PlanLoop
        plan_loop = PlanLoop(
            instructor=deps["instructor"],
            registry=deps["registry"],
            prompt_assembler=deps["prompt_assembler"],
            send_block=deps["send_block"],
        )
        _, final_say, _ = await plan_loop.run(
            user_message=goal,
            history=[],
        )

        return {
            "status": "ok",
            "result": final_say or "(计划执行完成，无输出)",
            "output_type": "text",
        }
