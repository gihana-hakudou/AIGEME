"""ReminderTool — 独立的待办/提醒管理工具

从 MemoryTool 中解耦出的独立工具，LLM 可直接调用
``reminder(operation="add", content="...")`` 管理定时提醒。

内部使用 TaskManager 进行数据存储（reminders/ 子目录）。
"""

import logging
from pathlib import Path

from core.memory.reminder import TaskManager
from core.tools.base import BaseTool

logger = logging.getLogger(__name__)

# 数据目录
MEMORY_BASE = Path(__file__).parent.parent.parent / ".AIGEME" / ".data"


def _get_memory_dir(user_id: str = "local", char_id: str = "ario") -> Path:
    """获取记忆目录（reminders/ 子目录由 TaskManager 自动创建）"""
    d = MEMORY_BASE / user_id / char_id / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return d


class ReminderTool(BaseTool):
    """待办/提醒管理工具"""

    name = "reminder"
    description = (
        "待办事项和定时提醒管理工具。支持新增/完成/取消/浏览/读取提醒。\n\n"
        "## 使用规范\n\n"
        "1. 当需要定时提醒或设置待办时，调用此工具。\n"
        "2. `add` 新增提醒，必须同时提供 `content`（提醒内容）和 `trigger_at`（触发时间）。\n"
        "3. `done` 标记提醒完成，需要 `id` 参数。\n"
        "4. `cancel` 取消提醒，需要 `id` 参数。\n"
        "5. `list` 浏览所有提醒，可用 `status` 筛选（pending/triggered/done/cancelled）。\n"
        "6. `read` 查看单个提醒详情，需要 `id` 参数。\n\n"
        "## trigger_at 格式\n\n"
        '- `"HH:MM"` - 每天此时触发\n'
        '- `"YYYY-MM-DD HH:MM"` - 单次绝对时间\n'
        '- `"周3 10:00"` 或 `"星期三 10:00"` - 每周此时触发\n'
        '- `"15 10:00"` - 每月 15 号此时触发\n\n'
        "## repeat 模式\n\n"
        "可选的重复模式：daily（每天）/ weekly（每周）/ monthly（每月）。\n"
        "如果 trigger_at 已通过周几或日期指定了重复规律，repeat 可省略。"
    )

    def __init__(self) -> None:
        super().__init__()
        self._char_id: str = "ario"

    def set_char_id(self, char_id: str) -> None:
        """由 ws_server 在连接建立时设置当前角色 ID"""
        self._char_id = char_id

    parameters = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["add", "done", "cancel", "list", "read"],
                "description": "add=新增 / done=完成 / cancel=取消 / list=浏览 / read=查看详情",
            },
            "content": {
                "type": "string",
                "description": "提醒内容。add 时必填",
            },
            "trigger_at": {
                "type": "string",
                "description": '触发时间 "HH:MM" 或 "YYYY-MM-DD HH:MM" 或 "周3 10:00" 或 "15 10:00"（add 时必填）',
            },
            "repeat": {
                "type": "string",
                "enum": ["daily", "weekly", "monthly"],
                "description": "重复模式: daily=每天 / weekly=每周 / monthly=每月（add 可选，trigger_at 设了周几/几日则自动识别）",
            },
            "id": {
                "type": "string",
                "description": "提醒标识。done/cancel/read 时必填",
            },
            "status": {
                "type": "string",
                "enum": ["pending", "triggered", "done", "cancelled"],
                "description": "按状态筛选。list 时可选",
            },
        },
        "required": ["operation"],
    }

    async def execute(  # type: ignore[override]
        self,
        operation: str,
        content: str | None = None,
        trigger_at: str | None = None,
        repeat: str | None = None,
        id: str | None = None,
        status: str | None = None,
        **kwargs,
    ) -> dict:
        memory_dir = _get_memory_dir(char_id=self._char_id)
        tm = TaskManager(memory_dir)

        if operation == "add":
            _title = content or ""
            if not _title or not trigger_at:
                return {
                    "status": "error",
                    "error": "add 需要 content（提醒内容）和 trigger_at 参数",
                }
            return await tm.add(title=_title, trigger_at=trigger_at, content=_title, repeat=repeat)

        if operation == "done":
            _task_id = id or ""
            if not _task_id:
                return {"status": "error", "error": "done 需要 id 参数"}
            return await tm.done(_task_id)

        if operation == "cancel":
            _task_id = id or ""
            if not _task_id:
                return {"status": "error", "error": "cancel 需要 id 参数"}
            return await tm.cancel(_task_id)

        if operation == "list":
            status_filter = status or ""
            return await tm.list_tasks(status_filter)

        if operation == "read":
            _task_id = id or ""
            if not _task_id:
                return {"status": "error", "error": "read 需要 id 参数"}
            return await tm.read_task(_task_id)

        return {"status": "error", "error": f"不支持的操作: {operation}"}
