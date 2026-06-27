"""TaskManager — Agent 待办事项管理（定时/周期性提醒）

存储格式：reminders/{id}.md，YAML frontmatter 管理状态
状态机：pending → triggered → done (单次) / pending (重复，已重算下次时间)

注：不使用 YamlFrontmatter 类（该类只处理标准记忆字段），直接读写 YAML。
"""

import uuid
import yaml
import hashlib
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 星期解析映射（周一=0，周日=6）
_WEEKDAY_MAP = {
    "1": 0, "一": 0, "周一": 0, "星期一": 0,
    "2": 1, "二": 1, "周二": 1, "星期二": 1,
    "3": 2, "三": 2, "周三": 2, "星期三": 2,
    "4": 3, "四": 3, "周四": 3, "星期四": 3,
    "5": 4, "五": 4, "周五": 4, "星期五": 4,
    "6": 5, "六": 5, "周六": 5, "星期六": 5,
    "7": 6, "日": 6, "周日": 6, "星期日": 6,
}


def _parse_weekday(s: str) -> int:
    """将中文/数字星期转换为 python weekday（周一=0...周日=6）"""
    s = s.strip()
    if s in _WEEKDAY_MAP:
        return _WEEKDAY_MAP[s]
    # 尝试直接解析数字
    try:
        return (int(s) - 1) % 7
    except ValueError:
        return 0  # 默认周一


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

    def _resolve_task(self, task_id: str) -> Path | None:
        """按文件名（即标题）查找任务"""
        direct = self._task_dir / f"{task_id}.md"
        return direct if direct.exists() else None

    async def add(
        self,
        title: str,
        trigger_at: str,
        content: str = "",
        repeat: str | None = None,
    ) -> dict:
        """创建待办任务

        Args:
            title: 任务标题（agent 填的提醒内容）
            trigger_at: 触发时间
              "HH:MM" → 每日此时触发
              "YYYY-MM-DD HH:MM" → 单次触发
              "周N HH:MM" → 每周周N此时触发（如"周3 10:00"每周三）
              "星期一 HH:MM" → 每周此时触发（如"星期一 10:00"）
            content: 任务详情（Agent 可读）
            repeat: 重复模式 null/"daily"/"weekly"/"monthly"
              如果已通过 trigger_at 指定了周几，repeat 可省略

        Returns:
            {"status": "ok", "result": {"id": "...", "title": "..."}}
        """
        # 生成短 ID：8 位十六进制（用当前时间的微秒部分 + 随机数）
        _ts = datetime.now().strftime("%f")
        _rand = hashlib.md5(str(uuid.uuid4()).encode()).hexdigest()[:4]
        task_id = f"t{_ts}{_rand}"[:12]
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
        file_path = self._resolve_task(task_id)
        if not file_path:
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
        file_path = self._resolve_task(task_id)
        if not file_path:
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

    async def read_task(self, task_id: str) -> dict:
        """读取单个任务详情"""
        file_path = self._resolve_task(task_id)
        if not file_path:
            return {"status": "error", "error": f"任务不存在: {task_id}"}
        fm, body = _load_yaml(file_path.read_text("utf-8"))
        if not fm:
            return {"status": "error", "error": f"任务文件损坏: {task_id}"}
        return {
            "status": "ok",
            "result": {
                "id": task_id,
                "title": fm.get("title", ""),
                "status": fm.get("status", ""),
                "trigger_at": fm.get("trigger_at", ""),
                "repeat": fm.get("repeat", "") or None,
                "last_triggered": fm.get("last_triggered", "") or None,
                "created": fm.get("created", ""),
                "content": body.strip(),
            },
        }

    def scan_due(self, now: datetime | None = None) -> list[dict]:
        """扫描到期待办，返回待注入的任务列表

        包含两种任务：
        - pending 且 trigger 时间已到 → 自动改为 triggered 后返回
        - 已 triggered 但未 done/cancelled → 继续返回（防止被 LLM 忽略后丢失）
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
            status = fm.get("status", "")

            # done / cancelled 跳过
            if status in ("done", "cancelled"):
                continue

            ta = fm.get("trigger_at", "")
            if not ta:
                continue

            # pending 且到期 → 标记 triggered，加入列表
            if status == "pending" and self._is_due(ta, now):
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

            # triggered 未 done → 继续出现，直到被处理
            elif status == "triggered":
                due.append({
                    "id": f.stem,
                    "title": fm.get("title", ""),
                    "trigger_at": ta,
                    "repeat": fm.get("repeat", "") or None,
                    "content": body.strip()[:200],
                })

        return due

    # ── 内部辅助 ────────────────────────────────────────────────

    @staticmethod
    def _is_due(trigger_at: str, now: datetime) -> bool:
        """判断是否到期

        支持格式：
        - "HH:MM"            → 每日时间点
        - "YYYY-MM-DD HH:MM" → 绝对时间点
        - "周N HH:MM"        → 每周周N（如"周3 10:00"）
        - "N HH:MM"          → 每月第N日（如"15 10:00"每月15号）
        """
        trigger = trigger_at.strip()
        if not trigger:
            return False

        # 尝试解析 "周N HH:MM" 或 "星期一 HH:MM"（每周）
        weekday_match = re.match(
            r'^(周[一二三四五六日日]|星期[一二三四五六日日]|[1-7])\s+(\d{1,2}:\d{2})$',
            trigger,
        )
        if weekday_match:
            day_str = weekday_match.group(1)
            time_str = weekday_match.group(2)
            target_dow = _parse_weekday(day_str)
            try:
                t = datetime.strptime(time_str, "%H:%M")
                # 找到下一个 target_dow
                days_ahead = (target_dow - now.weekday()) % 7
                if days_ahead == 0:
                    today_target = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
                    return now >= today_target
                return False  # 还没到那天
            except ValueError:
                pass

        # 尝试解析 "N HH:MM"（每月第N日，如"15 10:00"）
        day_match = re.match(r'^(\d{1,2})\s+(\d{1,2}:\d{2})$', trigger)
        if day_match:
            day_str = day_match.group(1)
            time_str = day_match.group(2)
            target_day = int(day_str)
            try:
                t = datetime.strptime(time_str, "%H:%M")
                today_target = now.replace(day=target_day, hour=t.hour, minute=t.minute, second=0, microsecond=0)
                return now >= today_target
            except ValueError:
                pass

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
        # 检测周几格式："周3 HH:MM" 或 "星期一 HH:MM"
        weekday_match = re.match(
            r'^(周[一二三四五六日日]|星期[一二三四五六日日]|[1-7])\s+(\d{1,2}:\d{2})$',
            trigger_at,
        )
        if weekday_match:
            day_str = weekday_match.group(1)
            time_str = weekday_match.group(2)
            target_dow = _parse_weekday(day_str)
            try:
                t = datetime.strptime(time_str, "%H:%M")
                next_time = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
                while next_time <= now or next_time.weekday() != target_dow:
                    next_time += timedelta(days=1)
                return next_time.strftime("%Y-%m-%d %H:%M")
            except ValueError:
                pass
        # 检测每月N号格式："N HH:MM"
        day_match = re.match(r'^(\d{1,2})\s+(\d{1,2}:\d{2})$', trigger_at)
        if day_match:
            time_str = day_match.group(2)
            target_day = int(day_match.group(1))
            try:
                t = datetime.strptime(time_str, "%H:%M")
                next_time = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
                while next_time <= now or next_time.day != target_day:
                    next_time += timedelta(days=1)
                return next_time.strftime("%Y-%m-%d %H:%M")
            except ValueError:
                pass
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
