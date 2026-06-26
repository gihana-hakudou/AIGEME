"""TaskManager — Agent 待办事项管理（定时/周期性提醒）

存储格式：reminders/{id}.md，YAML frontmatter 管理状态
状态机：pending → triggered → done (单次) / pending (重复，已重算下次时间)

注：不使用 YamlFrontmatter 类（该类只处理标准记忆字段），直接读写 YAML。
"""

import uuid
import yaml
import hashlib
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _dump_yaml(data: dict) -> str:
    """将 dict 序列化为 YAML frontmatter 字符串"""
    return yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False).strip()


def _load_yaml(content: str) -> tuple[dict, str]:
    """从可能的 frontmatter 内容中解析 (frontmatter_dict, body)"""
    if content.startswith("---\n"):
        parts = content.split("---\n", 2)
        if len(parts) >= 3:
            try:
                fm = yaml.safe_load(parts[1])
                return (fm if isinstance(fm, dict) else {}), parts[2].strip()
            except Exception:
                pass
    return {}, content.strip()


class TaskManager:
    """Agent 待办事项管理器"""

    TASK_DIR = "reminders"

    def __init__(self, memory_dir: Path) -> None:
        self._task_dir = memory_dir / self.TASK_DIR
        self._task_dir.mkdir(parents=True, exist_ok=True)

    # ── 公开 API ────────────────────────────────────────────────

    async def add(
        self,
        title: str,
        trigger_at: str,
        content: str = "",
        repeat: str | None = None,
    ) -> dict:
        """创建待办任务

        Args:
            title: 任务标题
            trigger_at: 触发时间 "HH:MM" 或 "YYYY-MM-DD HH:MM"
            content: 任务详情（Agent 可读）
            repeat: 重复模式 null/"daily"/"weekly"/"monthly"

        Returns:
            {"status": "ok", "result": {"id": "...", "title": "..."}}
        """
        task_id = str(uuid.uuid4())
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        body = content or title
        fm = {
            "id": task_id,
            "type": "reminder",
            "title": title,
            "trigger_at": trigger_at,
            "repeat": repeat or "",
            "status": "pending",
            "last_triggered": "",
            "source": "agent",
            "created": now,
            "updated": now,
        }
        file_path = self._task_dir / f"{task_id}.md"
        file_path.write_text(f"---\n{_dump_yaml(fm)}\n---\n\n{body}", encoding="utf-8")

        return {
            "status": "ok",
            "result": {"id": task_id, "title": title, "trigger_at": trigger_at, "repeat": repeat},
        }

    async def done(self, task_id: str) -> dict:
        """标记任务完成

        单次任务：status → done
        重复任务：重算下次 trigger_at，status → pending
        """
        file_path = self._task_dir / f"{task_id}.md"
        if not file_path.exists():
            return {"status": "error", "error": f"任务不存在: {task_id}"}

        fm, body = _load_yaml(file_path.read_text("utf-8"))
        if not fm:
            return {"status": "error", "error": f"任务文件损坏: {task_id}"}

        repeat = fm.get("repeat", "")
        now = datetime.now()

        if repeat == "daily":
            next_time = self._next_repeat(fm.get("trigger_at", ""), days=1)
            fm["status"] = "pending"
            fm["trigger_at"] = next_time
        elif repeat == "weekly":
            next_time = self._next_repeat(fm.get("trigger_at", ""), days=7)
            fm["status"] = "pending"
            fm["trigger_at"] = next_time
        elif repeat == "monthly":
            next_time = self._next_repeat(fm.get("trigger_at", ""), days=30)
            fm["status"] = "pending"
            fm["trigger_at"] = next_time
        else:
            fm["status"] = "done"

        fm["last_triggered"] = now.strftime("%Y-%m-%d %H:%M:%S")
        fm["updated"] = now.strftime("%Y-%m-%d %H:%M:%S")
        fm["checksum"] = hashlib.sha256(body.encode("utf-8")).hexdigest()

        file_path.write_text(f"---\n{_dump_yaml(dict(fm))}\n---\n\n{body}", encoding="utf-8")

        return {"status": "ok", "result": {"id": task_id, "status": fm["status"], "next_trigger": fm.get("trigger_at", "")}}

    async def cancel(self, task_id: str) -> dict:
        """取消任务"""
        file_path = self._task_dir / f"{task_id}.md"
        if not file_path.exists():
            return {"status": "error", "error": f"任务不存在: {task_id}"}

        fm, body = _load_yaml(file_path.read_text("utf-8"))
        if not fm:
            return {"status": "error", "error": f"任务文件损坏: {task_id}"}

        fm["status"] = "cancelled"
        fm["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        fm["checksum"] = hashlib.sha256(body.encode("utf-8")).hexdigest()

        file_path.write_text(f"---\n{_dump_yaml(dict(fm))}\n---\n\n{body}", encoding="utf-8")

        return {"status": "ok", "result": {"id": task_id, "status": "cancelled"}}

    async def list_tasks(self, status: str = "") -> dict:
        """列出任务（可按状态筛选）"""
        results = []
        for f in sorted(self._task_dir.glob("*.md")):
            content = f.read_text("utf-8")
            fm, body = _load_yaml(content)
            if not fm:
                continue
            if status and fm.get("status", "") != status:
                continue
            results.append({
                "id": f.stem,
                "title": fm.get("title", ""),
                "status": fm.get("status", ""),
                "trigger_at": fm.get("trigger_at", ""),
                "repeat": fm.get("repeat", "") or None,
                "last_triggered": fm.get("last_triggered", "") or None,
                "preview": body.strip()[:60],
            })
        return {"status": "ok", "result": {"count": len(results), "tasks": results}}

    def scan_due(self, now: datetime | None = None) -> list[dict]:
        """扫描到期待办，返回待注入的任务列表

        条件：status=pending AND trigger_at <= now
        扫描后自动将触发状态改为 triggered（同步操作，文件读写轻量）
        """
        if now is None:
            now = datetime.now()
        due: list[dict] = []

        for f in sorted(self._task_dir.glob("*.md")):
            try:
                content = f.read_text("utf-8")
            except Exception:
                continue
            fm, body = _load_yaml(content)
            if not fm:
                continue
            if fm.get("status") != "pending":
                continue

            ta = fm.get("trigger_at", "")
            if not ta:
                continue

            if self._is_due(ta, now):
                task_info = {
                    "id": f.stem,
                    "title": fm.get("title", ""),
                    "trigger_at": ta,
                    "repeat": fm.get("repeat", "") or None,
                    "content": body.strip()[:200],
                }
                due.append(task_info)

                # 标记为 triggered
                fm["status"] = "triggered"
                fm["last_triggered"] = now.strftime("%Y-%m-%d %H:%M:%S")
                fm["updated"] = now.strftime("%Y-%m-%d %H:%M:%S")
                fm["checksum"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
                try:
                    f.write_text(f"---\n{_dump_yaml(dict(fm))}\n---\n\n{body}", encoding="utf-8")
                except Exception:
                    pass
                logger.info("[TASK] 待办到期 injected: %s (%s)", task_info["title"], ta)

        return due

    # ── 内部辅助 ────────────────────────────────────────────────

    @staticmethod
    def _is_due(trigger_at: str, now: datetime) -> bool:
        """判断是否到期

        支持两种格式：
        - "HH:MM"         → 每日时间点
        - "YYYY-MM-DD HH:MM" → 绝对时间点
        """
        trigger = trigger_at.strip()
        if not trigger:
            return False

        # 尝试解析 "HH:MM"（每日时间）
        if len(trigger) == 5 and ":" in trigger:
            try:
                t = datetime.strptime(trigger, "%H:%M")
                today_target = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
                return now >= today_target
            except ValueError:
                pass

        # 尝试解析 "YYYY-MM-DD HH:MM"
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                target = datetime.strptime(trigger, fmt)
                return now >= target
            except ValueError:
                continue

        return False

    @staticmethod
    def _next_repeat(trigger_at: str, days: int) -> str:
        """计算下次重复触发时间"""
        now = datetime.now()
        # 如果是 "HH:MM" 格式，加 days 天
        if len(trigger_at) == 5 and ":" in trigger_at:
            try:
                t = datetime.strptime(trigger_at, "%H:%M")
                next_time = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
                while next_time <= now:
                    next_time += timedelta(days=days)
                return next_time.strftime("%Y-%m-%d %H:%M")
            except ValueError:
                pass
        # 绝对时间 + days
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
            try:
                target = datetime.strptime(trigger_at, fmt)
                target += timedelta(days=days)
                return target.strftime("%Y-%m-%d %H:%M")
            except ValueError:
                continue
        # 兜底
        return (now + timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
