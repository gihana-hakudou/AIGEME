"""Tool Registry — 工具注册中心（Singleton）"""

import json
import logging
import time
from typing import Any

from core.tools.base import BaseTool
from core.tools.permission import PermissionChain, PermissionFilter

logger = logging.getLogger(__name__)


class RateLimiter:
    """Per-session 速率限制（滑动窗口）"""

    def __init__(self, max_calls: int = 30, window: float = 60.0) -> None:
        self._max_calls = max_calls
        self._window = window
        self._timestamps: list[float] = []

    def check(self) -> bool:
        """检查是否超过限制。返回 True 表示允许通过。"""
        now = time.time()
        # 清理超出窗口的时间戳
        cutoff = now - self._window
        self._timestamps = [t for t in self._timestamps if t > cutoff]

        if len(self._timestamps) >= self._max_calls:
            return False  # 超过限制

        self._timestamps.append(now)
        return True


def _validate_args(schema: dict[str, Any], arguments: dict[str, Any]) -> list[str]:
    """对工具参数做 JSON Schema 校验，返回错误列表（为空表示校验通过）。

    覆盖 LLM 工具调用最常见的三类错误：
    - 缺少必需参数 (required)
    - 参数类型不匹配 (type: string/integer/number/boolean/array/object)
    - 枚举值越界 (enum)
    """
    errors: list[str] = []
    props = schema.get("properties", {})
    required = schema.get("required", [])

    # 1. 必需字段检查
    for key in required:
        if key not in arguments or arguments[key] is None:
            errors.append(f"缺少必需参数 '{key}'")
            continue

    # 2. 字段类型 + 枚举值检查
    for key, value in arguments.items():
        if key not in props:
            errors.append(f"未知参数 '{key}'")
            continue
        prop = props[key]
        prop_type = prop.get("type", "")
        enum_values = prop.get("enum", [])

        # 枚举值检查
        if enum_values and value not in enum_values:
            allowed = ", ".join(repr(v) for v in enum_values)
            errors.append(f"参数 '{key}' 的值 '{value}' 不在允许范围内 [{allowed}]")
            continue  # 值不合法，不需要再检查类型

        # 类型检查（注意：bool 是 int 的子类，需先检查 bool）
        if prop_type == "boolean" and not isinstance(value, bool):
            errors.append(f"参数 '{key}' 期望类型 boolean，收到 {type(value).__name__}")
        elif prop_type == "integer" and not isinstance(value, int):
            errors.append(f"参数 '{key}' 期望类型 integer，收到 {type(value).__name__}")
        elif prop_type == "number" and not isinstance(value, (int, float)):
            errors.append(f"参数 '{key}' 期望类型 number，收到 {type(value).__name__}")
        elif prop_type == "string" and not isinstance(value, str):
            errors.append(f"参数 '{key}' 期望类型 string，收到 {type(value).__name__}")
        elif prop_type == "array" and not isinstance(value, list):
            errors.append(f"参数 '{key}' 期望类型 array，收到 {type(value).__name__}")
        elif prop_type == "object" and not isinstance(value, dict):
            errors.append(f"参数 '{key}' 期望类型 object，收到 {type(value).__name__}")

    return errors


class ToolRegistry:
    """工具注册中心"""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}
        self.session_id: str = ""
        self._rate_limiter = RateLimiter(max_calls=30, window=60.0)
        self._permission_chain = PermissionChain()

    def add_permission_filter(self, filter: PermissionFilter) -> None:
        """注册权限过滤器"""
        self._permission_chain.add(filter)

    def register(self, tool: BaseTool) -> None:
        """注册单个工具"""
        self._tools[tool.name] = tool

    def register_many(self, *tools: BaseTool) -> None:
        """批量注册"""
        for tool in tools:
            self.register(tool)

    def get(self, name: str) -> BaseTool | None:
        """按名称查找工具"""
        return self._tools.get(name)

    async def execute(self, name: str, arguments: dict[str, Any], _confirmed: bool = False) -> dict[str, Any]:
        """执行工具，统一包装返回（含 output_type 供消费者按类型解析）

        _confirmed: 内部参数，用户已确认操作时设为 True，跳过权限检查。
        """
        # 速率限制检查
        if not self._rate_limiter.check():
            logger.info("[AUDIT] session=%s tool=%s args=%s result=blocked(rate_limit)",
                self.session_id, name, json.dumps(arguments, ensure_ascii=False)[:200])
            return {
                "status": "blocked",
                "reason": "rate limit exceeded",
            }

        # 权限检查（用户已确认时跳过）
        if not _confirmed:
            verdict = await self._permission_chain.check(name, arguments, {"session_id": self.session_id})
            if not verdict.allow:
                logger.info("[AUDIT] session=%s tool=%s args=%s result=blocked(permission:%s)",
                    self.session_id, name, json.dumps(arguments, ensure_ascii=False)[:200],
                    verdict.reason)
                return {
                    "status": "blocked",
                    "reason": verdict.reason,
                }
            if verdict.require_confirm:
                logger.info("[AUDIT] session=%s tool=%s args=%s result=needs_confirm",
                    self.session_id, name, json.dumps(arguments, ensure_ascii=False)[:200])
                return {
                    "status": "needs_confirm",
                    "operation": verdict.reason or name,
                }

        tool = self.get(name)
        if not tool:
            logger.info("[AUDIT] session=%s tool=%s args=%s result=error(tool_not_found)",
                self.session_id, name, json.dumps(arguments, ensure_ascii=False)[:200])
            return {
                "status": "error",
                "error": f"工具 '{name}' 未注册",
                "error_type": "tool_not_found",
            }

        # JSON Schema 校验：在执行前拦截格式错误
        schema_errors = _validate_args(tool.parameters, arguments)
        if schema_errors:
            logger.info("[AUDIT] session=%s tool=%s args=%s result=error(schema_error)",
                self.session_id, name, json.dumps(arguments, ensure_ascii=False)[:200])
            return {
                "status": "error",
                "error": f"参数校验失败: {'; '.join(schema_errors)}",
                "error_type": "schema_error",
                "tool_name": name,
                "arguments_received": arguments,
            }

        try:
            # 过滤内部参数（_ 前缀），只传递工具关心的参数
            safe_args = {k: v for k, v in arguments.items() if not k.startswith('_')}
            # 用户已确认时，让工具也知道已确认状态
            if _confirmed:
                safe_args['_confirmed'] = True
            result = await tool.execute(**safe_args)

            # 工具自身返回 needs_confirm（如 document 工具的外部路径检查）
            if isinstance(result, dict) and result.get("status") == "needs_confirm":
                if _confirmed:
                    # 用户已确认但工具仍返回 needs_confirm —— 这是工具内部策略阻止
                    # 注入 _force=True 让工具跳过内部安全检查直接执行
                    logger.info("[AUDIT] session=%s tool=%s args=%s result=needs_confirm→force_retry(confirmed)",
                        self.session_id, name, json.dumps(arguments, ensure_ascii=False)[:200])
                    force_args = {**safe_args, '_force': True}
                    result = await tool.execute(**force_args)
                    # 即使 force 后仍失败，则透传工具结果
                    if isinstance(result, dict) and result.get("status") in ("blocked", "error"):
                        return result

                    # 正常包装 force 后的成功结果
                    result_output_type = (
                        result.get("output_type", tool.output_type)
                        if isinstance(result, dict)
                        else tool.output_type
                    )
                    return {
                        "status": "ok",
                        "result": result,
                        "output_type": result_output_type,
                    }
                logger.info("[AUDIT] session=%s tool=%s args=%s result=needs_confirm(tool)",
                    self.session_id, name, json.dumps(arguments, ensure_ascii=False)[:200])
                return result

            # 工具自身返回 blocked 或 error → 直接透传，避免被包装为 status=ok
            if isinstance(result, dict) and result.get("status") in ("blocked", "error"):
                logger.info("[AUDIT] session=%s tool=%s args=%s result=%s",
                    self.session_id, name, json.dumps(arguments, ensure_ascii=False)[:200],
                    result.get("status"))
                return result

            # output_type 优先级：工具返回 > 工具类静态声明 > 默认 "json"
            result_output_type = (
                result.get("output_type", tool.output_type)
                if isinstance(result, dict)
                else tool.output_type
            )
            # 审计日志
            result_status = result.get("status", "?") if isinstance(result, dict) else "?"
            logger.info("[AUDIT] session=%s tool=%s args=%s result=%s",
                self.session_id, name, json.dumps(arguments, ensure_ascii=False)[:200],
                result_status)
            return {
                "status": "ok",
                "result": result,
                "output_type": result_output_type,
            }
        except TypeError as e:
            logger.info("[AUDIT] session=%s tool=%s args=%s result=error(type_error)",
                self.session_id, name, json.dumps(arguments, ensure_ascii=False)[:200])
            return {
                "status": "error",
                "error": f"参数错误: {e!s}",
                "error_type": "argument_error",
                "tool_name": name,
                "arguments_received": arguments,
            }
        except Exception as e:
            logger.info("[AUDIT] session=%s tool=%s args=%s result=error(execution_error)",
                self.session_id, name, json.dumps(arguments, ensure_ascii=False)[:200])
            return {
                "status": "error",
                "error": f"执行失败: {e!s}",
                "error_type": "execution_error",
            }

    @property
    def schemas(self) -> list[dict[str, Any]]:
        """返回所有工具的 JSON Schema + output_type（用于注入 Prompt + 消费者解析）"""
        return [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
                "output_type": t.output_type,
            }
            for t in self._tools.values()
        ]

    @property
    def names(self) -> list[str]:
        """返回所有已注册工具名列表"""
        return list(self._tools.keys())


# 全局单例
_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    """获取全局 ToolRegistry 单例"""
    global _registry
    if _registry is None:
        _registry = ToolRegistry()
    return _registry


def init_registry(*tools: BaseTool) -> ToolRegistry:
    """初始化并注册工具"""
    global _registry
    _registry = ToolRegistry()
    _registry.register_many(*tools)
    return _registry
