"""DocumentTool — 文件操作打包工具 (read/write/append/edit/delete/list)"""

import datetime
from pathlib import Path

from core.tools.base import BaseTool
from core.tools.file_lock import acquire_file_lock


def _read_file_paginated(path: str, start_line: int = 1, max_chars: int = 5000) -> dict:
    """分页读取文件"""
    max_chars = min(max_chars, 30000)
    p = Path(path)
    if not p.exists():
        return {"status": "error", "error": "文件不存在"}

    lines = p.read_text("utf-8").splitlines(keepends=True)
    total_lines = len(lines)
    total_chars = sum(len(l) for l in lines)
    selected = lines[start_line - 1 :]
    content = "".join(selected)
    truncated = len(content) > max_chars
    chars_returned = min(len(content), max_chars)
    truncated_chars = len(content) - chars_returned
    if truncated:
        content = content[:max_chars]

    returned_lines = content.count("\n")
    if content and not content.endswith("\n"):
        returned_lines += 1

    result: dict = {
        "file": str(p),
        "total_lines": total_lines,
        "total_chars": total_chars,
        "start_line": start_line,
        "returned_lines": returned_lines,
        "chars_returned": chars_returned,
        "truncated": truncated,
        "content": content,
        "hint": "",
    }
    if truncated:
        result["truncated_chars"] = truncated_chars
    end_line = start_line + returned_lines - 1
    result["end_line"] = end_line
    if truncated:
        next_line = end_line + 1
        remaining = total_lines - end_line
        result["next_start_line"] = next_line
        result["remaining_lines"] = remaining
        result["remaining_chars"] = total_chars - (start_line - 1) - chars_returned
        hint_parts = []
        if remaining > 0:
            hint_parts.append(f"还有 {remaining} 行未读取")
        if truncated_chars > 0:
            hint_parts.append(f"最后一行内容被截断了 {truncated_chars} 个字符（单行超过 {max_chars} 字符上限）")
        hint_parts.append(
            f"继续读取请调用 document(operation='read', path='{path}', start_line={next_line})"
        )
        result["hint"] = "。".join(hint_parts)
    return result


def _search_in_file(path: str, query: str, context_lines: int = 2) -> dict:
    """在文件中搜索文本，返回匹配行号及上下文"""
    p = Path(path)
    if not p.exists():
        return {"status": "error", "error": "文件不存在"}
    try:
        lines = p.read_text("utf-8").splitlines()
    except Exception as e:
        return {"status": "error", "error": f"读取文件失败: {e}"}

    total_lines = len(lines)
    query_lower = query.lower()
    matches: list[dict] = []
    matched_ranges: list[tuple[int, int]] = []  # (start, end) 1-based

    for i, line in enumerate(lines, 1):
        if query_lower in line.lower():
            start = max(1, i - context_lines)
            end = min(total_lines, i + context_lines)
            # 合并重叠或相邻的上下文范围
            if matched_ranges and start <= matched_ranges[-1][1] + 1:
                matched_ranges[-1] = (matched_ranges[-1][0], max(matched_ranges[-1][1], end))
            else:
                matched_ranges.append((start, end))
            matches.append({
                "line": i,
                "content": line[:200],  # 截断超长行
            })

    result: dict = {
        "file": str(p),
        "total_lines": total_lines,
        "query": query,
        "match_count": len(matches),
        "matches": matches[:50],  # 最多返回 50 条
        "truncated_matches": len(matches) > 50,
    }
    if matched_ranges:
        range_strs = [f"第{s}~{e}行" if s != e else f"第{s}行" for s, e in matched_ranges]
        result["search_ranges"] = range_strs
        result["hint"] = (
            f"找到 {len(matches)} 处匹配，分布范围: {'、'.join(range_strs)}。"
            f"可使用 document(operation='read', path='{path}', start_line=<起始行>) 精确读取。"
        )
    else:
        result["hint"] = f"未找到包含「{query}」的文本"
    return result


def _read_image(path: str, max_size: int = 1024, quality: int = 85) -> dict:
    """读取图片文件，缩放并转 JPEG base64，供多模态 LLM 分析。

    Args:
        path: 图片文件路径
        max_size: 最大边长（像素），默认 1024
        quality: JPEG 质量（1-100），默认 85

    Returns:
        dict: {file, width, height, size_kb, data_url} 或 {status: "error", error: ...}
    """
    import base64
    import io
    import os
    from PIL import Image

    p = Path(path)
    if not p.exists():
        return {"status": "error", "error": f"图片文件不存在: {path}"}
    if not p.is_file():
        return {"status": "error", "error": f"路径不是文件: {path}"}

    # 检查文件扩展名
    ext = p.suffix.lower()
    image_exts = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tiff", ".tif", ".ico"}
    if ext not in image_exts:
        return {"status": "error", "error": f"不支持的文件格式: {ext}，支持的格式: {', '.join(sorted(image_exts))}"}

    try:
        img_data = p.read_bytes()
        with Image.open(io.BytesIO(img_data)) as pil_img:
            original_width, original_height = pil_img.size

            # 缩放至最大 max_size
            if pil_img.width > max_size or pil_img.height > max_size:
                ratio = min(max_size / pil_img.width, max_size / pil_img.height)
                pil_img = pil_img.resize(
                    (int(pil_img.width * ratio), int(pil_img.height * ratio)),
                )

            # 转 JPEG base64
            buf = io.BytesIO()
            # RGBA/P 模式需要先转 RGB
            if pil_img.mode in ("RGBA", "P"):
                pil_img = pil_img.convert("RGB")
            pil_img.save(buf, format="JPEG", quality=quality)
            resized_b64 = base64.b64encode(buf.getvalue()).decode()

            file_size = os.path.getsize(p)
            return {
                "file": str(p),
                "width": pil_img.width,
                "height": pil_img.height,
                "original_width": original_width,
                "original_height": original_height,
                "size_bytes": file_size,
                "size_kb": round(file_size / 1024, 1),
                "data_url": f"data:image/jpeg;base64,{resized_b64}",
            }
    except Exception as e:
        return {"status": "error", "error": f"图片读取失败: {e}"}


class DocumentTool(BaseTool):
    """文件操作工具"""

    name = "document"
    description = "文件操作工具。支持文本文件读写（read/write/append/edit/delete）、目录列表（list）、文本搜索（search）；还支持图片读取（read_image），可将图片转为多模态数据后由 AI 分析其内容（支持 jpg/png/gif/bmp/webp 等常见格式）。不传 path 时，list 默认列出工作区目录，write 自动生成文件到工作区。"
    # output_type 在不同 operation 下动态变化，统一走 json 由 _extract_tool_content 按字段判断
    output_type = "json"

    def __init__(self) -> None:
        super().__init__()
        self._char_id: str | None = None  # 当前角色 ID，由 ws_server 在连接建立时设置

    def set_char_id(self, char_id: str) -> None:
        """由 ws_server 在连接建立时设置当前角色 ID，实现工作区角色隔离"""
        self._char_id = char_id

    @property
    def _workspace_dir(self) -> Path:
        """角色隔离的工作区目录，如 .AIGEME/.data/local/ario/workspace/"""
        project_root = Path(__file__).parent.parent.parent
        if self._char_id:
            return project_root / ".AIGEME" / ".data" / "local" / self._char_id / "workspace"
        return project_root / ".AIGEME" / ".workspace"

    parameters = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["read", "write", "append", "edit", "delete", "list", "search", "read_image"],
                "description": "read=文本读取 / write=写入覆盖 / append=追加末尾 / edit=字符串替换 / delete=删除 / list=列出目录 / search=搜索文件内文本 / read_image=读取图片（转为多模态数据供 LLM 分析）",
            },
            "path": {"type": "string", "description": "文件或目录路径。write/list 时可省略：write 自动生成到角色工作区，list 默认列出角色工作区目录。"},
            "content": {
                "type": "string",
                "description": "写入/追加的内容 (write/append 时必填)",
            },
            "old_string": {
                "type": "string",
                "description": "被替换的原文 (edit 时必填)",
            },
            "new_string": {
                "type": "string",
                "description": "替换后的新内容 (edit 时必填)",
            },
            "query": {
                "type": "string",
                "description": "搜索关键词 (search 时必填, 忽略大小写)",
            },
            "start_line": {
                "type": "integer",
                "description": "从第几行开始读取 (read 时可选, 默认 1)",
            },
            "max_chars": {
                "type": "integer",
                "description": "最多读取字符数 (read 时可选, 默认 5000, 最大 30000)",
            },
        },
        "required": ["operation"],
    }

    async def execute(  # type: ignore[override]
        self,
        operation: str,
        path: str | None = None,
        content: str | None = None,
        old_string: str | None = None,
        new_string: str | None = None,
        query: str | None = None,
        start_line: int = 1,
        max_chars: int = 5000,
        **kwargs,
    ) -> dict:
        # 未指定路径时自动生成到角色隔离工作区
        if path is None:
            if operation == "write":
                now = datetime.datetime.now()
                ts = now.strftime("%Y%m%d_%H%M%S")
                path = str(self._workspace_dir / f"workspace_{ts}.txt")
            elif operation == "list":
                path = str(self._workspace_dir)
            else:
                return {"status": "error", "error": f"{operation} 操作需要 path 参数"}

        # 权限检查由 registry 外层的 permission_chain 统一处理
        # 此处仅弹出 _confirmed 标记避免残留进入下层操作
        kwargs.pop("_confirmed", False)

        # 相对路径自动映射
        # - 以 .AIGEME/ 开头 → 相对项目根目录
        # - 其他相对路径（纯文件名或子目录）→ 映射到角色工作区
        if path and not Path(path).is_absolute():
            if path.startswith(".AIGEME") or path.startswith(".AIGEME\\"):
                project_root = Path(__file__).parent.parent.parent
                path = str(project_root / path)
            else:
                ws_dir = self._workspace_dir
                ws_dir.mkdir(parents=True, exist_ok=True)
                path = str(ws_dir / path)

        p = Path(path)

        if operation == "read":
            if not p.exists():
                return {"status": "error", "error": f"文件不存在: {path}"}
            if p.is_dir():
                return {"status": "error", "error": f"路径是目录，请使用 list 操作: {path}"}
            return {
                "status": "ok",
                "result": _read_file_paginated(path, start_line, max_chars),
                "output_type": "file_read",
            }

        if operation == "write":
            if content is None:
                return {"status": "error", "error": "write 操作需要 content 参数"}
            lock = await acquire_file_lock(p)
            async with lock:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
            return {"status": "ok", "result": f"已写入 {path}", "output_type": "text"}

        if operation == "append":
            if content is None:
                return {"status": "error", "error": "append 操作需要 content 参数"}
            lock = await acquire_file_lock(p)
            async with lock:
                p.parent.mkdir(parents=True, exist_ok=True)
                with open(p, "a", encoding="utf-8") as f:
                    f.write(content)
            return {"status": "ok", "result": f"已追加到 {path}", "output_type": "text"}

        if operation == "edit":
            if old_string is None or new_string is None:
                return {
                    "status": "error",
                    "error": "edit 操作需要 old_string 和 new_string 参数",
                }
            lock = await acquire_file_lock(p)
            async with lock:
                if not p.exists():
                    return {"status": "error", "error": f"文件不存在: {path}"}
                file_content = p.read_text("utf-8")
                count = file_content.count(old_string)
                if count == 0:
                    return {"status": "error", "error": "未找到匹配的原文"}
                if count > 1:
                    return {
                        "status": "error",
                        "error": f"找到 {count} 处匹配，请提供更精确的原文（包含更多上下文）来唯一匹配",
                    }
                p.write_text(file_content.replace(old_string, new_string), encoding="utf-8")
            return {"status": "ok", "result": f"已编辑 {path}", "output_type": "text"}

        if operation == "delete":
            if not p.exists():
                return {"status": "error", "error": f"文件不存在: {path}"}
            lock = await acquire_file_lock(p)
            async with lock:
                p.unlink()
            return {"status": "ok", "result": f"已删除 {path}", "output_type": "text"}

        if operation == "list":
            if not p.exists():
                p.mkdir(parents=True, exist_ok=True)
            elif not p.is_dir():
                return {"status": "error", "error": f"路径不是目录: {path}"}
            files = [
                {
                    "name": f.name,
                    "type": "dir" if f.is_dir() else "file",
                    "size": f.stat().st_size if f.is_file() else 0,
                }
                for f in sorted(p.iterdir())
            ]
            return {
                "status": "ok",
                "result": {"path": str(p), "files": files},
                "output_type": "file_list",
            }

        if operation == "search":
            if query is None:
                return {"status": "error", "error": "search 操作需要 query 参数"}
            if not p.exists():
                return {"status": "error", "error": f"文件不存在: {path}"}
            if p.is_dir():
                return {"status": "error", "error": "search 仅支持文件，不支持目录"}
            return {
                "status": "ok",
                "result": _search_in_file(path, query),
                "output_type": "file_search",
            }

        if operation == "read_image":
            if not p.exists():
                return {"status": "error", "error": f"图片文件不存在: {path}"}
            if p.is_dir():
                return {"status": "error", "error": f"路径是目录，不是图片: {path}"}
            result = _read_image(path)
            if "status" in result and result["status"] == "error":
                return {"status": "error", "error": result["error"]}
            return {
                "status": "ok",
                "result": result,
                "output_type": "image",
            }

        return {"status": "error", "error": f"不支持的操作: {operation}"}
