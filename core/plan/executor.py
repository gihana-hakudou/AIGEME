"""ExecutorFactory — 创建隔离的 RaActLoop 实例"""

import copy
import logging
from typing import Any

from core.raact_loop.loop import RaActLoop

logger = logging.getLogger(__name__)


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
