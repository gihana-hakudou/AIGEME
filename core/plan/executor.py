"""ExecutorFactory — 创建隔离的 RaActLoop 实例"""

import copy
import logging
import re
from typing import Any

from core.raact_loop.loop import RaActLoop

logger = logging.getLogger(__name__)

# 安全的工具子集（白名单）
SAFE_TOOL_SUBSET: list[str] = [
    "bash", "read", "write", "glob", "grep",
    "web_search", "web_fetch", "memory",
    "task_list", "task_create", "task_update",
]


def _sanitize_sensitive_info(text: str) -> str:
    """脱敏敏感信息

    替换文本中的 API 密钥、密码、Bearer Token 等敏感信息。

    Args:
        text: 原始文本

    Returns:
        脱敏后的文本
    """
    # 替换 api_key=xxx 模式
    text = re.sub(
        r'(api_key\s*[=:]\s*)["\']?[a-zA-Z0-9_\-]{8,}["\']?',
        r'\1[API_KEY]',
        text,
    )
    # 替换 password=xxx 模式
    text = re.sub(
        r'(password\s*[=:]\s*)["\']?.+?["\']?(?:\s|$)',
        r'\1[PASSWORD]',
        text,
    )
    # 替换 Bearer token
    text = re.sub(
        r'Bearer\s+[a-zA-Z0-9_\-.]{10,}',
        'Bearer [BEARER_TOKEN]',
        text,
    )
    # 替换 sk- 开头的密钥
    text = re.sub(
        r'\bsk[a-zA-Z0-9]{8,}\b',
        '[API_KEY]',
        text,
    )
    return text


class ExecutorFactory:
    """创建隔离的 RaActLoop 实例，复用主循环的 PromptAssembler

    子 Agent 和主循环的区别：
    - 对话历史 history=[]，干净不继承
    - 任务指令由 _build_executor_message 构建
    - System Prompt 完全相同（复用 PromptAssembler）
    """

    def __init__(
        self,
        raact_loop_kwargs: dict[str, Any],
        config: dict | None = None,
    ) -> None:
        """初始化 ExecutorFactory

        Args:
            raact_loop_kwargs: 传给 RaActLoop 构造函数的参数字典
                              （至少包含 instructor, registry, prompt_assembler）
            config: 可选配置
        """
        self._raact_loop_kwargs = raact_loop_kwargs
        self._config = config or {}

    def create(self) -> RaActLoop:
        """创建一个新的 RaActLoop 实例

        关键：复用同一个 PromptAssembler，确保子 Agent 的 System Prompt
        包含完整的角色设定、用户信息、记忆快照、工具列表。
        """
        truncate_length = self._config.get("truncate_tool_content_length", 500)
        return RaActLoop(
            instructor=self._raact_loop_kwargs["instructor"],
            registry=self._raact_loop_kwargs["registry"],
            prompt_assembler=copy.copy(self._raact_loop_kwargs["prompt_assembler"]),
            memory_dir=self._config.get("memory_dir"),
            context_window=self._config.get("context_window") or 128000,
            token_limit_ratio=self._config.get("token_limit_ratio", 0.9),
            truncate_length=truncate_length,
        )


class PlanExecutorFactory(ExecutorFactory):
    """Plan Executor 工厂 — 与 PlanLoop 配合的子 Agent 创建器"""

    def create_executor(self) -> RaActLoop:
        """创建执行子任务的 RaActLoop 实例

        与 create() 相同，但提供更明确的语义名称以与 PlanLoop 协作。
        """
        return self.create()


class PlanExecutor(RaActLoop):
    """PlanExecutor — 执行子任务的 RaAct 循环

    作为 PlanLoop 的子 Agent 执行器，职责与 RaActLoop 相同。
    提供别名以在 Plan 上下文中保持语义清晰。
    """
    pass
