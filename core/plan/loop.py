"""PlanLoop — Plan-Execute-Review 主循环"""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from core.engine.instructor_client import InstructorClient
from core.engine.context import PromptAssembler
from core.plan.models import Plan, PlanResponse, SubTask, SubTaskStatus
from core.plan.planner import PlanPlanner
from core.plan.executor import ExecutorFactory
from core.protocols.blocks import Block
from core.tools.registry import ToolRegistry
from langchain_core.messages import AIMessage

logger = logging.getLogger(__name__)


class PlanLoop:
    """Plan-and-Execute 主循环

    职责：
    1. 接收复杂任务 → 调用 PlanPlanner 生成 Plan
    2. 逐个执行 SubTask（每个启动独立 RaActLoop）
    3. 子任务结果输出为 md 文件，不做字数截断
    4. 执行完毕后 Review → 统合报告
    """

    def __init__(
        self,
        instructor: InstructorClient,
        registry: ToolRegistry,
        prompt_assembler: PromptAssembler,
        send_block: Callable,
        config: dict | None = None,
    ) -> None:
        self._instructor = instructor
        self._registry = registry
        self._prompt_assembler = prompt_assembler
        # 包装 send_block：dict → Block 对象转换，兼容 session.send_block(Block) 签名
        self._send_block = self._make_block_sender(send_block)
        self._config = config or {}

        # 初始化 PlanPlanner（通过工厂方法，使用 InstructorClient 公共接口）
        self._planner = PlanPlanner.from_instructor(
            instructor=instructor,
            model=self._config.get("plan_model"),
        )

        # 初始化 ExecutorFactory
        self._executor_factory = ExecutorFactory(
            raact_loop_kwargs={
                "instructor": instructor,
                "registry": registry,
                "prompt_assembler": prompt_assembler,
            },
            config=config,
        )

        # 取消支持
        self._cancelled_flag = False
        # 审核支持（预留）
        self._approval_result: bool | None = None
        self._approval_event = asyncio.Event()

        # 子任务结果目录
        self._result_dir = Path(
            self._config.get("subtask_result_dir", ".AIGEME/plan_results")
        )
        # 是否需要用户审核计划
        self._require_approval = self._config.get("require_plan_approval", False)

    def cancel(self) -> None:
        """取消当前执行"""
        self._cancelled_flag = True

    @property
    def _cancelled(self) -> bool:
        return self._cancelled_flag

    # ── Public API ──

    @staticmethod
    def _make_block_sender(send_block: Callable) -> Callable:
        """包装 send_block，自动将 dict 转换为 Block 对象"""
        async def _send(data: dict | Block) -> None:
            if isinstance(data, Block):
                await send_block(data)
            else:
                try:
                    await send_block(Block(**data))
                except (TypeError, ValueError) as e:
                    logger.error("Block creation failed: data=%s, error=%s", data, e)
                    return
        return _send

    async def run(
        self,
        user_message: str,
        history: list[Any],
        images: list[dict] | None = None,
    ) -> tuple[list[Any], str, str]:
        """Plan-Execute-Review 主流程

        Returns:
            (round_messages, final_say, accumulated_reasoning)
            与 RaActLoop.raact_stream 返回格式一致
        """
        # ── Phase 1: Plan ──
        await self._send_plan_thinking("正在分析任务并制定计划...")
        plan = await self._plan(user_message, history)

        if self._cancelled or plan is None:
            return [], "计划生成失败，请重试或简化问题。", ""

        # 推送计划到前端
        await self._send_plan(plan)

        # ── Phase 1.5: 用户审核（可选） ──
        if self._require_approval:
            approved = await self._wait_for_approval()
            if not approved:
                return [], "计划已取消", ""
            if self._cancelled:
                return [], "", ""

        # ── Phase 2: Execute ──
        subtask_results: dict[str, str] = {}
        subtask_timeout = self._config.get("subtask_timeout", 300)  # 默认 5 分钟

        for subtask in self._execution_order(plan):
            if self._cancelled:
                break

            # 跳过前置任务未完成的子任务
            failed_deps = [
                dep for dep in subtask.depends_on
                if subtask_results.get(dep) == "__failed__"
            ]
            if failed_deps:
                subtask.status = SubTaskStatus.SKIPPED
                subtask.result = f"跳过：前置任务 {failed_deps} 失败"
                await self._send_progress(plan)
                continue

            subtask.status = SubTaskStatus.RUNNING
            await self._send_subtask_start(subtask, plan)

            # 构建子任务上下文
            executor_message = self._build_executor_message(
                subtask=subtask,
                plan=plan,
                original_goal=user_message,
            )

            # 启动独立 RaActLoop 执行子任务
            executor_loop = self._executor_factory.create()
            # 设置取消引用（共享 PlanLoop 的取消状态）
            executor_loop.set_cancelled_ref(lambda: self._cancelled_flag)

            try:
                round_msgs, final_say, reasoning = await asyncio.wait_for(
                    executor_loop.raact_stream(
                        user_message=executor_message,
                        history=[],
                        send_block=self._send_block,
                    ),
                    timeout=subtask_timeout,
                )

                # 记录结果
                subtask.result = (final_say or "")[:500]
                subtask.status = (
                    SubTaskStatus.COMPLETED if final_say
                    else SubTaskStatus.FAILED
                )
                if subtask.status == SubTaskStatus.FAILED:
                    subtask_results[subtask.id] = "__failed__"
                else:
                    subtask_results[subtask.id] = final_say
                    # 完整结果写入 md 文件
                    subtask.result_file = await self._save_result_md(
                        subtask, final_say
                    )

            except asyncio.TimeoutError:
                logger.error("子任务 %s 执行超时 (%ds)", subtask.id, subtask_timeout)
                subtask.status = SubTaskStatus.FAILED
                subtask.error = f"执行超时 ({subtask_timeout}s)"
                subtask_results[subtask.id] = "__failed__"
            except Exception as e:
                logger.exception("子任务 %s 执行异常: %s", subtask.id, e)
                subtask.status = SubTaskStatus.FAILED
                subtask.error = str(e)
                subtask_results[subtask.id] = "__failed__"

            # 推送子任务完成进度
            await self._send_subtask_done(subtask, plan)

        # ── Phase 3: Review ──
        if not self._cancelled:
            final_say = await self._review(plan, subtask_results, user_message)
        else:
            final_say = "执行已取消。"

        round_messages = []
        if final_say:
            round_messages.append(AIMessage(content=final_say))
        return round_messages, final_say or "", ""

    # ── Phase 1: Plan ──

    async def _plan(
        self,
        user_message: str,
        history: list[Any],
    ) -> Plan | None:
        """调用 PlanPlanner 生成计划"""
        try:
            plan_response = await self._planner.generate(user_message, history)
        except Exception as e:
            logger.exception("规划生成失败: %s", e)
            return None

        if plan_response is None:
            return None

        return Plan(
            goal=plan_response.goal,
            subtasks=plan_response.subtasks,
            strategy=plan_response.strategy,
        )

    # ── Phase 3: Review ──

    async def _review(
        self,
        plan: Plan,
        subtask_results: dict[str, str],
        original_goal: str,
    ) -> str:
        """审查执行结果，生成统合报告"""
        review_prompt = self._format_plan_results(plan)
        review_message = f"""原始目标：{original_goal}

执行计划与结果：
{review_prompt}

请评估：
1. 所有子任务是否成功完成？
2. 结果是否达成了原始目标？
3. 如有不足，指出需要补充的方面。

如果全部完成，请给出统合报告。
如果有重大缺失，建议需要追加的子任务。"""

        try:
            response = await self._instructor.create_completion(
                messages=[
                    {"role": "system", "content": self._prompt_assembler.build_system_prompt()},
                    {"role": "user", "content": review_message},
                ],
            )
            return response.say or "执行完成，但无法生成统合报告。"
        except Exception as e:
            logger.exception("Review 阶段失败: %s", e)
            return "执行完成，统合报告生成失败。"

    def _format_plan_results(
        self,
        plan: Plan,
    ) -> str:
        """格式化执行结果供 Review 阶段使用"""
        lines = [f"目标: {plan.goal}"]
        if plan.strategy:
            lines.append(f"策略: {plan.strategy}")
        lines.append("")

        for st in plan.subtasks:
            status_icon = {
                SubTaskStatus.COMPLETED: "✅",
                SubTaskStatus.FAILED: "❌",
                SubTaskStatus.SKIPPED: "⏭️",
                SubTaskStatus.RUNNING: "🔄",
                SubTaskStatus.PENDING: "⏳",
            }.get(st.status, "❓")

            lines.append(f"{status_icon} {st.id}: {st.title}")
            if st.result:
                lines.append(f"   摘要: {st.result}")
            if st.result_file:
                lines.append(f"   结果文件: {st.result_file}")
            if st.depends_on:
                lines.append(f"   依赖: {', '.join(st.depends_on)}")
            if st.error:
                lines.append(f"   错误: {st.error}")
            lines.append("")

        return "\n".join(lines)

    # ── 辅助方法 ──

    def _build_executor_message(
        self,
        subtask: SubTask,
        plan: Plan,
        original_goal: str,
    ) -> str:
        """构建子 Agent 的任务指令（作为第一条 HumanMessage）"""
        parts: list[str] = []

        # 1. 主目标
        parts.append(f"## 主目标\n{original_goal}")

        # 2. 你的子任务
        parts.append(f"## 你的子任务\n{subtask.description}")

        # 3. 计划概览（让子 Agent 知道自己在哪一步）
        progress_lines = []
        for st in plan.subtasks:
            if st.id == subtask.id:
                marker = "← **当前任务**"
            elif st.status == SubTaskStatus.COMPLETED:
                marker = "✅ 已完成"
            elif st.status == SubTaskStatus.RUNNING:
                marker = "🔄 执行中"
            else:
                marker = "⏳ 待执行"
            progress_lines.append(f"- {st.id}: {st.title} {marker}")
        parts.append("## 计划概览\n" + "\n".join(progress_lines))

        # 4. 前置任务结果
        dep_lines = []
        for dep_id in subtask.depends_on:
            dep_task = next((s for s in plan.subtasks if s.id == dep_id), None)
            if dep_task and dep_task.result_file:
                dep_lines.append(
                    f"- {dep_id}（{dep_task.title}）："
                    f"结果文件 {dep_task.result_file}"
                    f"（摘要：{dep_task.result or '无'}）"
                )
            elif dep_task and dep_task.result:
                dep_lines.append(
                    f"- {dep_id}（{dep_task.title}）：{dep_task.result}"
                )
        if dep_lines:
            parts.append(
                "## 前置任务结果\n"
                + "\n".join(dep_lines)
                + "\n如需了解前置任务细节，请用 file_read 工具读取对应的 md 文件。"
            )

        # 5. 输出要求
        parts.append(
            f"## 输出要求\n"
            f"完成子任务后，将完整结果写入文件：{self._result_dir.as_posix()}/{subtask.id}.md\n"
            f"同时在回复中给出简短摘要。"
        )

        return "\n\n".join(parts)

    def _execution_order(self, plan: Plan) -> list[SubTask]:
        """拓扑排序：按依赖关系确定执行顺序

        使用 Kahn 算法，确保前置任务在前执行。
        """
        # 构建入度表和邻接表
        in_degree: dict[str, int] = {st.id: 0 for st in plan.subtasks}
        # 反向图：st -> deps，入度 = 依赖数
        for st in plan.subtasks:
            in_degree[st.id] = len(st.depends_on)

        # 前置任务 -> 后置任务映射（用于入度递减）
        forward: dict[str, list[str]] = {}
        for st in plan.subtasks:
            for dep in st.depends_on:
                if dep not in forward:
                    forward[dep] = []
                forward[dep].append(st.id)

        # 初始队列 = 入度为 0 的节点
        queue = [st.id for st in plan.subtasks if in_degree[st.id] == 0]
        ordered: list[SubTask] = []
        subtask_map = {st.id: st for st in plan.subtasks}

        while queue:
            nid = queue.pop(0)
            if nid in subtask_map:
                ordered.append(subtask_map[nid])
            for neighbor in forward.get(nid, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        # 如果有循环依赖，把剩余节点追加到末尾
        remaining = [st for st in plan.subtasks if st not in ordered]
        ordered.extend(remaining)

        return ordered

    async def _save_result_md(self, subtask: SubTask, content: str) -> str:
        """将子任务完整结果写入 md 文件

        Returns:
            生成的 md 文件路径
        """
        # 安全处理 subtask.id：防止路径穿越
        # subtask.id 来自 LLM 输出，必须清理
        safe_id = "".join(c for c in subtask.id if c.isalnum() or c in "-_")
        if not safe_id:
            safe_id = "task_unknown"

        # 确保目录存在
        try:
            self._result_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

        # 使用 Path.resolve() 防止目录穿越
        file_path = (self._result_dir / f"{safe_id}.md").resolve()
        # 验证文件路径在允许的目录内
        if not str(file_path).startswith(str(self._result_dir.resolve())):
            logger.error("路径穿越尝试: subtask.id='%s',  sanitized='%s'", subtask.id, safe_id)
            return ""

        try:
            full_content = (
                f"# {subtask.title}\n\n"
                f"**ID**: {subtask.id}\n"
                f"**描述**: {subtask.description}\n"
                f"**状态**: {subtask.status.value}\n"
                f"**完成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                "---\n\n"
                f"{content}"
            )
            file_path.write_text(full_content, encoding="utf-8")
            logger.info("子任务结果已写入: %s", file_path)
            return str(file_path)
        except OSError as e:
            logger.error("写入结果文件失败 %s: %s", file_path, e)
            return ""

    # ── Block 推送 ──

    async def _send_plan_thinking(self, text: str) -> None:
        """推送规划思路到前端"""
        try:
            await self._send_block({
                "type": "block",
                "block_type": "plan_thinking",
                "delta": text,
                "is_final": False,
                "metadata": {},
            })
        except Exception as e:
            logger.warning("推送 plan_thinking 失败: %s", e)

    async def _send_plan(self, plan: Plan) -> None:
        """推送完整计划到前端，并终止 plan_thinking 流"""
        # 终止 plan_thinking 流（补发 is_final=True）
        try:
            await self._send_block({
                "type": "block",
                "block_type": "plan_thinking",
                "delta": "",
                "is_final": True,
                "metadata": {},
            })
        except Exception:
            pass
        try:
            subtasks_data = [
                {
                    "id": st.id,
                    "title": st.title,
                    "description": st.description,
                    "depends_on": st.depends_on,
                }
                for st in plan.subtasks
            ]
            plan_data = {
                "goal": plan.goal,
                "strategy": plan.strategy or "",
                "subtasks": subtasks_data,
                "require_approval": self._require_approval,
            }
            await self._send_block({
                "type": "block",
                "block_type": "plan",
                "delta": json.dumps(plan_data, ensure_ascii=False),
                "is_final": True,
                "metadata": {},
            })
        except Exception as e:
            logger.warning("推送 plan 失败: %s", e)

    async def _send_subtask_start(self, subtask: SubTask, plan: Plan) -> None:
        """推送子任务开始消息"""
        try:
            completed = sum(
                1 for s in plan.subtasks if s.status == SubTaskStatus.COMPLETED
            )
            total = len(plan.subtasks)
            await self._send_block({
                "type": "block",
                "block_type": "plan_progress",
                "delta": json.dumps({
                    "type": "subtask_start",
                    "subtask_id": subtask.id,
                    "title": subtask.title,
                    "status": "running",
                    "completed": completed,
                    "total": total,
                }, ensure_ascii=False),
                "is_final": False,
                "metadata": {},
            })
        except Exception as e:
            logger.warning("推送 subtask_start 失败: %s", e)

    async def _send_subtask_done(self, subtask: SubTask, plan: Plan) -> None:
        """推送子任务完成消息"""
        try:
            completed = sum(
                1 for s in plan.subtasks
                if s.status in (SubTaskStatus.COMPLETED, SubTaskStatus.SKIPPED)
            )
            total = len(plan.subtasks)
            data = {
                "type": "subtask_done" if subtask.status == SubTaskStatus.COMPLETED else "subtask_failed",
                "subtask_id": subtask.id,
                "title": subtask.title,
                "status": subtask.status.value,
                "summary": subtask.result or "",
                "completed": completed,
                "total": total,
            }
            if subtask.error:
                data["error"] = subtask.error
            await self._send_block({
                "type": "block",
                "block_type": "plan_progress",
                "delta": json.dumps(data, ensure_ascii=False),
                "is_final": False,
                "metadata": {},
            })
        except Exception as e:
            logger.warning("推送 subtask_done 失败: %s", e)

    async def _send_progress(self, plan: Plan) -> None:
        """推送通用进度更新"""
        try:
            completed, total = plan.progress
            data = {
                "type": "progress",
                "status": "running",
                "completed": completed,
                "total": total,
            }
            await self._send_block({
                "type": "block",
                "block_type": "plan_progress",
                "delta": json.dumps(data, ensure_ascii=False),
                "is_final": False,
                "metadata": {},
            })
        except Exception as e:
            logger.warning("推送 progress 失败: %s", e)

    # ── 用户审核（预留） ──

    async def _wait_for_approval(self) -> bool:
        """等待用户审核计划

        通过 ws_server 接收前端 approve/reject 消息来设置 _approval_result。
        asyncio.Event 方式由 ws_server 的 plan_action handler 调用 set_approval_result()。
        如果 120 秒内无响应则自动拒绝。
        """
        await self._send_plan_review()
        # 如果有 asyncio.Event 接口则 await，否则轮询 _approval_result
        if self._approval_result is not None:
            return self._approval_result
        try:
            result = await asyncio.wait_for(
                self._approval_event.wait(),
                timeout=120.0,
            )
            return self._approval_result if self._approval_result is not None else False
        except asyncio.TimeoutError:
            logger.warning("Plan approval timed out after 120s, auto-rejecting")
            return False

    async def _send_plan_review(self) -> None:
        """推送计划审核请求到前端"""
        try:
            await self._send_block({
                "type": "block",
                "block_type": "plan_review",
                "delta": json.dumps({
                    "message": "请审核执行计划",
                    "options": ["approve", "reject", "modify"],
                }, ensure_ascii=False),
                "is_final": True,
                "metadata": {},
            })
        except Exception as e:
            logger.warning("推送 plan_review 失败: %s", e)

    def set_approval_result(self, approved: bool) -> None:
        """设置审核结果（由 ws_server 调用）"""
        self._approval_result = approved
        self._approval_event.set()
