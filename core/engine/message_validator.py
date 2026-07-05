"""消息顺序校验器 — 在发送到 LLM API 前检查消息合法性

OpenAI/DeepSeek API 的 Jinja 模板要求：
- ``tool`` 角色消息必须跟在 ``assistant``（含 tool_calls）或另一个 ``tool`` 后面
- 违反此规则会导致服务端返回 500 + Jinja Exception

本模块提供 validate + auto-fix：
1. **孤立 tool 消息清理**：删除开头或中间出现的 "无主 tool"
2. **孤立 tool_calls 清理**：删除 assistant 消息中无对应 tool 回应的 tool_calls
3. **空 content 保护**：确保 tool 消息有非空 content
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def validate_and_fix_messages(messages: list[dict]) -> list[dict]:
    """校验并修复消息顺序。

    规则（OpenAI/DeepSeek 协议）：
    - tool 必须跟在 assistant（含 tool_calls）或另一个 tool 后面
    - assistant 含 tool_calls 时，后面必须有对应数量的 tool 消息

    Args:
        messages: LLM 消息列表，每项含 ``role``, ``content`` 等字段

    Returns:
        修复后的消息列表（稳健做法：出错时返回原始列表并记录警告）
    """
    if not messages:
        return messages

    # 调试日志：始终打印消息角色序列（方便排查问题）
    roles_preview = " → ".join(
        _short_role(m) for m in messages[:50]
    )
    extra = f"... (+{len(messages) - 50})" if len(messages) > 50 else ""
    logger.info(
        "[MSG_VALIDATOR] 校验前消息序列 (%d 条): %s%s",
        len(messages), roles_preview, extra,
    )

    original_count = len(messages)
    fixed = list(messages)

    # ── Step 1: 清理孤立 tool 消息（开头或 assistant(say) 后面的 tool）──
    # 从前往后扫描，移除 tool 前的角色不是 assistant（含 tool_calls）或 tool 的消息
    cleaned: list[dict] = []
    orphan_tool_count = 0
    for i, msg in enumerate(fixed):
        role = msg.get("role", "")
        if role != "tool":
            cleaned.append(msg)
            continue

        # tool 消息：检查前一条消息
        if not cleaned:
            # tool 出现在开头 → 孤立
            orphan_tool_count += 1
            logger.warning(
                "[MSG_VALIDATOR] 孤立 tool 消息: 消息[%d] 出现在消息列表开头, "
                "content='%s'", i, _preview(msg.get("content", ""))
            )
            continue

        prev = cleaned[-1]
        prev_role = prev.get("role", "")
        prev_has_tc = bool(prev.get("tool_calls"))
        if prev_role == "assistant" and prev_has_tc:
            cleaned.append(msg)
        elif prev_role == "tool":
            cleaned.append(msg)
        else:
            # tool 前是 user/system/assistant(say) → 孤立
            orphan_tool_count += 1
            logger.warning(
                "[MSG_VALIDATOR] 孤立 tool 消息: 消息[%d] role=%s 前是 role=%s "
                "(need assistant with tool_calls or tool), content='%s'",
                i, role, prev_role, _preview(msg.get("content", ""))
            )

    if orphan_tool_count:
        logger.warning(
            "[MSG_VALIDATOR] 已清理 %d 条孤立 tool 消息 (%d→%d)",
            orphan_tool_count, len(fixed), len(cleaned),
        )

    # ── Step 2: 清理孤立 tool_calls（assistant 有 tool_calls 但无后续 tool）──
    result = _clean_orphan_tool_calls_from_list(cleaned)

    # Step 2 可能把 assistant(TC) 变成 assistant(say)，
    # 导致其后的 tool 消息变成孤立 → 再跑一轮 Step 1
    if result != cleaned:
        re_cleaned: list[dict] = []
        re_orphan = 0
        for msg in result:
            if msg.get("role") != "tool":
                re_cleaned.append(msg)
                continue
            if not re_cleaned:
                re_orphan += 1
                continue
            prev = re_cleaned[-1]
            pr = prev.get("role", "")
            ptc = bool(prev.get("tool_calls"))
            if (pr == "assistant" and ptc) or pr == "tool":
                re_cleaned.append(msg)
            else:
                re_orphan += 1
        if re_orphan:
            logger.warning(
                "[MSG_VALIDATOR] 第二轮清理: 移除 %d 条因 Step2 变孤立的 tool 消息",
                re_orphan,
            )
            result = re_cleaned

    # ── Step 3: 确保 tool 消息 content 非空（空 content 也可能触发服务端异常）──
    for i, msg in enumerate(result):
        if msg.get("role") == "tool" and not msg.get("content"):
            result[i] = dict(msg, content="(空结果)")
            logger.warning(
                "[MSG_VALIDATOR] 消息[%d] tool 消息 content 为空，已填充占位符", i
            )

    removed = original_count - len(result)
    if removed:
        logger.info(
            "[MSG_VALIDATOR] 消息校验: 共清理 %d 条问题消息 (%d→%d)",
            removed, original_count, len(result),
        )
    else:
        logger.info("[MSG_VALIDATOR] 消息校验: 无需修复，序列合法")

    return result


def _clean_orphan_tool_calls_from_list(messages: list[dict]) -> list[dict]:
    """清理消息列表中孤立的 tool_calls。

    从后往前扫描，如果 assistant 的 tool_calls 中有无对应 tool 的条目，
    只移除那些孤立的条目，保留有对应 tool 的。
    """
    if not messages:
        return messages

    # 收集所有 tool 消息的 tool_call_id
    tool_ids: set[str] = set()
    for msg in messages:
        if msg.get("role") == "tool" and msg.get("tool_call_id"):
            tool_ids.add(msg["tool_call_id"])

    # 从后往前找孤立的 assistant(TC)
    result = list(messages)
    for i in range(len(result) - 1, -1, -1):
        msg = result[i]
        if msg.get("role") != "assistant":
            continue
        tcs = msg.get("tool_calls", [])
        if not tcs:
            continue

        # 分离孤立的和有效的 tool_calls
        orphaned: list[dict] = []
        valid: list[dict] = []
        for tc in tcs:
            tc_id = ""
            if isinstance(tc, dict):
                tc_id = tc.get("id", "")
            elif hasattr(tc, "id"):
                tc_id = tc.id  # type: ignore[union-attr]
            if tc_id and tc_id not in tool_ids:
                orphaned.append(tc)
            else:
                valid.append(tc)

        if not orphaned:
            continue  # 没有孤立的

        if not valid:
            # 全部孤立 → 完全移除 tool_calls
            result[i] = dict(msg)
            result[i].pop("tool_calls", None)
            logger.warning(
                "[MSG_VALIDATOR] 已清理孤立 tool_calls: 消息[%d] (%d 个工具全部孤立), content='%s'",
                i, len(tcs), _preview(msg.get("content", "")),
            )
        else:
            # 部分孤立 → 只移除孤立的，保留有效的
            msg_copy = dict(msg)
            msg_copy["tool_calls"] = valid
            result[i] = msg_copy
            logger.warning(
                "[MSG_VALIDATOR] 已清理部分孤立 tool_calls: 消息[%d] 移除 %d 个孤立，保留 %d 个有效",
                i, len(orphaned), len(valid),
            )

    return result


def _preview(content: Any, max_len: int = 60) -> str:
    """安全截断内容预览"""
    if isinstance(content, list):
        return f"[array({len(content)} items)]"
    text = str(content) if content else ""
    return text[:max_len] + ("..." if len(text) > max_len else "")


def _short_role(msg: dict) -> str:
    """生成消息的简要角色标识（用于调试日志的消息序列预览）"""
    role = msg.get("role", "?")
    if role == "assistant" and msg.get("tool_calls"):
        tc_count = len(msg["tool_calls"])
        return f"ast(TC={tc_count})"
    if role == "tool" and msg.get("tool_call_id"):
        return f"tl({msg['tool_call_id'][:6]})"
    return role


# ── 便捷入口 ──────────────────────────────────────────────────────


def validate_inplace(messages: list[dict]) -> list[dict]:
    """原地校验（不修改原始列表），返回修复后的新列表。

    用法：
        messages = validate_inplace(messages)
    """
    return validate_and_fix_messages(messages)
