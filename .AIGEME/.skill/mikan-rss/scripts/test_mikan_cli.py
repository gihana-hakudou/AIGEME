"""Tests for mikan_cli.py — Mikan Project RSS CLI Tool.

All network calls are mocked; no real HTTP requests are made.
"""

import argparse
import builtins
import json
import os
import sys
from unittest.mock import MagicMock, mock_open, patch

import pytest

# Ensure the module can be imported
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mikan_cli


# Helper to create a minimally populated TorrentItem for tests
def _make_item(
    title="",
    link="",
    enclosure_url="",
    content_length=0,
    pub_date="",
    group_name="",
    episode="",
    quality="",
    anime_name="",
) -> mikan_cli.TorrentItem:
    return mikan_cli.TorrentItem(
        title=title,
        link=link,
        enclosure_url=enclosure_url,
        content_length=content_length,
        pub_date=pub_date,
        group_name=group_name,
        episode=episode,
        quality=quality,
        anime_name=anime_name,
    )


# =============================================================================
#  Tests for _parse_title
# =============================================================================

class TestParseTitle:
    """Extract group name, episode, quality, anime_name from torrent titles."""

    # (title, expected_group, expected_episode, expected_quality)
    TITLE_CASES = [
        # Standard formats from the PRD
        (
            "[喵萌奶茶屋] 葬送的芙莉莲 第二季 S2 [38][1080P][繁日双语]",
            "喵萌奶茶屋", "38", "1080P",
        ),
        (
            "[北宇治字幕组] 葬送的芙莉莲 第二季 S2 [37][1080p][简体内嵌]",
            "北宇治字幕组", "37", "1080p",
        ),
        (
            "[ANi] Sousou no Frieren S02 - 38 [1080P][Baha][WEB-DL]",
            "ANi", "38", "1080P",
        ),
        (
            "【桜都字幕组】葬送的芙莉莲 S2 [09][1080P][繁体内嵌]",
            "桜都字幕组", "09", "1080P",
        ),
        (
            "[LoliHouse] Frieren - 10 [WebRip 1080p HEVC-10bit]",
            "LoliHouse", "10", "1080p",
        ),
        # Edge cases
        ("", "", "", ""),                     # empty title
        ("[NoEpisode] Just a title", "NoEpisode", "", ""),
        ("[Group] Title EP01", "Group", "01", ""),
        ("[Group] Title Episode 05", "Group", "05", ""),
        ("[Group] Title Vol.3", "Group", "3", ""),
        ("[Group] Title 第12話", "Group", "12", ""),
        ("[Group] Title - 01 (720p)", "Group", "01", "720p"),
        ("[Group] Show S2 - 99 [2160p][HDR]", "Group", "99", "2160p"),
        ("[Group] Movie 4K HDR", "Group", "", "4K"),
        ("[Group] Title 第05話 (1080p)", "Group", "05", "1080p"),
        # Japanese-style brackets
        ("【组】动画名 EP07", "组", "07", ""),
        # No group brackets at all
        ("Raw Title - 01", "", "01", ""),
        ("Just a plain title with no metadata", "", "", ""),
    ]

    @pytest.mark.parametrize(
        "title,exp_group,exp_episode,exp_quality", TITLE_CASES
    )
    def test_parse_title(self, title, exp_group, exp_episode, exp_quality):
        item = _make_item(title=title)
        mikan_cli._parse_title(item)
        assert item.group_name == exp_group, f"group_name mismatch for {title!r}"
        assert item.episode == exp_episode, f"episode mismatch for {title!r}"
        assert item.quality == exp_quality, f"quality mismatch for {title!r}"

    def test_anime_name_populated(self):
        """Verify anime_name is extracted correctly."""
        item = _make_item(
            title="[喵萌奶茶屋] 葬送的芙莉莲 第二季 S2 [38][1080P][繁日双语]"
        )
        mikan_cli._parse_title(item)
        # The anime name portion should contain the show name
        assert "葬送的芙莉莲" in item.anime_name

    def test_parse_title_no_modify_on_empty(self):
        """Calling _parse_title on an item with empty title does not crash."""
        item = _make_item(title="")
        mikan_cli._parse_title(item)
        assert item.group_name == ""
        assert item.episode == ""
        assert item.quality == ""


# =============================================================================
#  Tests for _fmt_size
# =============================================================================

class TestFmtSize:
    """Format byte counts to human-readable strings."""

    @pytest.mark.parametrize(
        "bytes_val,expected",
        [
            (0, "0 B"),
            (1, "1 B"),
            (512, "512 B"),
            (1024, "1.00 KB"),
            (1536, "1.50 KB"),
            (1_048_576, "1.00 MB"),
            (912_250_624, "869.99 MB"),
            (2_147_483_648, "2.00 GB"),
            (10_737_418_240, "10.00 GB"),
            (1_073_741_824, "1.00 GB"),        # exactly 1 GB
            (1_048_576 - 1, "1024.00 KB"),     # 1 MB - 1 byte → still KB
        ],
    )
    def test_fmt_size(self, bytes_val, expected):
        assert mikan_cli._fmt_size(bytes_val) == expected


# =============================================================================
#  Tests for _parse_rss
# =============================================================================

# Sample RSS XML for testing (taken from the real Mikan feed)
SAMPLE_RSS_XML = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
<title>Mikan Project - 搜索结果:葬送的芙莉莲</title>
<link>http://mikanani.me/RSS/Search?searchstr=葬送的芙莉莲</link>
<item>
  <guid>喵萌奶茶屋 葬送的芙莉莲 S2 38</guid>
  <link>https://mikanani.me/Home/Episode/test123</link>
  <title>[喵萌奶茶屋] 葬送的芙莉莲 第二季 / Sousou no Frieren S2 [38][1080P][繁日双语]</title>
  <torrent xmlns="https://mikanani.me/0.1/">
    <link>https://mikanani.me/Home/Episode/test123</link>
    <contentLength>912250624</contentLength>
    <pubDate>2026-03-28T18:31:02.774</pubDate>
  </torrent>
  <enclosure type="application/x-bittorrent" length="912250624" url="https://mikanani.me/Download/test.torrent"/>
</item>
<item>
  <guid>北宇治字幕组 葬送的芙莉莲 S2 37</guid>
  <link>https://mikanani.me/Home/Episode/test456</link>
  <title>[北宇治字幕组] 葬送的芙莉莲 第二季 / Sousou no Frieren S2 [37][1080p][简体内嵌]</title>
  <torrent xmlns="https://mikanani.me/0.1/">
    <link>https://mikanani.me/Home/Episode/test456</link>
    <contentLength>711511232</contentLength>
    <pubDate>2026-03-24T10:39:40.492</pubDate>
  </torrent>
  <enclosure type="application/x-bittorrent" length="711511232" url="https://mikanani.me/Download/test2.torrent"/>
</item>
</channel></rss>"""

# Malformed / empty XML
EMPTY_RSS_XML = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
<title>No Results</title>
<link>http://mikanani.me/RSS/Search?searchstr=nonexistent</link>
</channel></rss>"""

INVALID_XML = "this is not xml at all"


class TestParseRss:
    """Parse Mikan RSS XML into TorrentItem list."""

    def test_parse_two_items(self):
        """Correctly parses the sample RSS with 2 items."""
        items = mikan_cli._parse_rss(SAMPLE_RSS_XML)
        assert len(items) == 2

        # First item
        assert items[0].title == (
            "[喵萌奶茶屋] 葬送的芙莉莲 第二季 / Sousou no Frieren S2 [38][1080P][繁日双语]"
        )
        assert items[0].link == "https://mikanani.me/Home/Episode/test123"
        assert items[0].enclosure_url == "https://mikanani.me/Download/test.torrent"
        assert items[0].content_length == 912250624
        assert items[0].pub_date == "2026-03-28T18:31:02.774"
        # Parsed metadata
        assert items[0].group_name == "喵萌奶茶屋"
        assert items[0].episode == "38"
        assert items[0].quality == "1080P"

        # Second item
        assert items[1].group_name == "北宇治字幕组"
        assert items[1].episode == "37"
        assert items[1].quality == "1080p"
        assert items[1].content_length == 711511232

    def test_parse_empty_rss(self):
        """RSS with no <item> elements returns an empty list."""
        items = mikan_cli._parse_rss(EMPTY_RSS_XML)
        assert items == []

    def test_parse_invalid_xml(self):
        """Malformed XML returns an empty list (does not crash)."""
        items = mikan_cli._parse_rss(INVALID_XML)
        assert items == []

    def test_parse_item_missing_fields(self):
        """Item with missing optional fields should not crash."""
        xml = """<?xml version="1.0"?><rss version="2.0"><channel>
        <item><title>Only Title</title></item>
        </channel></rss>"""
        items = mikan_cli._parse_rss(xml)
        assert len(items) == 1
        assert items[0].title == "Only Title"
        assert items[0].link == ""
        assert items[0].enclosure_url == ""
        assert items[0].content_length == 0
        assert items[0].pub_date == ""

    def test_parse_non_numeric_content_length(self):
        """contentLength that is not a valid integer defaults to 0."""
        xml = """<?xml version="1.0"?>
        <rss version="2.0"><channel>
        <item>
          <title>Test</title>
          <link>https://example.com</link>
          <torrent xmlns="https://mikanani.me/0.1/">
            <contentLength>not-a-number</contentLength>
          </torrent>
        </item>
        </channel></rss>"""
        items = mikan_cli._parse_rss(xml)
        assert len(items) == 1
        assert items[0].content_length == 0


# =============================================================================
#  Tests for fetch_search (with mocked network)
# =============================================================================

class TestFetchSearch:
    """fetch_search should parse RSS from a mocked HTTP response."""

    @patch("mikan_cli._fetch_url", return_value=SAMPLE_RSS_XML)
    def test_fetch_search_returns_items_and_xml(self, mock_fetch):
        """Returns (parsed items, raw XML) when the network succeeds."""
        items, raw_xml = mikan_cli.fetch_search("葬送的芙莉莲")
        assert len(items) == 2
        assert raw_xml == SAMPLE_RSS_XML
        mock_fetch.assert_called_once()

    @patch("mikan_cli._fetch_url", return_value=SAMPLE_RSS_XML)
    def test_fetch_search_with_group_id(self, mock_fetch):
        """Passes subgroupid parameter when group_id is provided."""
        items, raw_xml = mikan_cli.fetch_search("葬送的芙莉莲", group_id="583")
        assert len(items) == 2
        # The URL should contain subgroupid=583
        call_url = mock_fetch.call_args[0][0]
        assert "subgroupid=583" in call_url

    @patch("mikan_cli._fetch_url", return_value=SAMPLE_RSS_XML)
    def test_fetch_search_page_param(self, mock_fetch):
        """Passes page parameter correctly."""
        mikan_cli.fetch_search("Frieren", page=2)
        call_url = mock_fetch.call_args[0][0]
        assert "page=2" in call_url

    @patch("mikan_cli._fetch_url", return_value=EMPTY_RSS_XML)
    def test_fetch_search_empty_results(self, mock_fetch):
        """Returns empty list when RSS has no items."""
        items, raw_xml = mikan_cli.fetch_search("nonexistent")
        assert items == []
        assert raw_xml == EMPTY_RSS_XML


# =============================================================================
#  Tests for fetch_bangumi
# =============================================================================

class TestFetchBangumi:
    """fetch_bangumi should parse RSS from a mocked HTTP response."""

    @patch("mikan_cli._fetch_url", return_value=SAMPLE_RSS_XML)
    def test_fetch_bangumi_basic(self, mock_fetch):
        """Parses items from bangumi RSS."""
        items = mikan_cli.fetch_bangumi("12345")
        assert len(items) == 2
        call_url = mock_fetch.call_args[0][0]
        assert "bangumiId=12345" in call_url

    @patch("mikan_cli._fetch_url", return_value=SAMPLE_RSS_XML)
    def test_fetch_bangumi_with_group(self, mock_fetch):
        """Passes subgroupid when group_id is provided."""
        mikan_cli.fetch_bangumi("12345", group_id="200")
        call_url = mock_fetch.call_args[0][0]
        assert "subgroupid=200" in call_url


# =============================================================================
#  Tests for _fetch_url
# =============================================================================

class TestFetchUrl:
    """Test the raw HTTP fetch function with mocked urllib."""

    @patch("mikan_cli.urllib.request.urlopen")
    def test_fetch_url_success(self, mock_urlopen):
        """Returns decoded UTF-8 text on success."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"<rss><item>data</item></rss>"
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        result = mikan_cli._fetch_url("https://example.com/rss")
        assert result == "<rss><item>data</item></rss>"

    @patch("mikan_cli.urllib.request.urlopen")
    def test_fetch_url_unicode_decode_fallback(self, mock_urlopen):
        """Falls back to UTF-8 with replace on decode error."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"\xff\xfe<\xff>"
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        result = mikan_cli._fetch_url("https://example.com/rss")
        # Should not crash; replace errors mode is used
        assert isinstance(result, str)


# =============================================================================
#  Tests for subscription management
# =============================================================================

class TestSubscriptions:
    """Load / save / add / list / remove subscriptions."""

    def test_load_subscriptions_file_not_found(self):
        """Returns empty list when the JSON file does not exist."""
        with patch("mikan_cli.os.path.isfile", return_value=False):
            subs = mikan_cli._load_subscriptions()
        assert subs == []

    def test_load_subscriptions_valid(self):
        """Loads and deserializes valid subscription JSON."""
        data = json.dumps([
            {"name": "葬送的芙莉莲", "group_id": "583",
             "group_name": "喵萌奶茶屋", "added_date": "2026-01-01"},
        ])
        with patch("mikan_cli.os.path.isfile", return_value=True):
            with patch("builtins.open", mock_open(read_data=data)):
                subs = mikan_cli._load_subscriptions()
        assert len(subs) == 1
        assert subs[0].name == "葬送的芙莉莲"
        assert subs[0].group_id == "583"
        assert subs[0].group_name == "喵萌奶茶屋"
        assert subs[0].added_date == "2026-01-01"

    def test_load_subscriptions_invalid_json(self):
        """Malformed JSON returns an empty list."""
        with patch("mikan_cli.os.path.isfile", return_value=True):
            with patch("builtins.open", mock_open(read_data="not json")):
                subs = mikan_cli._load_subscriptions()
        assert subs == []

    def test_load_subscriptions_not_a_list(self):
        """JSON that is not a list returns an empty list."""
        with patch("mikan_cli.os.path.isfile", return_value=True):
            with patch("builtins.open", mock_open(read_data="{}")):
                subs = mikan_cli._load_subscriptions()
        assert subs == []

    def test_save_subscriptions(self):
        """Writes subscriptions to the JSON file."""
        subs = [mikan_cli.Subscription(
            name="Test", group_id="1", group_name="Group",
            added_date="2026-01-01",
        )]
        m = mock_open()
        with patch("builtins.open", m):
            mikan_cli._save_subscriptions(subs)
        # Verify the file was written
        handle = m()
        written = "".join(call[0][0] for call in handle.write.call_args_list)
        assert "Test" in written
        assert "Group" in written

    @patch("mikan_cli._load_subscriptions")
    @patch("mikan_cli._save_subscriptions")
    def test_cmd_subscribe_add_new(self, mock_save, mock_load):
        """Adding a new subscription saves it."""
        mock_load.return_value = []
        args = argparse.Namespace(name="芙莉莲", group_id=583,
                                  group_name="喵萌奶茶屋")
        mikan_cli.cmd_subscribe_add(args)
        mock_save.assert_called_once()
        saved = mock_save.call_args[0][0]
        assert len(saved) == 1
        assert saved[0].name == "芙莉莲"

    @patch("mikan_cli._load_subscriptions")
    @patch("mikan_cli._save_subscriptions")
    def test_cmd_subscribe_add_duplicate(self, mock_save, mock_load):
        """Adding a duplicate subscription does not save."""
        mock_load.return_value = [
            mikan_cli.Subscription(name="芙莉莲", group_id="583",
                                   group_name="喵萌奶茶屋", added_date=""),
        ]
        args = argparse.Namespace(name="芙莉莲", group_id=583,
                                  group_name="喵萌奶茶屋")
        mikan_cli.cmd_subscribe_add(args)
        mock_save.assert_not_called()

    @patch("mikan_cli._load_subscriptions")
    @patch("mikan_cli._save_subscriptions")
    def test_cmd_subscribe_remove_existing(self, mock_save, mock_load):
        """Removing an existing subscription saves the updated list."""
        mock_load.return_value = [
            mikan_cli.Subscription(name="芙莉莲", group_id="583",
                                   group_name="喵萌奶茶屋", added_date=""),
        ]
        args = argparse.Namespace(name="芙莉莲")
        mikan_cli.cmd_subscribe_remove(args)
        mock_save.assert_called_once()
        assert mock_save.call_args[0][0] == []

    @patch("mikan_cli._load_subscriptions")
    @patch("mikan_cli._save_subscriptions")
    def test_cmd_subscribe_remove_not_found(self, mock_save, mock_load):
        """Removing a non-existent subscription does nothing."""
        mock_load.return_value = []
        args = argparse.Namespace(name="芙莉莲")
        mikan_cli.cmd_subscribe_remove(args)
        mock_save.assert_not_called()

    @patch("mikan_cli._load_subscriptions")
    def test_cmd_subscribe_list_empty(self, mock_load, capsys):
        """Listing subscriptions when empty shows a helpful message."""
        mock_load.return_value = []
        args = argparse.Namespace()
        mikan_cli.cmd_subscribe_list(args)
        captured = capsys.readouterr()
        assert "No subscriptions yet" in captured.out

    @patch("mikan_cli._load_subscriptions")
    def test_cmd_subscribe_list_with_items(self, mock_load, capsys):
        """Listing subscriptions shows the subscribed items."""
        mock_load.return_value = [
            mikan_cli.Subscription(name="芙莉莲", group_id="583",
                                   group_name="喵萌奶茶屋",
                                   added_date="2026-01-01"),
        ]
        args = argparse.Namespace()
        mikan_cli.cmd_subscribe_list(args)
        captured = capsys.readouterr()
        assert "芙莉莲" in captured.out
        assert "喵萌奶茶屋" in captured.out


# =============================================================================
#  Tests for _fmt_date
# =============================================================================

class TestFmtDate:
    """Format RSS date strings to YYYY-MM-DD."""

    def test_standard_rfc822_date(self):
        result = mikan_cli._fmt_date("Sun, 28 Jan 2024 12:00:00 +0900")
        assert result == "2024-01-28"

    def test_date_without_timezone(self):
        result = mikan_cli._fmt_date("28 Jan 2024 12:00:00")
        assert result == "2024-01-28"

    def test_empty_date(self):
        assert mikan_cli._fmt_date("") == ""

    def test_mikan_iso_format(self):
        """Mikan's ISO-like date (without timezone) is handled."""
        result = mikan_cli._fmt_date("2026-03-28T18:31:02")
        # Falls through to the fallback [:10] slice
        assert result == "2026-03-28"

    def test_unparseable_date(self):
        """Fallback returns first 10 chars."""
        result = mikan_cli._fmt_date("not-a-date-string")
        assert result == "not-a-date"  # [:10]


# =============================================================================
#  Tests for _print_table
# =============================================================================

class TestPrintTable:
    """Display helper for torrent items."""

    def test_print_table_empty(self, capsys):
        """Empty item list shows a no-results message."""
        mikan_cli._print_table([])
        captured = capsys.readouterr()
        assert "(no results)" in captured.out

    def test_print_table_with_items(self, capsys):
        """Table is printed for valid items (smoke test)."""
        items = [
            _make_item(
                title="[Group] Test - 01",
                link="https://example.com/1",
                enclosure_url="https://example.com/1.torrent",
                group_name="Group",
                episode="01",
                quality="1080P",
                content_length=1_048_576,
            ),
        ]
        mikan_cli._print_table(items)
        captured = capsys.readouterr()
        assert "Group" in captured.out
        assert "01" in captured.out
        assert "1.00 MB" in captured.out


# =============================================================================
#  Tests for CLI dispatch (main / build_parser)
# =============================================================================

class TestCliParser:
    """Verify argument parsing for each subcommand."""

    def test_build_parser_search(self):
        parser = mikan_cli.build_parser()
        args = parser.parse_args(["search", "葬送的芙莉莲"])
        assert args.command == "search"
        assert args.keyword == "葬送的芙莉莲"
        assert args.page == 1
        assert args.group_id is None
        assert args.limit is None

    def test_build_parser_search_with_options(self):
        parser = mikan_cli.build_parser()
        args = parser.parse_args(
            ["search", "芙莉莲", "--page", "2", "--group-id", "583", "--limit", "5"]
        )
        assert args.page == 2
        assert args.group_id == "583"
        assert args.limit == 5

    def test_build_parser_list_groups(self):
        parser = mikan_cli.build_parser()
        args = parser.parse_args(["list-groups", "芙莉莲"])
        assert args.command == "list-groups"
        assert args.keyword == "芙莉莲"

    def test_build_parser_export(self):
        parser = mikan_cli.build_parser()
        args = parser.parse_args(["export", "芙莉莲", "--output", "out.txt"])
        assert args.command == "export"
        assert args.output == "out.txt"

    def test_build_parser_download(self):
        parser = mikan_cli.build_parser()
        args = parser.parse_args(
            ["download", "芙莉莲", "--group-id", "583", "--episode", "10",
             "--dir", "./dl"]
        )
        assert args.command == "download"
        assert args.episode == 10
        assert args.dir == "./dl"

    def test_build_parser_subscribe_add(self):
        parser = mikan_cli.build_parser()
        args = parser.parse_args(
            ["subscribe", "add", "芙莉莲", "--group-id", "583",
             "--group-name", "喵萌奶茶屋"]
        )
        assert args.command == "subscribe"
        assert args.action == "add"
        assert args.name == "芙莉莲"

    def test_build_parser_subscribe_list(self):
        parser = mikan_cli.build_parser()
        args = parser.parse_args(["subscribe", "list"])
        assert args.command == "subscribe"
        assert args.action == "list"

    def test_build_parser_subscribe_remove(self):
        parser = mikan_cli.build_parser()
        args = parser.parse_args(["subscribe", "remove", "芙莉莲"])
        assert args.command == "subscribe"
        assert args.action == "remove"
        assert args.name == "芙莉莲"

    def test_build_parser_check(self):
        parser = mikan_cli.build_parser()
        args = parser.parse_args(["check"])
        assert args.command == "check"


# =============================================================================
#  Tests for cmd_search with mocked fetch_search
# =============================================================================

class TestCmdSearch:
    """Search subcommand with mocked network."""

    @patch("mikan_cli.fetch_search")
    def test_cmd_search_no_results(self, mock_fetch, capsys):
        """Shows 'No results found' when empty."""
        mock_fetch.return_value = ([], SAMPLE_RSS_XML)
        args = argparse.Namespace(keyword="nonexistent", page=1,
                                  group_id=None, limit=None)
        mikan_cli.cmd_search(args)
        captured = capsys.readouterr()
        assert "No results for" in captured.out

    @patch("mikan_cli.fetch_search")
    def test_cmd_search_with_results(self, mock_fetch, capsys):
        """Shows a table of results."""
        items = [
            _make_item(
                title="[Group] Test - 01",
                link="https://example.com",
                enclosure_url="https://example.com/t.torrent",
                group_name="Group",
                episode="01", quality="1080P", content_length=1_048_576,
            ),
        ]
        mock_fetch.return_value = (items, SAMPLE_RSS_XML)
        args = argparse.Namespace(keyword="芙莉莲", page=1,
                                  group_id=None, limit=None)
        mikan_cli.cmd_search(args)
        captured = capsys.readouterr()
        assert "Group" in captured.out
        assert "01" in captured.out


# =============================================================================
#  Tests for cmd_list_groups with mocked fetch_search
# =============================================================================

class TestCmdListGroups:
    """List-groups subcommand."""

    @patch("mikan_cli.fetch_search")
    def test_cmd_list_groups_no_results(self, mock_fetch, capsys):
        """Shows 'No results found' when empty."""
        mock_fetch.return_value = ([], "")
        args = argparse.Namespace(keyword="nonexistent")
        mikan_cli.cmd_list_groups(args)
        captured = capsys.readouterr()
        assert "No results found" in captured.out

    @patch("mikan_cli.fetch_search")
    def test_cmd_list_groups_with_items(self, mock_fetch, capsys):
        """Shows group names and counts."""
        items = [
            _make_item(title="[A] X - 01", link="", enclosure_url="",
                       group_name="A"),
            _make_item(title="[A] X - 02", link="", enclosure_url="",
                       group_name="A"),
            _make_item(title="[B] Y - 01", link="", enclosure_url="",
                       group_name="B"),
        ]
        mock_fetch.return_value = (items, "")
        args = argparse.Namespace(keyword="test")
        mikan_cli.cmd_list_groups(args)
        captured = capsys.readouterr()
        assert "A" in captured.out
        assert "B" in captured.out


# =============================================================================
#  Tests for cmd_export with mocked fetch_search
# =============================================================================

class TestCmdExport:
    """Export subcommand."""

    @patch("mikan_cli.fetch_search")
    def test_cmd_export_no_results(self, mock_fetch, capsys):
        """Shows message when no results."""
        mock_fetch.return_value = ([], "")
        args = argparse.Namespace(keyword="nonexistent", page=1,
                                  group_id=None, output=None)
        mikan_cli.cmd_export(args)
        captured = capsys.readouterr()
        assert "Nothing to export" in captured.out

    @patch("mikan_cli.fetch_search")
    @patch("builtins.open", new_callable=mock_open)
    def test_cmd_export_with_output(self, mock_file, mock_fetch, capsys):
        """Writes results to the specified output file."""
        items = [
            _make_item(
                title="[A] X - 01",
                link="https://example.com",
                enclosure_url="https://dl.example.com/test.torrent",
                group_name="A", episode="01",
                quality="1080P", content_length=1_048_576,
            ),
        ]
        mock_fetch.return_value = (items, "")
        args = argparse.Namespace(keyword="test", page=1, group_id=None,
                                  output="out.txt")
        mikan_cli.cmd_export(args)
        captured = capsys.readouterr()
        assert "Exported" in captured.out

    @patch("mikan_cli.fetch_search")
    @patch("builtins.open", new_callable=mock_open)
    def test_cmd_export_auto_filename(self, mock_file, mock_fetch, capsys):
        """Auto-generates filename when output is not specified."""
        items = [
            _make_item(
                title="[A] X - 01",
                link="https://example.com",
                enclosure_url="https://dl.example.com/test.torrent",
                group_name="A", episode="01",
                quality="1080P", content_length=1_048_576,
            ),
        ]
        mock_fetch.return_value = (items, "")
        args = argparse.Namespace(keyword="test", page=1, group_id=None,
                                  output=None)
        mikan_cli.cmd_export(args)
        captured = capsys.readouterr()
        assert "Exported" in captured.out


# =============================================================================
#  Tests for TorrentItem dataclass
# =============================================================================

class TestTorrentItem:
    """TorrentItem dataclass basic behavior."""

    def test_default_values(self):
        item = mikan_cli.TorrentItem(
            title="t", link="l", enclosure_url="e"
        )
        assert item.title == "t"
        assert item.link == "l"
        assert item.enclosure_url == "e"
        assert item.content_length == 0
        assert item.pub_date == ""
        assert item.group_name == ""
        assert item.episode == ""
        assert item.quality == ""
        assert item.anime_name == ""


# =============================================================================
#  Tests for cmd_check with mocked fetch_search
# =============================================================================

class TestCmdCheck:
    """Check subcommand."""

    @patch("mikan_cli._load_subscriptions")
    def test_cmd_check_no_subscriptions(self, mock_load, capsys):
        """Shows message when there are no subscriptions."""
        mock_load.return_value = []
        args = argparse.Namespace()
        mikan_cli.cmd_check(args)
        captured = capsys.readouterr()
        assert "No subscriptions to check" in captured.out

    @patch("mikan_cli._load_subscriptions")
    @patch("mikan_cli.fetch_search")
    def test_cmd_check_with_items(self, mock_fetch, mock_load, capsys):
        """Shows latest episode info for a subscription."""
        mock_load.return_value = [
            mikan_cli.Subscription(name="芙莉莲", group_id="583",
                                   group_name="喵萌奶茶屋", added_date=""),
        ]
        items = [
            _make_item(
                title="[喵萌奶茶屋] 芙莉莲 [38][1080P]",
                link="https://example.com",
                enclosure_url="https://dl.example.com/test.torrent",
                group_name="喵萌奶茶屋", episode="38", quality="1080P",
                content_length=1_048_576,
            ),
        ]
        mock_fetch.return_value = (items, "")
        args = argparse.Namespace()
        mikan_cli.cmd_check(args)
        captured = capsys.readouterr()
        assert "芙莉莲" in captured.out
        assert "38" in captured.out
        assert "1080P" in captured.out
