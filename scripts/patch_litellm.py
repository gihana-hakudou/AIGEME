"""LiteLLM monkey-patch: Anthropic 流式 tool_calls 修复

Bug: litellm Anthropic handler 使用单变量 current_content_block_type
跟踪内容块类型，当流式返回交错块（如 thinking + tool_use 在同一个 index）
时会被覆盖，导致 tool_calls 丢失。

修复: 改为按 index 追踪 (current_content_block_type_by_index: dict[int, str])
"""

import re
import sys
from pathlib import Path


def apply_patch() -> bool:
    """对 litellm 的 Anthropic handler 打补丁，返回 True 表示有改动"""
    # 定位 litellm 安装目录
    try:
        import litellm
    except ImportError:
        print("[PATCH] litellm 未安装，跳过")
        return False

    litellm_dir = Path(litellm.__file__).resolve().parent
    handler_path = litellm_dir / "llms" / "anthropic" / "chat" / "handler.py"

    if not handler_path.exists():
        print(f"[PATCH] handler.py 未找到: {handler_path}")
        return False

    content = handler_path.read_text("utf-8")

    # 检查是否已打过补丁
    if "current_content_block_type_by_index" in content:
        print("[PATCH] 补丁已存在，跳过")
        return False

    # ── 补丁 1: __init__ 中的类型定义 ──
    content = content.replace(
        "        self.current_content_block_type: Optional[str] = None",
        "        self.current_content_block_type_by_index: dict[int, str] = {}",
    )

    # ── 补丁 2: partial_json 中的类型检查 ──
    content = content.replace(
        "if self.current_content_block_type in (\"tool_use\", \"server_tool_use\"):",
        "if self.current_content_block_type_by_index.get(chunk.get(\"index\", 0)) in (\"tool_use\", \"server_tool_use\"):",
    )

    # ── 补丁 3: content_block_start 中的赋值 ──
    content = content.replace(
        "                self.content_blocks = []  # reset content blocks when new block starts\n"
        "                # Track current content block type for filtering deltas\n"
        "                self.current_content_block_type = content_block_start[\"content_block\"][",
        "                self.content_blocks = []  # reset content blocks when new block starts\n"
        "                # Track current content block type per index for filtering deltas\n"
        "                _block_idx = chunk.get(\"index\", 0)\n"
        "                self.current_content_block_type_by_index[_block_idx] = content_block_start[\"content_block\"][",
    )

    # ── 补丁 4: content_block_stop 中的 server_tool_use 检查 ──
    content = content.replace(
        "if (\n"
        "                        self.current_content_block_type == \"server_tool_use\"\n"
        "                        and self._current_server_tool_id\n"
        "                    ):",
        "if (\n"
        "                        self.current_content_block_type_by_index.get(chunk.get(\"index\", 0))\n"
        "                        == \"server_tool_use\"\n"
        "                        and self._current_server_tool_id\n"
        "                    ):",
    )

    # ── 补丁 5: content_block_stop 中的重置 ──
    content = content.replace(
        "# Reset current content block type\n"
        "                self.current_content_block_type = None",
        "# Reset current content block type for this index\n"
        "                _stop_idx = chunk.get(\"index\", 0)\n"
        "                self.current_content_block_type_by_index.pop(_stop_idx, None)",
    )

    handler_path.write_text(content, encoding="utf-8")
    print(f"[PATCH] 补丁已应用到: {handler_path}")
    return True


if __name__ == "__main__":
    changed = apply_patch()
    sys.exit(0 if True else 1)
