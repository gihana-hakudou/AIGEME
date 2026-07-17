"""上下文组装 — PromptAssembler（Fixed + Variable 两部分）"""

import json
import logging
from pathlib import Path

from core.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# 系统信息文件路径（由 main.py 启动时自动生成）
_SYSTEM_INFO_PATH: Path | None = None


def set_system_info_path(path: Path) -> None:
    """设置 system_info.md 路径（由 WSServer 在初始化时调用）"""
    global _SYSTEM_INFO_PATH
    _SYSTEM_INFO_PATH = path


class PromptAssembler:
    """Prompt 组装器：Fixed + Variable 两部分"""

    def __init__(
        self,
        character_dir: Path,
        user_md_path: Path,
        system_prompt_path: Path,
        tools_registry: ToolRegistry,
        memory_index: str = "",
        is_first_turn: bool = False,
        active_skills: list[dict] | None = None,
        memory_dir: Path | None = None,
        organize_interval: int = 8,
    ) -> None:
        self._character_dir = character_dir
        self._user_md_path = user_md_path
        self._system_prompt_path = system_prompt_path
        self._tools_registry = tools_registry
        self._memory_index = memory_index
        self._is_first_turn = is_first_turn
        self._active_skills = active_skills or []
        self._memory_dir = memory_dir
        self._organize_interval = organize_interval
        self._total_rounds_since_organize = self._load_counter()
        self._force_memory_tool = False  # 组织提醒触发时，强制模型调 memory 工具
        self._cached_system_prompt: str | None = None

    # ── 持久化轮次计数器 ──────────────────────────

    @property
    def _counter_path(self) -> Path | None:
        if not self._memory_dir:
            return None
        return self._memory_dir / ".organize_counter"

    def reset_organize_counter(self) -> None:
        """agent 调用记忆工具后由 RaActLoop 调用，重置整理提醒计数器"""
        logger = __import__("logging").getLogger(__name__)
        if self._total_rounds_since_organize > 0:
            logger.info("agent 使用了记忆工具，重置 organize_counter")
            self._total_rounds_since_organize = 0
            self._save_counter()

    def _load_counter(self) -> int:
        """从持久化文件加载轮次计数器"""
        path = self._counter_path
        if path and path.exists():
            try:
                return int(path.read_text("utf-8").strip())
            except (OSError, ValueError):
                pass
        return 0

    def _save_counter(self) -> None:
        """保存轮次计数器到持久化文件"""
        path = self._counter_path
        if path:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(str(self._total_rounds_since_organize), encoding="utf-8")
            except OSError:
                logger.warning("保存 organize 计数器失败")

    def build_system_prompt(self) -> str:
        """组装 System Message 的固定部分（session 生命周期内不变，KV 缓存始终有效）

        仅包含固定内容，不包含任何可能每轮变化的信息。
        可变/动态内容通过 build_variable_content() 单独获取，由调用方注入为 user 消息。

        首次构建后缓存结果，后续直接返回缓存，确保 system prompt 在 session 内完全一致。
        """
        if self._cached_system_prompt is not None:
            return self._cached_system_prompt

        parts: list[str] = []

        # 1. 行为准则
        system_md = self._read_text(self._system_prompt_path)
        parts.append(system_md)

        # 2. 角色设定
        soul_path = self._character_dir / "soul.md"
        if soul_path.exists():
            parts.append(f"## 角色设定\n\n{self._read_text(soul_path)}")

        # 3. 用户信息（文件为空时不注入）
        if self._user_md_path.exists():
            user_content = self._read_text(self._user_md_path).strip()
            if user_content:
                parts.append(f"## 用户信息\n\n{user_content}")

        # 4. 记忆概览（仅载入最近 N 条，不再全量注入）
        overview = self._build_memory_overview(max_entries=20)
        if overview:
            parts.append(f"## 记忆系统\n\n{overview}")

        # 5. 可用表情（格式见 system.md，此处仅列出当前角色可用表情）
        expression_list = self._get_available_expressions()
        if expression_list:
            expr_str = ", ".join(expression_list)
            parts.append(
                f"## 可用表情\n\n"
                f"当前角色可用: {expr_str}\n"
            )

        # 6. 已加载技能 — 优先从 SkillManager 动态获取
        dynamic_skills = self._get_dynamic_skills()
        active_skills = dynamic_skills if dynamic_skills else self._active_skills
        if active_skills:
            skill_lines = [
                f"- {s.get('name')}: {s.get('description')}" for s in active_skills
            ]
            parts.append("## 已加载技能\n\n" + "\n".join(skill_lines))

        # 7. 系统环境信息（自动检测，启动后不变）
        if _SYSTEM_INFO_PATH and _SYSTEM_INFO_PATH.exists():
            try:
                system_text = _SYSTEM_INFO_PATH.read_text("utf-8").strip()
                if system_text:
                    parts.append(system_text)
            except Exception:
                pass

        # 9. 工作区目录（按角色隔离）
        project_root = self._character_dir.parent.parent
        char_id = self._character_dir.name
        workspace_dir = project_root / ".AIGEME" / ".data" / "local" / char_id / "workspace"
        parts.append(f"## 工作区\n\n你的工作区目录: {workspace_dir}")

        self._cached_system_prompt = "\n\n".join(parts)
        return self._cached_system_prompt

    def build_variable_content(self) -> str | None:
        """组装本轮可变/动态内容（作为 user 消息注入，不污染 system KV cache）

        统一管理所有每轮可能变化的信息，避免分散在 loop.py 中手动拼 user 消息。

        当前包含：
        - 时间信息
        - 工具优先指令（每轮注入，提高 LLM 注意力权重）
        - 记忆初始化提示（首次对话检查 MEMORY.md 是否已建立）
        - 记忆整理提醒（纯轮次触发）

        注意：TTS 格式指导不再在此动态注入，依赖 system prompt 固定规则 + 历史消息带标签的格式感染。
        """
        parts: list[str] = []

        # 时间信息
        from datetime import datetime
        now = datetime.now()
        weekdays = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']
        time_str = now.strftime(f'%Y年%m月%d日 %H:%M {weekdays[now.weekday()]}')
        parts.append(f"_（系统信息）_\n当前时间: {time_str}")

        # 工具优先规则 + 无工具则回复
        parts.append(
            "开始任务前，检查可用工具和技能是否与用户意图相关。"
            "如果存在相关工具，必须立即调用；如果没有，直接回复用户。"
        )

        # 记忆行为规范提醒（完整规范在 tool memory 的描述中）
        parts.append(
            "使用 memory 工具管理长期记忆：新增信息、建立链接、定期审计、清理冗余。"
            "具体规范见 memory 工具描述。"
        )

        # 记忆初始化检查（首次对话检查 MEMORY.md 是否已建立）
        if self._is_first_turn:
            memory_file = self._character_dir.parent.parent / ".AIGEME" / ".data" / "local" / self._character_dir.name / "memory" / "MEMORY.md"
            if not memory_file.exists():
                parts.append(
                    "首次对话，长期记忆尚未建立。"
                    "对话中主动使用 memory 工具记录重要信息。"
                )

        # 记忆整理提醒（轮次触发，但 agent 主动用过记忆工具会外部重置）
        self._total_rounds_since_organize += 1
        self._save_counter()
        logger.info("[ORGANIZE] round=%d, interval=%d, force=%s",
            self._total_rounds_since_organize, self._organize_interval, self._force_memory_tool)
        if self._total_rounds_since_organize >= self._organize_interval:
            parts.append(
                f"你已经 {self._organize_interval} 轮没有整理过记忆了。"
                "通过 memory 工具将最近成功或失败的经历、值得记录的信息作为新记忆保存，"
                "同时归档过期条目、修改错误信息、合并重复内容。"
            )
            self._total_rounds_since_organize = 0
            self._save_counter()
            self._force_memory_tool = True  # 标记本轮需要强制调 memory 工具

        # ⏰ 待办事项到期提醒
        if self._memory_dir:
            try:
                from core.memory.reminder import TaskManager
                tm = TaskManager(self._memory_dir)
                due = tm.scan_due()
                if due:
                    lines = ["## ⏰ 待处理事项"]
                    for t in due:
                        repeat_tag = f" ({t['repeat']})" if t.get("repeat") else ""
                        lines.append(f"- [{t['id']}] {t['content']}（原定 {t['trigger_at']}{repeat_tag}）")
                        lines.append(f"  完成: reminder(operation=done, id={t['id']}) / 取消: reminder(operation=cancel, id={t['id']})")
                    parts.append("\n".join(lines))
            except Exception:
                pass

        return "\n\n---\n\n".join(parts)


    def _get_available_expressions(self) -> list[str]:
        """扫描 tachi-e 目录获取可用表情列表"""
        tachi_dir = self._character_dir.parent.parent / "tachi-e"
        char_id = self._character_dir.name
        char_tachi = tachi_dir / char_id
        if not char_tachi.exists():
            return []
        return [f.stem for f in sorted(char_tachi.glob("*.png"))]

    def _get_dynamic_skills(self) -> list[dict]:
        """从 SkillManager 动态获取技能列表（如有）"""
        skill_tool = self._tools_registry.get("skill")
        if skill_tool and hasattr(skill_tool, "_manager") and skill_tool._manager:
            return skill_tool._manager.list_all()
        return []

    @staticmethod
    def _read_text(path: Path) -> str:
        """读取文件内容，不存在则返回空字符串"""
        try:
            return path.read_text("utf-8")
        except FileNotFoundError:
            return ""

    def _build_memory_overview(self, max_entries: int = 20) -> str | None:
        """从真实 MEMORY.md 中解析并返回最近 N 条记忆概览"""
        if not self._memory_dir:
            return None
        memory_file = self._memory_dir / "MEMORY.md"
        if not memory_file.exists():
            return None

        content = memory_file.read_text("utf-8").strip()
        if not content:
            return None

        entries: list[dict] = []
        lines = content.split("\n")
        current_section = ""
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.startswith("## "):
                current_section = line.strip("#").strip()

            # Detect table start: header row followed by separator row
            if line.startswith("|") and "|" in line[1:] and not line.startswith("|---"):
                # Check if next line is a separator
                header_row = line
                if i + 1 < len(lines) and lines[i + 1].startswith("|---"):
                    # Skip header and separator, parse data rows
                    i += 2
                    while i < len(lines):
                        row = lines[i]
                        if not row.startswith("|"):
                            break
                        if row.startswith("|---"):
                            i += 1
                            continue
                        cells = [c.strip() for c in row.split("|")[1:-1]]
                        if len(cells) >= 5:
                            name = cells[0]
                            if name and name != "(暂无)":
                                entries.append({
                                    "section": current_section,
                                    "name": name,
                                    "updated": cells[2],
                                    "referenced": cells[3],
                                    "summary": cells[4],
                                })
                        i += 1
                    continue  # already advanced i
            i += 1

        if not entries:
            return None

        from datetime import datetime, timedelta

        def _parse_date(d: str) -> datetime:
            if not d or d == "-":
                return datetime.min
            for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    return datetime.strptime(d, fmt)
                except ValueError:
                    continue
            return datetime.min

        now = datetime.now()

        for e in entries:
            d1 = _parse_date(e["updated"])
            d2 = _parse_date(e["referenced"])
            e["_sort_date"] = max(d1, d2)

            # 读取文件的 importance
            imp = 3  # 默认
            fpath = self._memory_dir / f"{e['name']}.md"
            if fpath.exists():
                try:
                    raw = fpath.read_text("utf-8")
                    if raw.startswith("---\n"):
                        import yaml
                        _, fm_str, _ = raw.split("---\n", 2)
                        fm = yaml.safe_load(fm_str)
                        if isinstance(fm, dict) and "importance" in fm:
                            imp = int(fm["importance"])
                except Exception:
                    pass

            # 复合分数：重要度(0-1) × 0.6 + 新鲜度(0-1) × 0.4
            imp_score = (imp - 1) / 4  # 1→0, 5→1
            days_since = max(0, (now - e["_sort_date"]).days) if e["_sort_date"] != datetime.min else 999
            recency_score = max(0, 1 - days_since / 30)  # 30天内新鲜
            e["_sort_score"] = round(imp_score * 0.6 + recency_score * 0.4, 2)

        entries.sort(key=lambda e: e["_sort_score"], reverse=True)
        entries = entries[:max_entries]

        # Format as compact markdown
        section_map: dict[str, list[str]] = {}
        for e in entries:
            tag = e["section"].replace("记忆", "").strip()
            if tag not in section_map:
                section_map[tag] = []
            summary_short = e["summary"][:80] if len(e["summary"]) > 80 else e["summary"]
            section_map[tag].append(
                f"- **{e['name']}**（更新: {e['updated']}, 引用: {e['referenced']}）: {summary_short}"
            )

        out_lines = [f"> 会话启动时加载，共 {len(entries)} 条。需检索完整记忆请使用 memory 工具。", ""]
        for tag in section_map:
            out_lines.append(f"**{tag}**")
            out_lines.extend(section_map[tag])
            out_lines.append("")

        return "\n".join(out_lines)

    def _replace_current_time(self, text: str) -> str:
        """将文本中的 {{current_time}} 替换为当前时间

        格式: 2026年06月14日 17:35 星期日
        """
        from datetime import datetime
        now = datetime.now()
        weekdays = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']
        time_str = now.strftime(f'%Y年%m月%d日 %H:%M {weekdays[now.weekday()]}')
        return text.replace('{{current_time}}', time_str)
