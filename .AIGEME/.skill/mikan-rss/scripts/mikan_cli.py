#!/usr/bin/env python3
"""Mikan Project (蜜柑计划) RSS CLI Tool.

A pure-Python command-line tool for searching anime torrents, managing
subscriptions, and downloading .torrent files from Mikanani.me RSS feeds.

Usage:
    python mikan_cli.py search <keyword> [--page N] [--group-id G] [--limit N]
    python mikan_cli.py season
    python mikan_cli.py list-groups <keyword>
    python mikan_cli.py export <keyword> [--page N] [--group-id G] [--output FILE]
    python mikan_cli.py download <keyword> [--group-id G] [--episode N] [--dir DIR]
    python mikan_cli.py subscribe add <name> --group-id G --group-name NAME
    python mikan_cli.py subscribe list
    python mikan_cli.py subscribe remove <name>
    python mikan_cli.py check
"""

import argparse
import html
import io
import json
import os
import re
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Windows GBK console fix: wrap stdout/stderr with UTF-8 writer
# ---------------------------------------------------------------------------
if sys.stdout.encoding and sys.stdout.encoding.upper() in ('GBK', 'CP936', 'GB2312'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr.encoding and sys.stderr.encoding.upper() in ('GBK', 'CP936', 'GB2312'):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://mikanani.me"
SEARCH_URL = f"{BASE_URL}/RSS/Search"
BANGUMI_URL = f"{BASE_URL}/RSS/Bangumi"
MAIN_URL = f"{BASE_URL}/"
SUBSCRIPTION_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "mikan_subscriptions.json")
REQUEST_TIMEOUT = 15  # seconds

# XML namespaces used in Mikan RSS
NS = {
    "torrent": "https://mikanani.me/0.1/",
}

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class TorrentItem:
    """Represents a single torrent entry from the Mikan RSS feed."""
    title: str
    link: str
    enclosure_url: str  # .torrent download URL or magnet link
    content_length: int = 0  # bytes
    pub_date: str = ""

    # Extracted metadata (populated by _parse_title)
    group_name: str = ""
    episode: str = ""
    quality: str = ""
    anime_name: str = ""


@dataclass
class Subscription:
    """A tracked anime subscription stored locally."""
    name: str
    group_id: str
    group_name: str
    added_date: str = ""


# ---------------------------------------------------------------------------
# HTTP / RSS helpers
# ---------------------------------------------------------------------------

def _fetch_url(url: str, timeout: int = REQUEST_TIMEOUT) -> str:
    """Fetch a URL and return its text content.

    Raises:
        SystemExit: On network error.
    """
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) "
                       "Gecko/20100101 Firefox/120.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            # Try UTF-8 first, fall back to detected encoding
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                # Heuristic: strip XML declaration and decode as UTF-8 anyway
                return raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        print(f"  [Error] HTTP {e.code}: {e.reason}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"  [Error] Network error: {e.reason}", file=sys.stderr)
        sys.exit(1)
    except OSError as e:
        print(f"  [Error] Connection failed: {e}", file=sys.stderr)
        sys.exit(1)


def _parse_rss(xml_text: str) -> List[TorrentItem]:
    """Parse a Mikan RSS XML string into a list of TorrentItem.

    Handles namespace-aware parsing for ``torrent:contentLength`` and
    ``torrent:pubDate``.
    """
    items: List[TorrentItem] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"  [Error] Failed to parse RSS XML: {e}", file=sys.stderr)
        return items

    for item_elem in root.iter("item"):
        title_el = item_elem.find("title")
        link_el = item_elem.find("link")
        enclosure_el = item_elem.find("enclosure")
        length_el = item_elem.find(".//torrent:contentLength", NS)
        pub_el = item_elem.find(".//torrent:pubDate", NS)

        title = title_el.text if title_el is not None else ""
        link = link_el.text if link_el is not None else ""
        enclosure_url = enclosure_el.get("url", "") if enclosure_el is not None else ""

        content_length = 0
        if length_el is not None and length_el.text:
            try:
                content_length = int(length_el.text)
            except ValueError:
                content_length = 0

        pub_date = pub_el.text if pub_el is not None else ""

        item = TorrentItem(
            title=title,
            link=link,
            enclosure_url=enclosure_url,
            content_length=content_length,
            pub_date=pub_date,
        )
        _parse_title(item)
        items.append(item)

    return items


def _parse_title(item: TorrentItem) -> None:
    """Extract group name, episode number, quality and anime name from title.

    Typical title formats::

        [SubGroup] Anime Name - 01 (1080p) [...]
        [SubGroup] Anime Name 第01話 (1080p) [...]
        [SubGroup] Anime Name S2 - 01 (1080p) [...]

    This is a best-effort parser; unmatched titles remain with empty fields.
    """
    title = item.title.strip()
    if not title:
        return

    # Extract group name from brackets: [GroupName] or 【GroupName】
    group_match = re.match(r'[\[【]([^\]】]+)[\]】]\s*', title)
    if group_match:
        item.group_name = group_match.group(1).strip()
        rest = title[group_match.end():]
    else:
        rest = title

    # Extract episode number: look for patterns like "- 01", "第01話", "EP01", or
    # a bare "[01]" / "【01】" bracket group right after the group name prefix.
    episode_patterns = [
        (r'[－\-]\s*(\d+\.?\d*)', 1),        # "- 01" or "－ 01"
        (r'第(\d+\.?\d*)', 1),                 # "第01話"
        (r'EP\s*(\d+)', 1),                    # "EP01"
        (r'[Ee]pisode\s+(\d+)', 1),           # "Episode 01"
        (r'Vol[.．]?\s*(\d+)', 1),            # "Vol.01"
        (r'[\[【](\d+\.?\d*)[\]】]', 1),      # "[38]" or "【38】" — lowest priority
    ]
    episode = ""
    for pattern, group_idx in episode_patterns:
        m = re.search(pattern, rest)
        if m:
            episode = m.group(group_idx)
            break

    # Extract quality / resolution: e.g. (1080p), [720p], 4K, etc.
    quality_patterns = [
        r'(2160p|1080p|720p|480p|360p)',
        r'(4K|8K)',
        r'(Hi10P|Hi444PP|x264|x265|HEVC|AVC)',
    ]
    quality = ""
    for pattern in quality_patterns:
        m = re.search(pattern, rest, re.IGNORECASE)
        if m:
            quality = m.group(1)
            break

    # Anime name: everything between group name and episode number
    anime_name = rest.strip()
    if episode:
        # Try to remove episode part from the end of anime_name
        ep_re = re.search(
            r'[－\-]\s*\d+\.?\d*|第\d+\.?\d*話|EP\s*\d+|'
            r'[Ee]pisode\s+\d+|Vol[.．]?\s*\d+',
            anime_name
        )
        if ep_re:
            anime_name = anime_name[:ep_re.start()].strip()
        # Also strip quality/tags at the end
        anime_name = re.sub(r'[\(\[][^\)\]]*[\)\]]', '', anime_name).strip()
        # Remove trailing dash/hyphen
        anime_name = re.sub(r'[－\-]\s*$', '', anime_name).strip()

    item.episode = episode
    item.quality = quality
    item.anime_name = anime_name or rest


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _fmt_size(bytes_val: int) -> str:
    """Format byte count to human-readable string."""
    if bytes_val >= 1_073_741_824:
        return f"{bytes_val / 1_073_741_824:.2f} GB"
    elif bytes_val >= 1_048_576:
        return f"{bytes_val / 1_048_576:.2f} MB"
    elif bytes_val >= 1_024:
        return f"{bytes_val / 1_024:.2f} KB"
    return f"{bytes_val} B"


def _fmt_date(rss_date: str) -> str:
    """Convert RSS date string to a compact ``YYYY-MM-DD`` format.

    RSS dates look like: ``Sun, 28 Jan 2024 12:00:00 +0900``
    """
    if not rss_date:
        return ""
    # Strip day-of-week prefix
    clean = re.sub(r'^[A-Z][a-z]+,\s*', '', rss_date)
    # Try parsing with timezone
    for fmt in ("%d %b %Y %H:%M:%S %z", "%d %b %Y %H:%M:%S"):
        try:
            dt = datetime.strptime(clean.strip(), fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return rss_date[:10]  # fallback


def _print_table(items: List[TorrentItem],
                 limit: Optional[int] = None,
                 start_index: int = 1) -> None:
    """Print a formatted table of torrent items.

    Args:
        items: Torrent items to display.
        limit: Maximum number of rows to show (``None`` = all).
        start_index: Starting index number for the first row.
    """
    if not items:
        print("  (no results)")
        return

    show_items = items[:limit] if limit is not None else items

    # Column widths
    col_idx = 4
    col_group = max(10, max((len(it.group_name) for it in show_items), default=0))
    col_ep = 5
    col_quality = max(7, max((len(it.quality) for it in show_items), default=0))
    col_size = 10
    col_date = 10
    # Title column: use anime_name if available, otherwise a truncated full title
    col_title = max(24, max(
        (len(it.anime_name) if it.anime_name else 0
         for it in show_items), default=0))
    col_title = min(col_title, 40)  # cap title width

    # Header
    sep = "-" * (col_idx + col_group + col_ep + col_quality + col_size + col_date + col_title + 7)
    print(f"  {sep}")
    print(f"  {'#':<{col_idx}} {'字幕组':<{col_group}} {'集数':<{col_ep}} "
          f"{'画质':<{col_quality}} {'大小':<{col_size}} {'日期':<{col_date}}"
          f" {'番剧名':<{col_title}}")
    print(f"  {sep}")

    # Rows
    for i, item in enumerate(show_items, start=start_index):
        size_str = _fmt_size(item.content_length) if item.content_length else "-"
        date_str = _fmt_date(item.pub_date)
        ep_str = item.episode if item.episode else "-"
        qual_str = item.quality if item.quality else "-"
        group_str = item.group_name if item.group_name else "-"
        # Show anime_name if available, otherwise truncate raw title
        title_str = item.anime_name if item.anime_name else item.title
        if len(title_str) > col_title:
            title_str = title_str[:col_title - 3] + "..."

        print(f"  {i:<{col_idx}} {group_str:<{col_group}} {ep_str:<{col_ep}} "
              f"{qual_str:<{col_quality}} {size_str:<{col_size}} {date_str:<{col_date}}"
              f" {title_str:<{col_title}}")

    print(f"  {sep}")


# ---------------------------------------------------------------------------
# Core search / fetch logic
# ---------------------------------------------------------------------------

def fetch_search(keyword: str,
                 page: int = 1,
                 group_id: Optional[str] = None) -> Tuple[List[TorrentItem], str]:
    """Search Mikan RSS and return (items, raw_xml).

    Args:
        keyword: Search keyword (anime name).
        page: Page number (1-based).
        group_id: Optional subtitle group ID to filter by.

    Returns:
        Tuple of (parsed torrent items, raw XML string).
    """
    params: Dict[str, str] = {
        "searchstr": keyword,
        "page": str(page),
    }
    if group_id:
        params["subgroupid"] = str(group_id)

    url = f"{SEARCH_URL}?{urllib.parse.urlencode(params)}"
    raw_xml = _fetch_url(url)
    items = _parse_rss(raw_xml)
    return items, raw_xml


def fetch_bangumi(bangumi_id: str,
                  group_id: Optional[str] = None) -> List[TorrentItem]:
    """Fetch RSS by bangumi ID and optional group ID.

    Args:
        bangumi_id: The bangumi/anime ID on Mikan.
        group_id: Optional subtitle group ID.

    Returns:
        Parsed torrent items.
    """
    params: Dict[str, str] = {"bangumiId": bangumi_id}
    if group_id:
        params["subgroupid"] = str(group_id)

    url = f"{BANGUMI_URL}?{urllib.parse.urlencode(params)}"
    raw_xml = _fetch_url(url)
    return _parse_rss(raw_xml)


# ---------------------------------------------------------------------------
# Season list (scrape main page)
# ---------------------------------------------------------------------------


def fetch_season_list() -> Dict[str, List[Tuple[str, str]]]:
    """Scrape the Mikan main page to get the current season's anime list.

    Returns:
        Dict mapping weekday → list of (bangumi_name, bangumi_id).
    """
    html_text = _fetch_url(MAIN_URL)
    season: Dict[str, List[Tuple[str, str]]] = {}
    current_day = ""
    # Map HTML entity refs to weekday names (decoded)
    weekday_entities = {
        "星期一": "星期一", "星期二": "星期二", "星期三": "星期三",
        "星期四": "星期四", "星期五": "星期五", "星期六": "星期六", "星期日": "星期日",
    }

    for line in html_text.split("\n"):
        s = line.strip()
        # Detect weekday header (either date-cn in header area or stand-alone in content area)
        detected_day = ""
        for wd_name in weekday_entities:
            wd_encoded = ''.join(f'&#x{ord(c):X};' for c in wd_name)
            if wd_encoded in s:
                detected_day = wd_name
                break
        if detected_day:
            current_day = detected_day
            if current_day not in season:
                season[current_day] = []
            continue

        # Detect bangumi links
        m = re.search(r'<a[^>]*href="/Home/Bangumi/(\d+)"[^>]*>([^<]+)</a>', s)
        if m and current_day:
            bid = m.group(1)
            name = html.unescape(m.group(2)).strip()
            if name and not any(name == n for n, _ in season[current_day]):
                season[current_day].append((name, bid))

    return season


def cmd_season(args: argparse.Namespace) -> None:
    """Display the current season's anime list grouped by weekday."""
    print("\n  Fetching current season lineup ...\n")

    try:
        season = fetch_season_list()
    except Exception as e:
        print(f"  [Error] Failed to fetch season list: {e}\n")
        return

    if not season:
        print("  No anime found for the current season.\n")
        return

    total = sum(len(items) for items in season.values())
    for day, items in season.items():
        if not items:
            continue
        col_name = max(len(name) for name, _ in items)
        sep = "-" * (col_name + 20)
        print(f"  [{day}]")
        print(f"  {sep}")
        for name, bid in items:
            print(f"  {name:<{col_name}}  (bangumiId={bid})")
        print(f"  {sep}\n")

    print(f"  ({total} anime in total)\n")


# ---------------------------------------------------------------------------
# Subcommand: search
# ---------------------------------------------------------------------------

def cmd_search(args: argparse.Namespace) -> None:
    """Search for anime torrents and display results in a table."""
    keyword = args.keyword
    page = args.page
    group_id = args.group_id
    limit = args.limit

    print(f"\n  Searching for \"{keyword}\" (page {page}) ...\n")

    items, _ = fetch_search(keyword, page=page, group_id=group_id)
    total = len(items)

    if total == 0:
        print(f"  No results for \"{keyword}\" (page {page}). Try a different keyword or page.\n")
        return

    _print_table(items, limit=limit, start_index=1)

    # Item count per full page (Mikan usually returns 50 per page)
    items_per_page = max(len(items), 50)
    if total < items_per_page:
        print(f"\n  (end of results -- {total} item(s) on this page)\n")
    else:
        print(f"\n  ({total} item(s) on this page, use --page {page + 1} for next)\n")


# ---------------------------------------------------------------------------
# Subcommand: list-groups
# ---------------------------------------------------------------------------

def cmd_list_groups(args: argparse.Namespace) -> None:
    """Search for an anime and show all subtitle groups with their IDs."""
    keyword = args.keyword

    print(f"\n  Scanning groups for \"{keyword}\" ...\n")

    # Fetch first page of search results
    items, raw_xml = fetch_search(keyword, page=1)

    # Collect group statistics from titles
    group_counter: Dict[str, int] = {}
    for item in items:
        name = item.group_name if item.group_name else "(unknown)"
        group_counter[name] = group_counter.get(name, 0) + 1

    if not group_counter:
        print("  No results found.\n")
        return

    # Try to get group IDs from the bangumi page
    group_ids: Dict[str, str] = {}
    try:
        # Extract bangumiId from the first item's episode page
        if items and items[0].link:
            ep_html = _fetch_url(items[0].link)
            m = re.search(r'bangumiId=(\d+)', ep_html)
            if m:
                bangumi_id = m.group(1)
                # Fetch bangumi page to get all subgroup IDs and names
                bg_url = f"{BASE_URL}/Home/Bangumi/{bangumi_id}"
                bg_html = _fetch_url(bg_url)
                # Parse subgroup-text divs: <div class="subgroup-text" id="123">
                #   <a href="/Home/PublishGroup/..." ...>字幕组名</a>
                for m2 in re.finditer(
                    r'<div class="subgroup-text"[^>]*id="(\d+)"[^>]*>.*?'
                    r'<a[^>]*>([^<]+)</a>',
                    bg_html, re.DOTALL
                ):
                    sid = m2.group(1)
                    gname = m2.group(2).strip()
                    # Decode HTML entities
                    gname = html.unescape(gname)
                    if gname and sid not in group_ids.values():
                        group_ids[gname] = sid
    except Exception:
        pass  # group IDs are best-effort

    # Sort by count descending
    sorted_groups = sorted(group_counter.items(), key=lambda x: -x[1])

    # Show table with optional group_id column
    has_ids = any(name in group_ids for name, _ in sorted_groups)
    col_name_len = max(12, max(len(g) for g, _ in sorted_groups))
    col_count_len = 6
    col_id_len = 12 if has_ids else 0

    sep = "-" * (col_name_len + col_count_len + col_id_len + 4)
    print(f"  {sep}")
    header = f"  {'字幕组':<{col_name_len}} {'次数':<{col_count_len}}"
    if has_ids:
        header += f" {'字幕组ID':<{col_id_len}}"
    print(header)
    print(f"  {sep}")
    for name, count in sorted_groups:
        line = f"  {name:<{col_name_len}} {count:<{col_count_len}}"
        if has_ids:
            sid = group_ids.get(name, "?")
            line += f" {sid:<{col_id_len}}"
        print(line)
    print(f"  {sep}")
    if has_ids:
        print(f"  ({len(sorted_groups)} group(s) found, use --group-id <ID> to filter)\n")
    else:
        print(f"  ({len(sorted_groups)} group(s) found, group IDs unavailable)\n")


# ---------------------------------------------------------------------------
# Subcommand: export
# ---------------------------------------------------------------------------

def cmd_export(args: argparse.Namespace) -> None:
    """Search for an anime and export the results to a text file."""
    keyword = args.keyword
    page = args.page
    group_id = args.group_id
    output = args.output

    print(f"\n  Searching for \"{keyword}\" (page {page}) ...\n")

    items, _ = fetch_search(keyword, page=page, group_id=group_id)

    if not items:
        print("  No results found. Nothing to export.\n")
        return

    # Auto-generate output filename if not provided
    if not output:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_keyword = re.sub(r'[\\/*?:"<>|]', '_', keyword)[:30]
        output = f"mikan_export_{safe_keyword}_{timestamp}.txt"

    lines: List[str] = []
    for item in items:
        group = item.group_name if item.group_name else "?"
        ep = item.episode if item.episode else "?"
        quality = item.quality if item.quality else "?"
        size = _fmt_size(item.content_length) if item.content_length else "?"
        link = item.enclosure_url if item.enclosure_url else item.link
        anime_title = item.anime_name if item.anime_name else item.title
        lines.append(f"[{group}] {anime_title} | EP{ep} {quality} {size} {link}")

    try:
        with open(output, "w", encoding="utf-8") as f:
            f.write(f"# Mikan Export: {keyword} (page {page})\n")
            f.write(f"# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            if group_id:
                f.write(f"# Group ID: {group_id}\n")
            f.write(f"# Total items: {len(lines)}\n")
            f.write("# " + "-" * 60 + "\n")
            f.write("\n".join(lines))
            f.write("\n")
        print(f"  Exported {len(lines)} item(s) to: {output}\n")
    except OSError as e:
        print(f"  [Error] Failed to write file: {e}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Subcommand: download
# ---------------------------------------------------------------------------

def _download_file(url: str, dest_path: str) -> None:
    """Download a file from *url* to *dest_path* with a progress indicator."""
    req = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) "
                       "Gecko/20100101 Firefox/120.0"),
    })
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 8192

            with open(dest_path, "wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        pct = downloaded * 100 // total
                        bar_len = 40
                        filled = pct * bar_len // 100
                        bar = "#" * filled + "." * (bar_len - filled)
                        print(f"\r    [{bar}] {pct}% ({_fmt_size(downloaded)}/{_fmt_size(total)})",
                              end="", flush=True)
                    else:
                        print(f"\r    Downloaded {_fmt_size(downloaded)} ...",
                              end="", flush=True)
            print()  # newline after progress
    except urllib.error.HTTPError as e:
        print(f"\n  [Error] HTTP {e.code}: {e.reason}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"\n  [Error] Network error: {e.reason}", file=sys.stderr)
        sys.exit(1)
    except OSError as e:
        print(f"\n  [Error] Failed to write file: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_download(args: argparse.Namespace) -> None:
    """Search for an anime and download the specified episode's .torrent file."""
    keyword = args.keyword
    group_id = args.group_id
    episode = args.episode
    dest_dir = args.dir

    print(f"\n  Searching for \"{keyword}\" ...\n")

    items, _ = fetch_search(keyword, page=1, group_id=group_id)

    if not items:
        print("  No results found.\n")
        return

    # --all: download all batch/full-season packs (items without episode number)
    dl_all = getattr(args, 'all', False)
    if dl_all:
        batch_items = [it for it in items if not it.episode]
        if not batch_items:
            print("  No batch/full-season packs found (all items have episode numbers).\n")
            return
        os.makedirs(dest_dir, exist_ok=True)
        for i, target in enumerate(batch_items, 1):
            torrent_url = target.enclosure_url
            if not torrent_url:
                print(f"  [{i}/{len(batch_items)}] No torrent URL for \"{target.title[:50]}\", skip.\n")
                continue
            safe_name = re.sub(r'[\\/*?:"<>|]', '_', target.title).strip()
            if not safe_name:
                safe_name = f"batch_{int(time.time())}_{i}"
            if not safe_name.endswith(".torrent"):
                safe_name += ".torrent"
            dest_path = os.path.join(dest_dir, safe_name)
            print(f"  [{i}/{len(batch_items)}] Downloading: {target.title[:60]}...")
            _download_file(torrent_url, dest_path)
            print(f"  [OK] -> {dest_path}\n")
        print(f"  Downloaded {len(batch_items)} batch pack(s) to: {dest_dir}\n")
        return

    # If episode not specified, pick the latest (highest episode number)
    if episode is None:
        # Check if --all was used (handled above before this block)
        seen: List[TorrentItem] = []
        seen_eps: set = set()
        for item in items:
            if item.episode and item.episode not in seen_eps:
                seen.append(item)
                seen_eps.add(item.episode)

        if not seen:
            # No episode-numbered items found, check for batch packs
            batch = [it for it in items if not it.episode]
            if batch:
                print("  No individual episodes found, but batch pack(s) available.\n"
                      "  Use --all to download batch packs.\n")
            else:
                print("  Could not determine episodes from titles.\n"
                      "  Try specifying --episode or --all for batch packs.\n")
            return

        # Pick the one with largest episode number (numeric)
        seen.sort(key=lambda x: float(x.episode) if x.episode.replace('.', '', 1).isdigit() else 0,
                  reverse=True)
        target = seen[0]
        print(f"  Latest episode detected: episode {target.episode}\n")
    else:
        ep_str = str(episode)
        candidates = [it for it in items if it.episode == ep_str]
        if not candidates:
            # Fallback: check if there are batch packs with no episode number
            batch = [it for it in items if not it.episode]
            if batch:
                print(f"  No episode {episode} found, but {len(batch)} batch pack(s) available."
                      f" Use --all to download them.\n")
            else:
                print(f"  No items found for episode {episode}.\n")
            return
        # Pick the first match
        target = candidates[0]

    torrent_url = target.enclosure_url
    if not torrent_url:
        print(f"  [Error] No torrent URL found for \"{target.title}\".\n")
        return

    # Build safe filename from title
    safe_name = re.sub(r'[\\/*?:"<>|]', '_', target.title).strip()
    if not safe_name:
        safe_name = f"torrent_{int(time.time())}"
    if not safe_name.endswith(".torrent"):
        safe_name += ".torrent"

    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, safe_name)

    print(f"  Downloading: {target.title}")
    print(f"  From: {torrent_url}")
    print(f"  To:   {dest_path}\n")

    _download_file(torrent_url, dest_path)
    print(f"\n  [OK] Saved to: {dest_path}\n")


# ---------------------------------------------------------------------------
# Subcommand: subscribe
# ---------------------------------------------------------------------------

def _load_subscriptions() -> List[Subscription]:
    """Load subscriptions from the local JSON file.

    Returns:
        An empty list if the file does not exist or is malformed.
    """
    if not os.path.isfile(SUBSCRIPTION_FILE):
        return []
    try:
        with open(SUBSCRIPTION_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, list):
            return []
        subs: List[Subscription] = []
        for entry in raw:
            if isinstance(entry, dict):
                subs.append(Subscription(
                    name=entry.get("name", ""),
                    group_id=str(entry.get("group_id", "")),
                    group_name=entry.get("group_name", ""),
                    added_date=entry.get("added_date", ""),
                ))
        return subs
    except (json.JSONDecodeError, OSError):
        return []


def _save_subscriptions(subs: List[Subscription]) -> None:
    """Persist subscription list to the local JSON file."""
    raw: List[Dict[str, str]] = [
        {
            "name": s.name,
            "group_id": s.group_id,
            "group_name": s.group_name,
            "added_date": s.added_date,
        }
        for s in subs
    ]
    try:
        with open(SUBSCRIPTION_FILE, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
    except OSError as e:
        print(f"  [Error] Failed to write subscriptions: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_subscribe_add(args: argparse.Namespace) -> None:
    """Add an anime to the subscription list."""
    name = args.name
    group_id = str(args.group_id) if args.group_id else ""
    group_name = args.group_name if args.group_name else ""

    subs = _load_subscriptions()

    # Check for duplicate
    for s in subs:
        if s.name == name and s.group_id == group_id:
            print(f"  \"{name}\" (group {group_id}) is already subscribed.\n")
            return

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    subs.append(Subscription(
        name=name,
        group_id=group_id,
        group_name=group_name,
        added_date=now_str,
    ))
    _save_subscriptions(subs)
    print(f"  [OK] Subscribed to \"{name}\" (group: {group_name or group_id or 'all'})\n")


def cmd_subscribe_list(args: argparse.Namespace) -> None:  # noqa: ARG001
    """List all subscribed anime."""
    subs = _load_subscriptions()

    if not subs:
        print("  No subscriptions yet.\n")
        print("  Add one with: python mikan_cli.py subscribe add <name> --group-id <id>\n")
        return

    col_name = max(20, max((len(s.name) for s in subs), default=0))
    col_gid = 12
    col_gname = max(12, max((len(s.group_name) for s in subs), default=0))
    col_date = 22

    sep = "-" * (col_name + col_gid + col_gname + col_date + 5)
    print(f"  {sep}")
    print(f"  {'番剧名':<{col_name}} {'字幕组ID':<{col_gid}} "
          f"{'字幕组名':<{col_gname}} {'添加日期':<{col_date}}")
    print(f"  {sep}")
    for i, s in enumerate(subs, start=1):
        print(f"  {s.name:<{col_name}} {s.group_id:<{col_gid}} "
              f"{s.group_name:<{col_gname}} {s.added_date:<{col_date}}")
    print(f"  {sep}")
    print(f"  ({len(subs)} subscription(s))\n")


def cmd_subscribe_remove(args: argparse.Namespace) -> None:
    """Remove an anime from the subscription list (by name match)."""
    name = args.name
    subs = _load_subscriptions()
    before = len(subs)
    subs = [s for s in subs if s.name != name]
    removed = before - len(subs)

    if removed == 0:
        print(f"  \"{name}\" not found in subscriptions.\n")
        return

    _save_subscriptions(subs)
    print(f"  [OK] Removed \"{name}\" ({removed} record(s)).\n")


# ---------------------------------------------------------------------------
# Subcommand: check
# ---------------------------------------------------------------------------

def cmd_check(args: argparse.Namespace) -> None:  # noqa: ARG001
    """Check all subscriptions for new episodes."""
    subs = _load_subscriptions()

    if not subs:
        print("  No subscriptions to check.\n")
        return

    print(f"  Checking {len(subs)} subscription(s) ...\n")

    for s in subs:
        print(f"  >> {s.name}", end="")
        if s.group_name:
            print(f" [{s.group_name}]", end="")
        print()

        try:
            group_id = s.group_id if s.group_id and s.group_id != "0" else None
            items, _ = fetch_search(s.name, page=1, group_id=group_id)
        except SystemExit:
            print(f"    +-- [Error] Network request failed\n")
            continue

        if not items:
            print(f"    +-- No results found\n")
            continue

        # Find the latest episode
        candidates = [it for it in items if it.episode]
        if not candidates:
            print(f"    +-- Could not determine episode numbers\n")
            # Show the first torrent link as fallback
            first = items[0]
            link = first.enclosure_url or first.link
            print(f"    +-- Latest: {first.title}")
            print(f"    +-- Link: {link}\n")
            continue

        # Sort by episode number descending
        def _ep_key(it: TorrentItem) -> float:
            try:
                return float(it.episode)
            except ValueError:
                return 0.0

        candidates.sort(key=_ep_key, reverse=True)
        latest = candidates[0]
        date_str = _fmt_date(latest.pub_date)
        link = latest.enclosure_url or latest.link

        print(f"    +-- Latest episode: {latest.episode}")
        print(f"    +-- Quality: {latest.quality or 'N/A'}")
        print(f"    +-- Size: {_fmt_size(latest.content_length) if latest.content_length else 'N/A'}")
        if date_str:
            print(f"    +-- Date: {date_str}")
        print(f"    +-- Link: {link}")
        print()

    print("  Done.\n")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="mikan_cli",
        description="Mikan Project RSS CLI Tool - search, download, track anime.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python mikan_cli.py search "葬送的芙莉莲"
              python mikan_cli.py search "葬送的芙莉莲" --page 2 --group-id 583 --limit 10
              python mikan_cli.py list-groups "葬送的芙莉莲"
              python mikan_cli.py export "葬送的芙莉莲" --output results.txt
              python mikan_cli.py download "葬送的芙莉莲" --group-id 583 --episode 10
              python mikan_cli.py subscribe add "葬送的芙莉莲" --group-id 583 --group-name "喵萌奶茶屋"
              python mikan_cli.py subscribe list
              python mikan_cli.py subscribe remove "葬送的芙莉莲"
              python mikan_cli.py check
        """),
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # --- search ---
    p_search = sub.add_parser("search", help="Search for anime torrents")
    p_search.add_argument("keyword", type=str, help="Anime name or keyword")
    p_search.add_argument("--page", type=int, default=1, help="Page number (default: 1)")
    p_search.add_argument("--group-id", type=str, default=None,
                          help="Subtitle group ID to filter by")
    p_search.add_argument("--limit", type=int, default=None,
                          help="Max results to display (default: all)")

    # --- list-groups ---
    p_groups = sub.add_parser("list-groups", help="List all subtitle groups for an anime")
    p_groups.add_argument("keyword", type=str, help="Anime name or keyword")

    # --- season ---
    p_season = sub.add_parser("season", help="Show current season's anime lineup")
    # Placeholder for future year/season args

    # --- export ---
    p_export = sub.add_parser("export", help="Export search results to a text file")
    p_export.add_argument("keyword", type=str, help="Anime name or keyword")
    p_export.add_argument("--page", type=int, default=1, help="Page number (default: 1)")
    p_export.add_argument("--group-id", type=str, default=None,
                          help="Subtitle group ID to filter by")
    p_export.add_argument("--output", type=str, default=None,
                          help="Output file path (auto-generated if omitted)")

    # --- download ---
    p_dl = sub.add_parser("download", help="Download a .torrent file")
    p_dl.add_argument("keyword", type=str, help="Anime name or keyword")
    p_dl.add_argument("--group-id", type=str, default=None,
                      help="Subtitle group ID to filter by")
    p_dl.add_argument("--episode", type=int, default=None,
                      help="Episode number (default: latest)")
    p_dl.add_argument("--all", action="store_true",
                      help="Download full-season/batch packs (items without episode number)")
    p_dl.add_argument("--dir", type=str, default="./downloads",
                      help="Download directory (default: ./downloads)")

    # --- subscribe ---
    p_sub = sub.add_parser("subscribe", help="Manage subscriptions")
    sub_sub = p_sub.add_subparsers(dest="action", required=True)

    p_sub_add = sub_sub.add_parser("add", help="Add a subscription")
    p_sub_add.add_argument("name", type=str, help="Anime name")
    p_sub_add.add_argument("--group-id", type=int, default=None,
                           help="Subtitle group ID")
    p_sub_add.add_argument("--group-name", type=str, default="",
                           help="Subtitle group name")

    sub_sub.add_parser("list", help="List all subscriptions")

    p_sub_rm = sub_sub.add_parser("remove", help="Remove a subscription")
    p_sub_rm.add_argument("name", type=str, help="Anime name to remove")

    # --- check ---
    sub.add_parser("check", help="Check subscriptions for new episodes")

    return parser


# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry point: parse arguments and dispatch to the appropriate command."""
    parser = build_parser()
    args = parser.parse_args()

    cmd = args.command
    if cmd == "search":
        cmd_search(args)
    elif cmd == "season":
        cmd_season(args)
    elif cmd == "list-groups":
        cmd_list_groups(args)
    elif cmd == "export":
        cmd_export(args)
    elif cmd == "download":
        cmd_download(args)
    elif cmd == "subscribe":
        action = args.action
        if action == "add":
            cmd_subscribe_add(args)
        elif action == "list":
            cmd_subscribe_list(args)
        elif action == "remove":
            cmd_subscribe_remove(args)
        else:
            parser.print_help()
    elif cmd == "check":
        cmd_check(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
