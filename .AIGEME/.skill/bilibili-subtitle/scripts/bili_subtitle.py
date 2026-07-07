#!/usr/bin/env python3
"""
B站字幕提取工具 — bili_subtitle.py

用法:
  python bili_subtitle.py BV124TD6GECb
  python bili_subtitle.py BV124TD6GECb --output subtitle.txt
  python bili_subtitle.py BV124TD6GECb --plain    # 只输出纯文本（无时间戳）

依赖: 项目 venv, browser 工具已配置
注意: 浏览器需要已登录 B站 账号
"""

import sys
import json
import os
import re
import subprocess
import argparse


BROWSER_CLI = "python -m core.tools.browser.cli"


def run_browser(*args, timeout=15):
    """调用浏览器CLI并返回stdout"""
    cmd = f"{BROWSER_CLI} {' '.join(args)}"
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        return None
    except Exception as e:
        return None


def get_video_info(bvid):
    """获取视频AID和CID"""
    print(f"[1/4] 获取视频信息: {bvid}")
    
    url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    run_browser("goto_url", url, timeout=10)
    run_browser("wait_for_load", "10", timeout=10)
    run_browser("wait", "1", timeout=5)
    body = run_browser('js', 'document.body.innerText', timeout=5)
    
    if not body:
        print("  ❌ 获取视频信息失败（超时）")
        return None, None, None
    
    try:
        data = json.loads(body)
        if data.get("code") != 0:
            print(f"  ❌ API错误: {data.get('message', '未知')}")
            return None, None, None
        
        aid = data["data"]["aid"]
        cid = data["data"]["cid"]
        title = data["data"]["title"]
        print(f"  ✅ {title}")
        print(f"     AID: {aid}, CID: {cid}")
        return aid, cid, title
    except (json.JSONDecodeError, KeyError) as e:
        print(f"  ❌ 解析失败: {e}")
        print(f"  原始响应前200字: {body[:200]}")
        return None, None, None


def get_subtitle_list(aid, cid):
    """获取字幕列表"""
    print(f"[2/4] 获取字幕列表...")
    
    url = f"https://api.bilibili.com/x/player/wbi/v2?aid={aid}&cid={cid}"
    run_browser("goto_url", url, timeout=10)
    run_browser("wait_for_load", "10", timeout=10)
    run_browser("wait", "1", timeout=5)
    body = run_browser('js', 'document.body.innerText', timeout=5)
    
    if not body:
        print("  ❌ 获取字幕列表失败（超时）")
        return []
    
    try:
        data = json.loads(body)
        if data.get("code") != 0:
            print(f"  ❌ API错误: {data.get('message', '未知')}")
            return []
        
        subtitle_data = data.get("data", {}).get("subtitle", {})
        need_login = subtitle_data.get("need_login_subtitle", False)
        subtitles = subtitle_data.get("subtitles", [])
        
        if need_login and not subtitles:
            print("  ❌ 需要登录才能获取字幕（浏览器未登录B站）")
            return []
        
        if not subtitles:
            print("  ⚠️  该视频没有可用字幕")
            return []
        
        print(f"  ✅ 找到 {len(subtitles)} 条字幕:")
        for s in subtitles:
            lan_map = {"ai-zh": "AI中文", "zh": "中文CC", "en": "英文"}
            lan_name = lan_map.get(s["lan"], s["lan"])
            print(f"     - [{lan_name}] {s.get('lan_doc', '')}")
        
        return subtitles
    except (json.JSONDecodeError, KeyError) as e:
        print(f"  ❌ 解析失败: {e}")
        return []


def download_subtitle(subtitle_item):
    """下载字幕JSON"""
    lan = subtitle_item["lan"]
    lan_map = {"ai-zh": "AI中文", "zh": "中文CC"}
    lan_name = lan_map.get(lan, lan)
    print(f"[3/4] 下载字幕 ({lan_name})...")
    
    sub_url = subtitle_item["subtitle_url"]
    if sub_url.startswith("//"):
        sub_url = "https:" + sub_url
    
    run_browser("goto_url", sub_url, timeout=10)
    run_browser("wait_for_load", "10", timeout=10)
    run_browser("wait", "1", timeout=5)
    body = run_browser('js', 'document.body.innerText', timeout=5)
    
    if not body:
        print("  ❌ 下载失败（超时）")
        return None
    
    try:
        sub_json = json.loads(body)
        if "body" not in sub_json:
            print("  ❌ 字幕JSON格式异常（无body字段）")
            return None
        
        items = sub_json["body"]
        print(f"  ✅ 下载成功，共 {len(items)} 条字幕")
        return items
    except json.JSONDecodeError as e:
        print(f"  ❌ 解析字幕JSON失败: {e}")
        return None


def format_subtitle(items, plain_only=False):
    """格式化字幕文本"""
    lines = []
    for item in items:
        start = item.get("from", 0)
        end = item.get("to", 0)
        content = item.get("content", "")
        
        if plain_only:
            lines.append(content)
        else:
            lines.append(f"[{start:.1f}s - {end:.1f}s] {content}")
    
    return lines


def main():
    parser = argparse.ArgumentParser(description="B站字幕提取工具")
    parser.add_argument("bvid", help="视频BVID（如 BV124TD6GECb）")
    parser.add_argument("--output", "-o", help="输出文件路径（默认打印到终端）")
    parser.add_argument("--plain", "-p", action="store_true", help="只输出纯文本，不带时间戳")
    parser.add_argument("--lang", default="ai-zh", help="字幕语言（默认 ai-zh，可选 zh）")
    args = parser.parse_args()
    
    bvid = args.bvid.strip()
    
    # 校验BVID格式
    if not re.match(r"^BV[a-zA-Z0-9]{10,}$", bvid):
        print("❌ BVID格式错误，应为 BV 开头的12位字符串")
        sys.exit(1)
    
    print(f"🎬 B站字幕提取工具")
    print(f"{'=' * 50}")
    
    # Step 1: 获取视频信息
    result = get_video_info(bvid)
    if result is None or result[0] is None:
        print("\n❌ 提取失败")
        sys.exit(1)
    
    aid, cid, title = result
    
    # Step 2: 获取字幕列表
    subtitles = get_subtitle_list(aid, cid)
    if not subtitles:
        print("\n❌ 提取失败：无可用字幕")
        sys.exit(1)
    
    # 选择字幕（优先用户指定的语言）
    target_sub = None
    for s in subtitles:
        if s["lan"] == args.lang:
            target_sub = s
            break
    if not target_sub:
        target_sub = subtitles[0]
        print(f"  ⚠️  未找到 {args.lang} 字幕，使用 {target_sub['lan']}")
    
    # Step 3: 下载字幕
    items = download_subtitle(target_sub)
    if not items:
        print("\n❌ 提取失败：字幕下载错误")
        sys.exit(1)
    
    # Step 4: 格式化输出
    print(f"[4/4] 格式化字幕...")
    lines = format_subtitle(items, plain_only=args.plain)
    
    if args.plain:
        output_text = "\n".join(lines)
    else:
        output_text = "\n".join(lines)
    
    header = (
        f"{'=' * 60}\n"
        f"《{title}》\n"
        f"BVID: {bvid} | 字幕: {target_sub['lan']}\n"
        f"共 {len(lines)} 条字幕\n"
        f"{'=' * 60}\n\n"
    )
    
    full_output = header + output_text
    
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(full_output)
        print(f"  ✅ 已保存到: {args.output}")
    else:
        print(f"\n{full_output}")
    
    print(f"\n✅ 提取完成！共 {len(lines)} 条字幕")


if __name__ == "__main__":
    main()
