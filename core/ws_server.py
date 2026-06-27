"""WebSocket 服务器 — 连接管理 + Session 管理"""

import asyncio
import json
import logging
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from core.character.loader import CharacterDef, CharacterLoader
from core.config.settings import get_config
from core.engine.context import PromptAssembler
from core.engine.instructor_client import InstructorClient
from core.persistence import Persistence
from core.protocols.blocks import Block, ClientMessage
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from core.raact_loop.loop import RaActLoop
from core.tools.registry import ToolRegistry
from core.tools.skill_tools import SkillManager

logger = logging.getLogger(__name__)


# === 诊断：写文件日志（绕过 uvicorn reload 输出问题） ===
_DIAG_LOG = Path(__file__).resolve().parent.parent / "diag_ws.log"
_DIAG_MAX_BYTES = 10 * 1024 * 1024  # 10MB


def _diag(msg: str) -> None:
    """写诊断日志到文件（超过10MB自动轮转）"""
    try:
        log_path = _DIAG_LOG
        if log_path.exists() and log_path.stat().st_size > _DIAG_MAX_BYTES:
            log_path.rename(log_path.with_suffix(".log.1"))
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{__import__('datetime').datetime.now()}] {msg}\n")
            f.flush()
    except Exception:
        pass


@dataclass
class Session:
    """WebSocket 会话，每个连接对应一个 Session"""

    ws: WebSocket
    user_id: str = "local"
    char_id: str = "ario"
    is_first_turn: bool = True
    history: list[Any] = field(default_factory=list)
    character: CharacterDef | None = None
    raact_loop: RaActLoop | None = None
    persistence: Persistence | None = None
    cancelled: bool = False
    pending_confirm: asyncio.Event | None = None
    confirm_result: str = ""
    raact_running: bool = False
    raact_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    raact_task: asyncio.Task | None = None
    message_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    # PAE Plan-and-Execute
    plan_loop: Any | None = None

    async def send_block(self, block: Block) -> None:
        """推送 Block 到 WebSocket"""
        try:
            await self.ws.send_json(block.model_dump())
        except Exception as e:
            logger.error("发送 Block 失败: %s", e)


class WSServer:
    """WebSocket 连接管理器"""

    def __init__(
        self,
        project_root: Path,
        registry: ToolRegistry,
        multimodal: bool = True,
    ) -> None:
        self._project_root = project_root
        self._registry = registry
        self._multimodal = multimodal
        self._sessions: dict[str, Session] = {}
        self._instructor: InstructorClient | None = None
        # 确认令牌存储：token → {session_id, created_at}
        self._confirm_tokens: dict[str, dict] = {}
        # 设置 system_info 路径供 PromptAssembler 引用
        from core.engine.context import set_system_info_path
        system_info_path = project_root / ".AIGEME" / ".data" / "system" / "system_info.md"
        set_system_info_path(system_info_path)
        _diag(f"WSServer.__init__ project_root={project_root}")

    @staticmethod
    def _process_images(images: list[str]) -> list[dict]:
        """同步处理图片：缩放至 1024px、转 JPEG/base64（在 run_in_executor 中执行）"""
        import base64
        import io
        import logging
        from PIL import Image

        ws_logger = logging.getLogger(__name__)
        result: list[dict] = []
        for i, b64_img in enumerate(images):
            try:
                img_data = base64.b64decode(b64_img)
                with Image.open(io.BytesIO(img_data)) as pil_img:

                    # 缩放至最大 1024px
                    max_size = 1024
                    if pil_img.width > max_size or pil_img.height > max_size:
                        ratio = min(max_size / pil_img.width, max_size / pil_img.height)
                        pil_img = pil_img.resize(
                            (int(pil_img.width * ratio), int(pil_img.height * ratio)),
                        )

                    # 转回 base64（JPEG 压缩）
                    buf = io.BytesIO()
                    pil_img.save(buf, format="JPEG", quality=85)
                    resized_b64 = base64.b64encode(buf.getvalue()).decode()

                    result.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{resized_b64}"},
                    })
                    ws_logger.info("图片 %d 已处理: %dx%d", i, pil_img.width, pil_img.height)
            except Exception as e:
                ws_logger.error("图片 %d 处理失败: %s", i, e)
        return result

    def reset_instructor(self) -> None:
        """强制重置 InstructorClient（前端保存设置后调用，使新配置立即生效）"""
        self._instructor = None
        _diag("reset_instructor: InstructorClient 已重置，下次请求时将重建")

    async def _ensure_instructor(self) -> InstructorClient:
        """懒初始化 InstructorClient"""
        if self._instructor is None:
            config = get_config()
            llm_config = config.get("llm", {})
            _diag(f"_ensure_instructor: config llm={llm_config}")
            api_key = llm_config.get("api_key")
            if api_key is not None and not isinstance(api_key, str):
                api_key = str(api_key)
            model = llm_config.get("model", "gpt-4o-mini")
            # 判断是否为原生 provider：看 model 前缀是否在 PROVIDER_DEFAULTS 中有 native=True
            native_provider = False  # 默认走 OpenAI 兼容路由（安全）
            if "/" in model:
                _p = model.split("/", 1)[0].strip().lower()
                try:
                    from core.main import PROVIDER_DEFAULTS
                    defaults = PROVIDER_DEFAULTS.get(_p, {})
                    if defaults.get("native", False) is True:
                        native_provider = True
                except Exception:
                    pass
            # 非原生 provider（本地部署）未配 key 时，传占位符满足 OpenAI 客户端要求
            if not api_key and not native_provider:
                api_key = "not-needed"
            self._instructor = InstructorClient(
                model=model,
                max_retries=llm_config.get("max_retries", 2),
                api_base=llm_config.get("api_base"),
                api_key=api_key,
                temperature=llm_config.get("temperature", 0.7),
                max_tokens=llm_config.get("max_tokens", 4096),
                presence_penalty=llm_config.get("presence_penalty"),
                frequency_penalty=llm_config.get("frequency_penalty"),
                top_p=llm_config.get("top_p"),
                top_k=llm_config.get("top_k"),
                preserve_thinking=bool(llm_config.get("preserve_thinking", False)),
                native_provider=native_provider,
            )
        return self._instructor

    async def handle_connection(self, ws: WebSocket, character_id: str = "ario") -> None:
        """处理 WebSocket 连接生命周期"""
        # ── 身份验证（可选） ──
        # 从 query 参数获取 token
        import os
        from urllib.parse import parse_qs, urlparse
        query = parse_qs(urlparse(str(ws.url)).query)
        token = query.get("token", [""])[0]
        expected_token = os.environ.get("AIGEME_WS_TOKEN", "")
        if expected_token and token != expected_token:
            await ws.close(code=4001, reason="unauthorized")
            return

        _diag(f"handle_connection: accept start, char_id={character_id}")
        await ws.accept()
        _diag("handle_connection: accept done")
        session_id = str(id(ws))
        session = Session(ws=ws, char_id=character_id)
        session.pending_confirm = asyncio.Event()
        session.confirm_result = ""
        self._sessions[session_id] = session
        self._registry.session_id = session_id
        _diag(f"handle_connection: session created, id={session_id}")

        # 推送 session_id 给前端（用于 HTTP 确认端点）
        await session.send_block(
            Block(block_type="system", delta=f"session_id:{session_id}", is_final=True)
        )

        try:
            session.user_id = get_config().get("user", {}).get("default_id", "local")
            _diag("handle_connection: user_id set")

            # 加载角色
            loader = CharacterLoader(self._project_root)
            session.character = loader.load_character(character_id)
            _diag("handle_connection: character loaded")

            # 初始化 SkillManager 并注入 SkillTool
            skill_manager = SkillManager(self._project_root, character_id)
            skill_tool = self._registry.get("skill")
            if skill_tool:
                skill_tool.set_manager(skill_manager)
            _diag("handle_connection: skill_manager init")

            # 设置 MemoryTool 的当前角色 ID，确保记忆按角色隔离
            memory_tool = self._registry.get("memory")
            if memory_tool:
                from core.memory.tools import MemoryTool as _MT
                if isinstance(memory_tool, _MT):
                    memory_tool.set_char_id(character_id)

            # 设置 DocumentTool 的当前角色 ID，确保文件写入按角色隔离
            document_tool = self._registry.get("document")
            if document_tool:
                from core.tools.document_tools import DocumentTool as _DT
                if isinstance(document_tool, _DT):
                    document_tool.set_char_id(character_id)
            _diag("handle_connection: memory_tool & document_tool char_id set")

            # 初始化持久化
            data_dir = self._project_root / ".AIGEME" / ".data"
            config = get_config()
            persistence_cfg = config.get("persistence", {})
            persistence = Persistence(
                data_dir=data_dir,
                user_id=session.user_id,
                char_id=character_id,
                max_turns=persistence_cfg.get("max_recent_records", 50),
                keep_tool_turns=persistence_cfg.get("keep_tool_turns", 10),
                truncate_tool_content_length=persistence_cfg.get("truncate_tool_content_length", 500),
            )
            session.persistence = persistence
            _diag("handle_connection: persistence init")

            # 加载历史对话
            history = await persistence.load_recent_history()
            session.history = history
            session.is_first_turn = len(history) == 0
            _diag(f"handle_connection: history loaded, len={len(history)}")

            # 读取记忆索引
            memory_index = await self._load_memory_index(session)
            _diag("handle_connection: memory_index loaded")

            # 如果 MEMORY.md 不存在则用模板内容初始化（不依赖 is_first_turn）
            memory_dir_check = data_dir / session.user_id / session.char_id / "memory"
            memory_file_check = memory_dir_check / "MEMORY.md"
            if not memory_file_check.exists():
                _diag(f"handle_connection: MEMORY.md not found at {memory_file_check}, creating from template")
                # 从模板读取内容
                template_path = self._project_root / "core" / "prompts" / "templates" / "memory.md"
                if template_path.exists():
                    template_content = template_path.read_text("utf-8")
                    # 用模板内容 + 分区表格初始化 MEMORY.md（模板在上，表格在下，`---` 分隔）
                    memory_file_check.parent.mkdir(parents=True, exist_ok=True)
                    from core.memory.index import MemoryIndex
                    idx = MemoryIndex(memory_dir_check)
                    await idx.write_initial_with_template(template_content, [
                        {"name": "事件记忆", "hint": "具体的经历和事件"},
                        {"name": "事实记忆", "hint": "用户的属性、偏好、背景信息"},
                        {"name": "过程记忆", "hint": "操作步骤、工作流程"},
                        {"name": "情感记忆", "hint": "情绪状态、情感倾向"},
                        {"name": "反思记忆", "hint": "思考、推理、结论"},
                    ])
                    _diag("handle_connection: initial MEMORY.md created from template + section tables")
                else:
                    # 兜底：用 5 类分区
                    from core.memory.index import MemoryIndex
                    idx = MemoryIndex(memory_dir_check)
                    await idx.write_initial_index([
                        {"name": "事件记忆", "hint": "具体的经历和事件"},
                        {"name": "事实记忆", "hint": "用户的属性、偏好、背景信息"},
                        {"name": "过程记忆", "hint": "操作步骤、工作流程"},
                        {"name": "情感记忆", "hint": "情绪状态、情感倾向"},
                        {"name": "反思记忆", "hint": "思考、推理、结论"},
                    ])
                    _diag("handle_connection: initial MEMORY.md created with 5 sections (fallback)")

            # 构建 PromptAssembler
            character_dir = self._project_root / "character" / character_id
            user_md_path = self._project_root / "character" / "user.md"
            system_prompt_path = (
                self._project_root / "core" / "prompts" / "templates" / "system.md"
            )
            config = get_config()
            memory_cfg = config.get("memory", {})
            data_dir_for_raact = self._project_root / ".AIGEME" / ".data"
            raact_memory_dir = data_dir_for_raact / session.user_id / session.char_id / "memory"

            assembler = PromptAssembler(
                character_dir=character_dir,
                user_md_path=user_md_path,
                system_prompt_path=system_prompt_path,
                tools_registry=self._registry,
                memory_index=memory_index,
                is_first_turn=session.is_first_turn,
                active_skills=(
                    [{"name": s, "description": ""} for s in session.character.skills]
                    if session.character
                    else []
                ),
                memory_dir=raact_memory_dir,
                organize_interval=memory_cfg.get("organize_interval", 8),
            )
            _diag("handle_connection: PromptAssembler built")

            # 初始化 RaAct 循环（注入 settings.yaml 配置 + memory_dir）
            instructor = await self._ensure_instructor()
            llm_cfg = config.get("llm", {})
            truncate_length = persistence_cfg.get("truncate_tool_content_length", 500)
            session.raact_loop = RaActLoop(
                instructor=instructor,
                registry=self._registry,
                prompt_assembler=assembler,
                memory_dir=raact_memory_dir,
                context_window=llm_cfg.get("context_window", 128000),
                token_limit_ratio=llm_cfg.get("token_limit_ratio", 0.9),
                truncate_length=truncate_length,
                keep_tool_turns=persistence_cfg.get("keep_tool_turns", 5),
            )
            session.raact_loop.set_cancelled_ref(lambda: session.cancelled)
            session.raact_loop.set_confirm_refs(
                lambda: session.pending_confirm,
                lambda: session.confirm_result,
                lambda: setattr(session, 'confirm_result', ''),
            )
            _diag("handle_connection: RaActLoop init done")

            # 注入 PlanAndExecuteTool 的依赖：session 生存期内可用
            from core.plan.tool import PlanAndExecuteTool
            PlanAndExecuteTool.set_session_context(
                session_id=session_id,
                instructor=instructor,
                registry=self._registry,
                prompt_assembler=assembler,
                send_block=session.send_block,
            )
            _diag("handle_connection: PlanAndExecuteTool context set")

            # 首次对话处理
            if session.is_first_turn and session.character:
                # 发送欢迎系统消息
                await session.send_block(
                    Block(block_type="system", delta="新对话已开始", is_final=True)
                )
                _diag("handle_connection: welcome block sent")

                # 处理 identity：作为第一条 AI 消息
                identity = session.character.identity
                if identity and identity.strip():
                    # 1. 推送到前端对话框
                    await session.send_block(
                        Block(block_type="speech", delta=identity.strip(), is_final=True)
                    )
                    _diag("handle_connection: identity sent to frontend")

                    # 2. 发送 turn_end，解锁前端输入框（否则打字机结束后 turnEnded=false，按钮卡住）
                    await session.send_block(
                        Block(block_type="turn_end", delta="", is_final=True, metadata={"cancelled": False})
                    )
                    _diag("handle_connection: turn_end sent after identity")

                    # 3. 持久化到历史对话
                    await session.persistence.save_turn(
                        role="assistant",
                        content=identity.strip(),
                        meta={"expression": "default"},
                    )
                    _diag("handle_connection: identity persisted")

                    # 4. 插入到 history 前面作为 AIMessage
                    from langchain_core.messages import AIMessage
                    session.history.insert(0, AIMessage(content=identity.strip()))
                    _diag("handle_connection: identity added to history")

            # 主消息循环
            _diag("handle_connection: entering _message_loop")
            await self._message_loop(session)

        except WebSocketDisconnect:
            logger.info("WebSocket 断开: %s (角色: %s)", session_id, character_id)
            _diag(f"WebSocketDisconnect: {session_id}")
        except Exception as e:
            tb = traceback.format_exc()
            _diag(f"EXCEPTION: {e!s}\n{tb}")
            logger.error("WebSocket 错误: %s\n%s", e, tb)
            try:
                await session.send_block(
                    Block(block_type="error", delta=f"服务器错误: {e!s}")
                )
            except Exception:
                pass
        finally:
            await self._cleanup_session(session_id)
            _diag(f"cleanup_session: {session_id}")

    async def _message_loop(self, session: Session) -> None:
        """主消息循环 — 接收用户消息 → 启动 RaAct 后台任务 → 处理控制消息"""
        _diag("_message_loop: started")
        while True:
            _diag("_message_loop: waiting for receive_text")
            try:
                raw = await session.ws.receive_text()
            except WebSocketDisconnect:
                _diag("_message_loop: WebSocketDisconnect, break")
                logger.info("_message_loop: WebSocket 断开")
                break
            except Exception as e:
                _diag(f"_message_loop: receive_text FAILED: {e!s}")
                logger.error("_message_loop: 接收消息失败: %s", e, exc_info=True)
                continue

            _diag(f"_message_loop: received raw={raw[:100]}")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                _diag(f"_message_loop: JSON parse FAILED: {e}")
                logger.warning("_message_loop: JSON 解析失败: %s", e)
                continue

            try:
                msg = ClientMessage(**data)
            except Exception as e:
                _diag(f"_message_loop: ClientMessage validate FAILED: {e}, data={data}")
                logger.warning("_message_loop: 消息验证失败: %s, data=%s", e, data)
                continue

            _diag(f"_message_loop: msg type={msg.type} content={msg.content[:50] if msg.content else 'empty'}")

            # ── ping ──
            if msg.type == "ping":
                await session.ws.send_json({"type": "pong"})
                _diag("_message_loop: pong sent")
                continue

            # ── cancel — 始终可处理（中断正在运行的 raact） ──
            if msg.type == "cancel":
                session.cancelled = True
                await session.ws.send_json({"type": "cancelled"})
                _diag("_message_loop: cancel handled")
                continue

            # ── plan_action — 计划审核操作（approve / reject） ──
            if msg.type == "plan_action":
                action = msg.plan_action or msg.content or ""
                _diag(f"_message_loop: plan_action received, action={action}")
                if session.plan_loop:
                    session.plan_loop.set_approval_result(action == "approve")
                    _diag(f"_message_loop: plan_action processed, approved={action == 'approve'}")
                else:
                    _diag("_message_loop: plan_loop not available, ignoring plan_action")
                continue

            # ── set_permission_mode — 动态切换权限模式 ──
            if msg.type == "set_permission_mode":
                mode = (msg.content or "normal").strip().lower()
                from core.tools.bash_tools import set_permission_mode
                set_permission_mode(mode)
                _diag(f"_message_loop: permission_mode set to {mode}")
                await session.ws.send_json({
                    "type": "permission_mode_set",
                    "mode": mode,
                })
                continue

            # ── disconnect ──
            if msg.type == "disconnect":
                _diag("_message_loop: disconnect, break")
                break

            # ── user_message ──
            if msg.type == "user_message":
                # 原子检查-设置：先设标志再执行 async 操作，避免另一个协程在同窗口期进入
                if session.raact_lock.locked():
                    # RaAct 正在运行，消息入队
                    _diag("_message_loop: raact 正在运行，消息入队列")
                    await session.message_queue.put(msg)
                    await session.send_block(
                        Block(block_type="system", delta="消息已加入队列，将在当前对话完成后处理")
                    )
                    continue

                await session.raact_lock.acquire()  # 获取锁
                session.raact_running = True

                session.cancelled = False
                if not msg.content:
                    _diag("_message_loop: empty user_message, skip")
                    session.raact_running = False
                    session.raact_lock.release()
                    continue

                _diag("_message_loop: saving user message")
                await session.persistence.save_turn(
                    role="user", content=msg.content, meta={"expression": "default"}
                )

                # === DEBUG: 确认消息到达 ===
                logger.debug("收到消息: %s", msg.content[:50])

                # 处理图片（多模态）：缩放至 1024px、转 JPEG/re-base64（在后台线程执行，不阻塞消息循环）
                image_contents: list[dict] = []
                if self._multimodal and msg.images:
                    image_contents = await asyncio.get_event_loop().run_in_executor(
                        None, self._process_images, msg.images
                    )

                # 在后台任务中执行 RaAct 循环，使消息循环能继续处理 confirm_response 等消息
                logger.info("[TOOL_DEBUG_WS] 后台启动 raact_stream, user_message=%s, history_len=%s",
                    msg.content[:50] if msg.content else "None", len(session.history))

                _diag("_message_loop: launching raact_stream as background task")
                session.raact_task = asyncio.create_task(
                    self._run_raact_task(
                        session=session,
                        user_message=msg.content,
                        history=session.history,
                        images=image_contents or None,
                    )
                )
                _diag("_message_loop: raact_stream background task launched")

    async def _run_raact_task(
        self,
        session: Session,
        user_message: str,
        history: list[Any],
        images: list[dict] | None = None,
    ) -> None:
        """后台运行 raact_stream，完成后持久化结果（不阻塞消息循环）"""
        import logging
        ws_logger = logging.getLogger(__name__)
        try:
            ws_logger.info("[TOOL_DEBUG_WS] 后台 raact_stream 开始")
            round_messages, final_say, accumulated_reasoning = await session.raact_loop.raact_stream(
                user_message=user_message,
                history=history,
                send_block=session.send_block,
                images=images,
            )
            ws_logger.info("[TOOL_DEBUG_WS] 后台 raact_stream 返回: final_say=%s, round_messages=%s, reasoning_count=%s",
                final_say[:80] if final_say else "None", len(round_messages), len(accumulated_reasoning))
        except asyncio.CancelledError:
            ws_logger.info("[TOOL_DEBUG_WS] 后台 raact_stream 被取消")
            raise
        except Exception as e:
            ws_logger.error("[TOOL_DEBUG_WS] 后台 raact_stream 失败: %s", e)
            _diag(f"_run_raact_task: raact_stream FAILED: {e!s}\n{traceback.format_exc()}")
            try:
                await session.send_block(
                    Block(block_type="error", delta=f"处理失败: {e!s}")
                )
            except Exception:
                pass
            return
        finally:
            # 检查队列中是否有待处理消息
            if session.message_queue.empty():
                session.raact_running = False
                session.raact_task = None
                session.raact_lock.release()
                _diag("_run_raact_task: raact_running=False, lock released")
            else:
                _diag("_run_raact_task: 队列非空，保留 lock 状态")

        # ── 持久化所有 in-loop 消息（保留 assistant→tool 交替顺序） ──
        _diag(f"_run_raact_task: raact_stream returned, saving results")

        # === Bug 5 修复：将当前用户消息加入 session.history 供下次回传 ===
        session.history.append(HumanMessage(content=user_message))
        # ================================================================

        session.history.extend(round_messages)

        # 保存 round_messages 中所有消息（按原始顺序，每个消息存为一个 turn）
        reasoning_idx = 0
        for msg in round_messages:
            if isinstance(msg, AIMessage):
                tc = msg.additional_kwargs.get("tool_calls")
                kwargs = {"role": "assistant", "content": msg.content or ""}
                if tc:
                    kwargs["tool_calls"] = tc
                # 从 accumulated_reasoning 取对应段的 reasoning 持久化
                if reasoning_idx < len(accumulated_reasoning):
                    part = accumulated_reasoning[reasoning_idx].strip()
                    if part:
                        kwargs["reasoning"] = part
                    reasoning_idx += 1
                await session.persistence.save_turn(**kwargs)
            elif isinstance(msg, ToolMessage):
                await session.persistence.save_turn(
                    role="tool",
                    content=msg.content,
                    tool_call_id=msg.tool_call_id,
                    tool_name=msg.additional_kwargs.get("tool_name", ""),
                    result=msg.content,
                )
        # 发送记忆更新信号（让前端刷新记忆面板）
        await session.send_block(
            Block(block_type="memory_update", delta="refresh", is_final=True)
        )

        # 发送工作区更新信号（让前端刷新工作区面板）
        await session.send_block(
            Block(block_type="workspace_update", delta="refresh", is_final=True)
        )

        session.is_first_turn = False
        _diag("_run_raact_task: turn completed")

        # 检查消息队列
        if not session.message_queue.empty():
            next_msg = await session.message_queue.get()
            _diag("_run_raact_task: 从队列取出下一条消息，启动新一轮处理")
            session.raact_task = asyncio.create_task(
                self._run_raact_task(
                    session=session,
                    user_message=next_msg.content,
                    history=session.history,
                    images=None,  # 队列消息不支持图片
                )
            )
            return  # 不设置 raact_task = None，新的任务接管

    async def _load_memory_index(self, session: Session) -> str:
        """加载记忆索引（通过 MemoryTool 实例缓存）"""
        data_dir = self._project_root / ".AIGEME" / ".data"
        memory_dir = data_dir / session.user_id / session.char_id / "memory"
        memory_tool = self._registry.get("memory")
        if memory_tool:
            from core.memory.tools import MemoryTool as _MT
            if isinstance(memory_tool, _MT):
                return await memory_tool.get_index_text(memory_dir)
        # 降级：直接读取文件
        memory_file = memory_dir / "MEMORY.md"
        return memory_file.read_text("utf-8") if memory_file.exists() else ""

    def get_session(self, session_id: str) -> Session | None:
        """通过 session_id 获取会话实例（供 HTTP 端点使用）"""
        return self._sessions.get(session_id)

    def create_confirm_token(self, session_id: str) -> str:
        """生成唯一的确认令牌，返回 token 字符串"""
        import time
        import uuid
        token = uuid.uuid4().hex[:16]
        self._confirm_tokens[token] = {
            "session_id": session_id,
            "created_at": time.time(),
        }
        # 清理过期令牌（超过5分钟）
        self._clean_expired_tokens()
        return token

    def resolve_confirm_token(self, token: str) -> str | None:
        """解析确认令牌，返回 session_id。无论成功与否都消耗令牌。"""
        entry = self._confirm_tokens.pop(token, None)
        return entry["session_id"] if entry else None

    def _clean_expired_tokens(self) -> None:
        """移除创建超过5分钟的过期确认令牌"""
        import time
        now = time.time()
        expired = [
            t for t, v in self._confirm_tokens.items()
            if now - v.get("created_at", 0) > 300
        ]
        for token in expired:
            del self._confirm_tokens[token]
        if expired:
            _diag(f"_clean_expired_tokens: removed {len(expired)} expired tokens")

    async def _cleanup_session(self, session_id: str) -> None:
        """清理 Session（等待正在运行的 raact 任务结束后再清理）"""
        session = self._sessions.get(session_id)
        if session and session.raact_task and not session.raact_task.done():
            session.raact_task.cancel()
            _diag(f"_cleanup_session: cancelled raact_task for {session_id}")
            try:
                await asyncio.wait_for(session.raact_task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                _diag(f"_cleanup_session: raact_task finished for {session_id}")
        self._sessions.pop(session_id, None)
        # 清理 PlanAndExecuteTool 的 session 上下文
        from core.plan.tool import PlanAndExecuteTool
        PlanAndExecuteTool.clear_session_context(session_id)
        # 清理过期确认令牌
        self._clean_expired_tokens()