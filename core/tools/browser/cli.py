#!/usr/bin/env python3
"""CLI interface for AIGEME Browser Control

Allows calling browser helper functions directly from the command line,
or executing browser automation scripts.

Usage:
    # Single command
    python -m core.tools.browser.cli goto_url https://www.baidu.com
    python -m core.tools.browser.cli search_baidu "搜索关键词"
    python -m core.tools.browser.cli page_info
    python -m core.tools.browser.cli js "document.title"
    python -m core.tools.browser.cli capture_screenshot
    python -m core.tools.browser.cli list_tabs
    python -m core.tools.browser.cli wait 1.5
    python -m core.tools.browser.cli click_at_xy 100 200
    python -m core.tools.browser.cli fill_input "#search" "hello"
    python -m core.tools.browser.cli press_key Enter

    # Script mode
    python -m core.tools.browser.cli script my_task.py

    # Help
    python -m core.tools.browser.cli help
    python -m core.tools.browser.cli <command> --help
"""

import argparse
import inspect
import io
import json
import os
import sys
import traceback
import typing
from contextlib import redirect_stdout, redirect_stderr


# ── Argparse helpers ────────────────────────────────────────────────────────

def _str_to_bool(v):
    """字符串转布尔值，正确处理 'false'/'0'/'no' → False"""
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        if v.lower() in ("true", "1", "yes", "y"):
            return True
        if v.lower() in ("false", "0", "no", "n"):
            return False
    raise argparse.ArgumentTypeError(f"无效的布尔值: {v!r}")


def _resolve_simple_type(tp):
    """将类型注解映射为 argparse 可用的 type 转换器"""
    if tp is bool:
        return _str_to_bool
    if tp is int:
        return int
    if tp is float:
        return float
    return str  # 包括 str, list, dict 等复杂类型


def _get_arg_type(param):
    """从参数注解推断 argparse 类型转换器，支持 str|None / Optional 等 Union 类型"""
    ann = param.annotation
    if ann is inspect.Parameter.empty:
        return str
    try:
        origin = typing.get_origin(ann)
        if origin is not None:
            # Union / X | None → 提取第一个非 None 类型
            args = typing.get_args(ann)
            for a in args:
                if a is not type(None):
                    return _resolve_simple_type(a)
            return str
    except Exception:
        pass
    return _resolve_simple_type(ann)


def _format_default(value) -> str:
    """格式化默认值用于帮助文本"""
    if value is None:
        return "None"
    if isinstance(value, str):
        return f"'{value}'"
    return str(value)


# ── Helper importing ────────────────────────────────────────────────────────

def _import_helpers() -> dict:
    """导入 helpers.py 中所有公开的 callable 函数"""
    from core.tools.browser import helpers as h
    return {k: v for k, v in vars(h).items() if callable(v) and not k.startswith("_")}


# ── Parser construction ─────────────────────────────────────────────────────

def _build_parser(helpers: dict) -> argparse.ArgumentParser:
    """动态构建 argparse 解析器

    每个 helper 函数自动生成一个子命令，参数按函数签名映射：
    - 无默认值参数 → 位置参数（必需）
    - 有默认值参数 → 位置参数（可选，nargs='?'），省略时使用默认值
    """
    parser = argparse.ArgumentParser(
        prog="python -m core.tools.browser.cli",
        description="AIGEME 浏览器控制 CLI — 通过命令行或脚本操作浏览器",
        add_help=False,  # 自定义 help 命令
    )

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    # ── 为每个 helper 函数创建子命令 ──
    for name in sorted(helpers.keys()):
        func = helpers[name]
        sig = inspect.signature(func)
        doc = (func.__doc__ or "").strip().split("\n")[0]

        sub = subparsers.add_parser(name, help=doc)
        sub.set_defaults(_func=func)

        for pname, param in sig.parameters.items():
            # 跳过 *args 和 **kwargs
            if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
                continue
            # 跳过 self/cls（虽然 helpers 没有类方法）
            if pname in ("self", "cls"):
                continue

            is_optional = param.default is not inspect.Parameter.empty
            arg_type = _get_arg_type(param)

            sub.add_argument(
                pname,
                nargs="?" if is_optional else None,
                default=param.default if is_optional else None,
                type=arg_type,
                metavar=pname.upper(),
                help=f"(default: {_format_default(param.default)})" if is_optional else None,
            )

    # ── script 子命令：执行脚本文件 ──
    script_parser = subparsers.add_parser("script", help="执行浏览器操作脚本文件")
    script_parser.add_argument(
        "file", type=str, metavar="FILE",
        help="Python 脚本文件路径",
    )

    # ── help 子命令 ──
    subparsers.add_parser("help", help="显示此帮助信息", add_help=False)

    return parser


# ── Output formatting ───────────────────────────────────────────────────────

def _format_output(result):
    """格式化函数返回值输出

    - None → 不输出
    - dict/list → JSON（ensure_ascii=False 支持中文）
    - 其他 → str()

    如果 result 是 dict 且包含 data_url，额外打印 data_url 摘要信息（前 80 字符）。
    """
    if result is None:
        return
    if isinstance(result, (dict, list)):
        print(json.dumps(result, ensure_ascii=False, default=str))
        if isinstance(result, dict) and "data_url" in result:
            du = result["data_url"]
            if len(du) > 80:
                print(f"data_url: {du[:80]}...")
            else:
                print(f"data_url: {du}")
    else:
        print(result)


# ── Script execution ───────────────────────────────────────────────────────

def _run_script(file_path: str, helpers: dict):
    """以内置全局变量方式执行浏览器操作脚本

    脚本中所有 helper 函数作为内置全局变量可用，无需 import。
    与 tools.py 的 exec(code, globals_dict) 模式一致。
    """
    abs_path = os.path.abspath(file_path)
    if not os.path.isfile(abs_path):
        print(f"错误: 脚本文件不存在: {abs_path}", file=sys.stderr)
        sys.exit(1)

    # 读取文件内容
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            code = f.read()
    except OSError as e:
        print(f"错误: 无法读取脚本文件: {e}", file=sys.stderr)
        sys.exit(1)

    # 自动启动 daemon
    _ensure_daemon()

    # 构建执行上下文：helpers 函数作为全局变量
    globals_dict = helpers.copy()
    globals_dict["__builtins__"] = __builtins__
    # 设置 __name__ = "__main__"，使脚本中的 if __name__ == "__main__": 守卫通过
    # 否则 exec() 默认将 __name__ 设为 "builtins"，导致入口函数永远不会被执行
    globals_dict["__name__"] = "__main__"

    # 注入 download_dir（与 tools.py 一致）
    from core.tools.browser.helpers import DOWNLOAD_DIR
    globals_dict["download_dir"] = str(DOWNLOAD_DIR)

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    try:
        compiled = compile(code, abs_path, "exec")
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            exec(compiled, globals_dict)

        out = stdout_buf.getvalue()
        err = stderr_buf.getvalue()
        if out:
            print(out, end="")
        if err:
            print(err, end="", file=sys.stderr)

    except Exception as e:
        import traceback as _tb
        _tb.print_exc(file=sys.stderr)
        print(f"脚本执行错误: {e}", file=sys.stderr)
        sys.exit(1)


# ── Daemon lifecycle ────────────────────────────────────────────────────────

def _ensure_daemon():
    """确保浏览器 daemon 正在运行，并配置下载/截图目录"""
    try:
        from core.tools.browser.manager import get_manager
        mgr = get_manager()
        if not mgr.is_running():
            print("正在启动浏览器 daemon...", flush=True)
        mgr.ensure_running()
        # 配置下载行为
        from core.tools.browser import helpers as h
        h.setup_download_handler()
        print("浏览器 daemon 就绪", flush=True)
    except Exception as e:
        import traceback as _tb
        _tb.print_exc(file=sys.stderr)
        print(f"错误: 启动浏览器 daemon 失败: {e}", file=sys.stderr)
        sys.exit(1)


# ── Main entry point ────────────────────────────────────────────────────────

def main():
    """CLI 入口：解析参数、执行命令"""
    helpers = _import_helpers()
    parser = _build_parser(helpers)

    # 无参数 → 显示帮助
    if len(sys.argv) <= 1:
        _print_help(parser, helpers)
        sys.exit(0)

    # 提取命令名（跳过 sys.argv[0]）
    command = sys.argv[1]

    # ── help 命令 ──
    if command == "help":
        _print_help(parser, helpers)
        sys.exit(0)

    # ── script 命令 ──
    if command == "script":
        if len(sys.argv) < 3:
            print("错误: script 命令需要指定脚本文件路径", file=sys.stderr)
            print("用法: python -m core.tools.browser.cli script <文件路径>", file=sys.stderr)
            sys.exit(1)
        _run_script(sys.argv[2], helpers)
        return

    # ── 未知命令 ──
    if command not in helpers:
        print(f"错误: 未知命令 '{command}'", file=sys.stderr)
        print(f"可用命令: {', '.join(sorted(helpers.keys()))}", file=sys.stderr)
        print("使用 'python -m core.tools.browser.cli help' 查看详细帮助", file=sys.stderr)
        sys.exit(1)

    # ── 自动启动 daemon（首次调用时） ──
    # --help / -h 不启动 daemon，让 argparse 直接打印帮助
    if "--help" not in sys.argv and "-h" not in sys.argv:
        _ensure_daemon()

    # ── 解析并执行命令 ──
    args = parser.parse_args()

    # 从 set_defaults 获取函数引用
    func = getattr(args, "_func", None)
    if func is None:
        print(f"错误: 找不到命令 '{command}' 的实现", file=sys.stderr)
        sys.exit(1)

    sig = inspect.signature(func)

    # 从解析后的 args 构建函数参数
    positional_args = []
    kwargs = {}
    for pname, param in sig.parameters.items():
        if pname in ("self", "cls"):
            continue
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue

        val = getattr(args, pname, None)
        is_optional = param.default is not inspect.Parameter.empty

        if is_optional:
            kwargs[pname] = val
        else:
            if val is None:
                print(f"错误: 缺少必需参数 '{pname}'", file=sys.stderr)
                sys.exit(1)
            positional_args.append(val)

    # 执行函数
    try:
        result = func(*positional_args, **kwargs)
        _format_output(result)
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        if os.environ.get("AIGEME_CLI_DEBUG"):
            traceback.print_exc(file=sys.stderr)
        sys.exit(1)


def _print_help(parser: argparse.ArgumentParser, helpers: dict):
    """打印完整帮助信息，包括所有可用命令及其说明"""
    print("AIGEME 浏览器控制 CLI")
    print()
    print("用法:")
    print("  python -m core.tools.browser.cli <命令> [参数...]")
    print("  python -m core.tools.browser.cli script <文件>")
    print()
    print("可用命令:")
    for name in sorted(helpers.keys()):
        func = helpers[name]
        sig = inspect.signature(func)
        params = []
        for pname, param in sig.parameters.items():
            if pname in ("self", "cls"):
                continue
            if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
                continue
            is_optional = param.default is not inspect.Parameter.empty
            if is_optional:
                params.append(f"[{pname}]")
            else:
                params.append(pname.upper())
        param_str = " ".join(params)
        doc = (func.__doc__ or "").strip().split("\n")[0]
        print(f"  {name:25s} {param_str:30s} {doc}")
    print()
    print("特殊命令:")
    print("  script <FILE>          执行浏览器操作脚本文件")
    print("  help                   显示此帮助信息")
    print("  <cmd> --help           查看单个命令的详细参数说明")
    print()
    print("脚本模式:")
    print("  使用 script 命令执行 Python 脚本文件，脚本中所有 helper 函数")
    print("  作为内置全局变量可用，无需 import。")
    print()
    print("  示例 (my_task.py):")
    print("    goto_url('https://www.baidu.com')")
    print("    wait_for_load()")
    print("    search_baidu('AI Agent')")
    print("    page = page_info()")
    print("    print(f'Title: {page[\"title\"]}')")


if __name__ == "__main__":
    main()
