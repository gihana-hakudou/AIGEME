"""YAML frontmatter 处理 — 注入/提取/校验/修复/更新"""

import hashlib
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


class YamlFrontmatter:
    """YAML frontmatter 处理 — 注入/提取/校验/修复/更新"""

    # 必须字段
    REQUIRED_FIELDS = ["id", "type", "created", "checksum"]
    # 可选字段
    OPTIONAL_FIELDS = ["updated", "tags", "links", "source", "round", "status", "title"]
    # 允许的类型枚举
    VALID_TYPES = {"event", "fact", "process", "emotion", "reflection", "decision", "summary", "preference"}
    # 允许的状态枚举
    VALID_STATUSES = {"active", "archived", "deprecated"}

    @staticmethod
    def inject(content: str, metadata: dict) -> str:
        """
        注入 YAML frontmatter。

        如果 content 已经有 ``---`` 开头，则替换现有 frontmatter。
        自动生成: id (UUID v4)、created、updated、checksum (SHA-256)。

        Args:
            content: 记忆正文
            metadata: 用户提供的元数据（type, tags, links, source, round）

        Returns:
            带 frontmatter 的完整文件内容：``---\\n{yaml_fields}\\n---\\n\\n{content}``
        """
        body = YamlFrontmatter._extract_body(content)

        # 构建 frontmatter 字典
        fm: dict = {}

        # 自动生成字段
        fm["id"] = YamlFrontmatter._generate_id()
        fm["created"] = YamlFrontmatter._now_str()
        fm["updated"] = fm["created"]
        fm["checksum"] = YamlFrontmatter._checksum(body)

        # 用户提供的元数据
        fm["type"] = metadata.get("type", "fact")
        fm["source"] = metadata.get("source", "user")
        fm["round"] = metadata.get("round", 0)
        fm["tags"] = metadata.get("tags", [])
        fm["links"] = metadata.get("links", [])
        fm["status"] = metadata.get("status", "active")
        if metadata.get("importance") is not None:
            fm["importance"] = metadata["importance"]
        if metadata.get("title"):
            fm["title"] = metadata["title"]

        # 序列化 YAML（sort_keys=False 保持字段顺序，allow_unicode 支持中文）
        yaml_str = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False).strip()

        return f"---\n{yaml_str}\n---\n\n{body}"

    @staticmethod
    def extract(file_path: Path) -> tuple[dict, str]:
        """
        从文件中解析 YAML frontmatter。

        Args:
            file_path: 文件路径

        Returns:
            (frontmatter_dict, body_text)
            如果文件不存在、无 frontmatter 或格式错误，返回 ({}, full_text)
        """
        if not file_path.exists():
            return {}, ""

        content = file_path.read_text("utf-8")
        return YamlFrontmatter.extract_io(content)

    @staticmethod
    def extract_io(content: str) -> tuple[dict, str]:
        """
        从字符串中解析 YAML frontmatter（方便测试和内部分析）。

        Args:
            content: 完整的文件内容字符串

        Returns:
            (frontmatter_dict, body_text)
            如果无 frontmatter 或格式错误，返回 ({}, full_text)
        """
        if not content.startswith("---\n"):
            return {}, content

        lines = content.split("\n")
        end_idx = -1
        for i, line in enumerate(lines):
            if i > 0 and line.strip() == "---":
                end_idx = i
                break

        if end_idx < 0:
            # 有开始标记但无结束标记 → 格式错误
            return {}, content

        yaml_text = "\n".join(lines[1:end_idx])
        body = "\n".join(lines[end_idx + 1:]).strip()

        try:
            fm = yaml.safe_load(yaml_text)
            if not isinstance(fm, dict):
                # 空 YAML 或非 dict 结构 → 返回空 dict
                return {}, body
            return fm, body
        except yaml.YAMLError:
            return {}, body

    @staticmethod
    def validate(content: str, original_fm: dict | None = None) -> dict:
        """
        三层校验 YAML frontmatter。

        Layer 1 — 格式完整性：检查是否以 ``---\\n`` 开头
        Layer 2 — YAML 结构：``yaml.safe_load`` 解析
        Layer 3 — 字段完整性：检查 ``REQUIRED_FIELDS`` 都存在

        Args:
            content: 完整的文件内容
            original_fm: 原始 frontmatter（可选，用于恢复）

        Returns:
            ``{"status": str, "frontmatter": dict, "note": str}``
            status 取值: ``ok`` / ``recovered`` / ``repaired`` / ``patched`` / ``fatal``
        """
        # Layer 1: 格式完整性
        if not content.startswith("---\n"):
            if original_fm is not None:
                return {
                    "status": "recovered",
                    "frontmatter": original_fm,
                    "note": "frontmatter 格式缺失（缺少 --- 标记），已使用原始数据恢复",
                }
            return {
                "status": "fatal",
                "frontmatter": {},
                "note": "缺少 YAML frontmatter 标记 (---)",
            }

        lines = content.split("\n")
        end_idx = -1
        for i, line in enumerate(lines):
            if i > 0 and line.strip() == "---":
                end_idx = i
                break

        if end_idx < 0:
            if original_fm is not None:
                return {
                    "status": "recovered",
                    "frontmatter": original_fm,
                    "note": "未找到 frontmatter 结束标记 (---)，已使用原始数据恢复",
                }
            return {
                "status": "fatal",
                "frontmatter": {},
                "note": "未找到 frontmatter 结束标记 (---)",
            }

        yaml_text = "\n".join(lines[1:end_idx])

        # Layer 2: YAML 结构
        try:
            fm = yaml.safe_load(yaml_text)
        except yaml.YAMLError as e:
            if original_fm is not None:
                return {
                    "status": "repaired",
                    "frontmatter": original_fm,
                    "note": f"YAML 解析失败: {e}，已使用原始数据修复",
                }
            return {
                "status": "fatal",
                "frontmatter": {},
                "note": f"YAML 解析失败: {e}",
            }

        if not isinstance(fm, dict):
            if original_fm is not None:
                return {
                    "status": "repaired",
                    "frontmatter": original_fm,
                    "note": "frontmatter 不是有效的 YAML 字典，已使用原始数据修复",
                }
            return {
                "status": "fatal",
                "frontmatter": {},
                "note": "frontmatter 不是有效的 YAML 字典",
            }

        # Layer 3: 字段完整性
        missing = [f for f in YamlFrontmatter.REQUIRED_FIELDS if f not in fm]
        if missing:
            if original_fm is not None:
                # 修补缺失字段
                for key in missing:
                    if key in original_fm:
                        fm[key] = original_fm[key]
                return {
                    "status": "patched",
                    "frontmatter": fm,
                    "note": f"缺失必填字段: {missing}，已从原始数据修补",
                }
            return {
                "status": "fatal",
                "frontmatter": fm,
                "note": f"缺失必填字段: {missing}",
            }

        return {"status": "ok", "frontmatter": fm, "note": ""}

    @staticmethod
    def repair(content: str, original_fm: dict) -> str:
        """
        尝试修复损坏的 frontmatter。

        按优先级尝试：
        1. ``SafeLoader`` — 标准 YAML 解析
        2. ``BaseLoader`` — 宽松 YAML 解析
        3. 正则逐字段提取 — 容错提取
        4. 全部失败 → 用 ``original_fm`` 重新注入

        使用 YAML dict 操作而非字符串拼接，防止注入攻击。

        Args:
            content: 完整的文件内容
            original_fm: 原始 frontmatter 数据（作为修复回退）

        Returns:
            修复后的完整文件内容
        """
        if not content.startswith("---\n"):
            # 完全没有 frontmatter → 用原始数据重新注入
            return YamlFrontmatter.inject(content, original_fm)

        lines = content.split("\n")
        end_idx = -1
        for i, line in enumerate(lines):
            if i > 0 and line.strip() == "---":
                end_idx = i
                break

        if end_idx < 0:
            # 有开始但无结束 → 重新注入
            return YamlFrontmatter.inject(content, original_fm)

        yaml_text = "\n".join(lines[1:end_idx])
        body = "\n".join(lines[end_idx + 1:]).strip()

        fm: dict | None = None

        # 优先级 1: SafeLoader
        try:
            parsed = yaml.safe_load(yaml_text)
            if isinstance(parsed, dict):
                fm = parsed
        except yaml.YAMLError:
            pass

        # 优先级 2: BaseLoader（更宽松，允许原生标签等）
        if fm is None:
            try:
                parsed = yaml.load(yaml_text, Loader=yaml.BaseLoader)
                if isinstance(parsed, dict):
                    fm = parsed
            except yaml.YAMLError:
                pass

        # 优先级 3: 正则逐字段提取（容错模式）
        if fm is None:
            fm = {}
            all_keys = list(YamlFrontmatter.REQUIRED_FIELDS) + YamlFrontmatter.OPTIONAL_FIELDS
            for key in all_keys:
                m = re.search(rf'^{re.escape(key)}:\s*(.+)$', yaml_text, re.MULTILINE)
                if m:
                    val = m.group(1).strip()
                    # 去掉外层引号
                    if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                        val = val[1:-1]
                    fm[key] = val

        # 确保 all_keys 存在，fallback 到 original_fm
        if fm is None:
            fm = {}

        all_keys = list(YamlFrontmatter.REQUIRED_FIELDS) + YamlFrontmatter.OPTIONAL_FIELDS
        for key in all_keys:
            if key not in fm and key in original_fm:
                fm[key] = original_fm[key]

        yaml_str = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False).strip()
        return f"---\n{yaml_str}\n---\n\n{body}"

    @staticmethod
    def has_frontmatter(content: str) -> bool:
        """快速检查文本是否包含有效的 YAML frontmatter

        Args:
            content: 完整的文件内容字符串

        Returns:
            如果内容以 ``---\\n`` 开头且能在前 30 行内找到闭合 ``---``，返回 True
        """
        if not content.startswith("---\n"):
            return False
        lines = content.split("\n")
        for i in range(1, min(len(lines), 31)):
            if lines[i].strip() == "---":
                return True
        return False

    @staticmethod
    def update(file_path: Path, updates: dict) -> None:
        """
        读取文件 → 解析 frontmatter → 合并 updates → 重新写入。

        自动更新 ``updated`` 字段为当前时间，以及 ``checksum`` 为正文的新 SHA-256。

        Args:
            file_path: 文件路径
            updates: 要更新/合并的字段字典
        """
        content = file_path.read_text("utf-8")
        fm, body = YamlFrontmatter.extract_io(content)

        # 合并 updates
        fm.update(updates)

        # 自动更新时间戳和校验和
        fm["updated"] = YamlFrontmatter._now_str()
        fm["checksum"] = YamlFrontmatter._checksum(body)

        # 重新序列化
        yaml_str = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False).strip()
        new_content = f"---\n{yaml_str}\n---\n\n{body}"

        file_path.write_text(new_content, encoding="utf-8")

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_id() -> str:
        """生成 UUID v4"""
        return str(uuid.uuid4())

    @staticmethod
    def _checksum(text: str) -> str:
        """计算内容的 SHA-256 十六进制摘要"""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _now_str() -> str:
        """返回当前时间的格式化字符串 ``YYYY-MM-DD HH:MM:SS``"""
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _sanitize_wikilink(target: str) -> str:
        """
        安全过滤 ``[[...]]`` 链接目标。

        只允许字母、数字、中文、下划线、连字符，过滤掉所有其他字符。
        如果检测到路径穿越模式（``..`` 或 ``/``），或过滤后为空，抛出 ``ValueError``。

        Args:
            target: 原始链接目标字符串

        Returns:
            安全过滤后的链接目标

        Raises:
            ValueError: 如果检测到路径穿越或过滤后为空
        """
        # 防御性检查：显式拦截路径穿越模式
        if ".." in target or "/" in target or "\\" in target:
            raise ValueError(f"路径穿越攻击拦截: {target}")

        sanitized = re.sub(r'[^a-zA-Z0-9_\u4e00-\u9fff\- \[\]]', "", target)
        if not sanitized:
            raise ValueError(f"不合法的链接目标: {target}")
        return sanitized

    @staticmethod
    def sanitize_filename(title: str) -> str:
        """将标题转为安全的文件名。

        - 替换 Windows 非法字符 \\ / : * ? " < > | → _
        - 截断至 200 字符
        - 去除首尾空白和点号
        - 空结果回退为 "untitled"

        Args:
            title: 原始标题字符串

        Returns:
            安全的文件名（不含 .md 后缀）
        """
        sanitized = re.sub(r'[\\/:*?"<>|]', "_", title)
        sanitized = sanitized.strip(". ")
        sanitized = sanitized[:200]
        if not sanitized:
            sanitized = "untitled"
        return sanitized

    @staticmethod
    def _extract_body(content: str) -> str:
        """
        从可能包含 frontmatter 的内容中提取纯正文。

        如果 content 以 ``---`` 开头，则解析并移除现有 frontmatter，
        否则直接返回原内容（去除首尾空白）。

        Args:
            content: 完整的文件内容

        Returns:
            纯正文文本
        """
        if content.startswith("---"):
            lines = content.split("\n")
            end_idx = -1
            for i, line in enumerate(lines):
                if i > 0 and line.strip() == "---":
                    end_idx = i
                    break
            if end_idx >= 0:
                return "\n".join(lines[end_idx + 1:]).strip()
        return content.strip()
