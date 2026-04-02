#!/usr/bin/env python3
"""
Test suite for MU Advisor Telegram Alert System.

Tests all modules with mocked external dependencies (Telegram API, DeepSeek API, yfinance).
Run: conda run -n mu_advisor python3 /home/wayne/website/mu_advisor/alerts/tests.py
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, date, timedelta
from unittest.mock import patch, MagicMock

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Shared test POSITION with real values (used to patch modules when POSITION is None)
TEST_POSITION = {
    "ticker": "MU",
    "long_strike": 380,
    "short_strike": 400,
    "contracts": 500,
    "entry_price": 11.897,
    "expiry": "2026-03-20",
    "breakeven": 391.897,
}


# ============================================================================
# Test Config
# ============================================================================
class TestConfig(unittest.TestCase):
    """Test configuration values are properly set."""

    def test_config_imports(self):
        from alerts.config import (
            TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, DEEPSEEK_API_KEY,
            POSITION, PRICE_LEVELS, VIX_LEVELS, FUTURES, PEERS,
            TZ_ET, TZ_SGT, CATALYSTS,
        )
        self.assertTrue(TELEGRAM_BOT_TOKEN)
        self.assertTrue(TELEGRAM_CHAT_ID)
        self.assertTrue(DEEPSEEK_API_KEY)
        self.assertEqual(POSITION["ticker"], "MU")
        # Position fields must exist (values change per trade)
        self.assertIn("long_strike", POSITION)
        self.assertIn("short_strike", POSITION)
        self.assertIn("contracts", POSITION)
        self.assertIn("entry_price", POSITION)
        self.assertIn("expiry", POSITION)
        self.assertIn("breakeven", POSITION)
        # Price levels should include position strikes when active
        if POSITION.get("long_strike"):
            self.assertIn(POSITION["long_strike"], PRICE_LEVELS)
        if POSITION.get("short_strike"):
            self.assertIn(POSITION["short_strike"], PRICE_LEVELS)
        self.assertIn(25, VIX_LEVELS)
        self.assertIn("ES=F", FUTURES)
        self.assertIn("NVDA", PEERS)
        self.assertEqual(TZ_ET, "US/Eastern")
        self.assertEqual(TZ_SGT, "Asia/Singapore")
        self.assertGreater(len(CATALYSTS), 0)

    def test_position_values(self):
        from alerts.config import POSITION
        # Just verify types, not specific values (change per trade)
        if POSITION.get("contracts"):
            self.assertIsInstance(POSITION["entry_price"], (int, float))
            self.assertIsInstance(POSITION["expiry"], str)
            self.assertIsInstance(POSITION["breakeven"], (int, float))


# ============================================================================
# Test Bot (Telegram sender)
# ============================================================================
class TestBot(unittest.TestCase):
    """Test Telegram message sending functions."""

    @patch("alerts.bot.requests.post")
    def test_send_alert_text(self, mock_post):
        from alerts.bot import send_alert
        mock_post.return_value = MagicMock(status_code=200)
        mock_post.return_value.raise_for_status = MagicMock()

        result = send_alert("Test message")
        self.assertTrue(result)
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        self.assertIn("sendMessage", call_args[0][0])
        self.assertEqual(call_args[1]["json"]["text"], "Test message")

    @patch("alerts.bot.requests.post")
    def test_send_alert_with_image(self, mock_post):
        from alerts.bot import send_alert
        mock_post.return_value = MagicMock(status_code=200)
        mock_post.return_value.raise_for_status = MagicMock()

        result = send_alert("Caption", image_url="https://example.com/img.jpg")
        self.assertTrue(result)
        first_call = mock_post.call_args_list[0]
        self.assertIn("sendPhoto", first_call[0][0])

    @patch("alerts.bot.requests.post")
    def test_send_alert_image_fallback(self, mock_post):
        from alerts.bot import send_alert
        # First call (photo) raises, second (text) succeeds
        photo_resp = MagicMock()
        photo_resp.raise_for_status.side_effect = Exception("photo fail")
        text_resp = MagicMock(status_code=200)
        text_resp.raise_for_status = MagicMock()
        mock_post.side_effect = [photo_resp, text_resp]

        result = send_alert("Message", image_url="https://example.com/bad.jpg")
        self.assertTrue(result)
        self.assertEqual(mock_post.call_count, 2)

    @patch("alerts.bot.requests.post")
    def test_send_alert_failure(self, mock_post):
        from alerts.bot import send_alert
        mock_post.side_effect = Exception("Network error")
        result = send_alert("Test")
        self.assertFalse(result)

    @patch("alerts.bot.send_alert")
    def test_send_price_alert_up(self, mock_send):
        from alerts.bot import send_price_alert
        mock_send.return_value = True
        send_price_alert("MU", 395.50, 395, "up")
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        self.assertIn("$395", msg)
        self.assertIn("$395.50", msg)
        # Uses green circle emoji for up
        self.assertIn("\U0001f7e2", msg)

    @patch("alerts.bot.send_alert")
    def test_send_price_alert_down(self, mock_send):
        from alerts.bot import send_price_alert
        mock_send.return_value = True
        send_price_alert("MU", 389.50, 390, "down")
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        self.assertIn("$390", msg)
        # Uses red circle emoji for down
        self.assertIn("\U0001f534", msg)

    @patch("alerts.bot.send_alert")
    def test_send_big_move_alert(self, mock_send):
        from alerts.bot import send_big_move_alert
        mock_send.return_value = True
        send_big_move_alert("MU", 405.0, 5.5, 383.88)
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        self.assertIn("+5.50%", msg)

    @patch("alerts.bot.send_alert")
    def test_send_vix_alert(self, mock_send):
        from alerts.bot import send_vix_alert
        mock_send.return_value = True
        send_vix_alert(31.5, 30, "up")
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        self.assertIn("VIX", msg)
        self.assertIn("30", msg)

    @patch("alerts.bot.send_alert")
    def test_send_spread_update(self, mock_send):
        from alerts.bot import send_spread_update
        mock_send.return_value = True
        send_spread_update(395.0, 13.50, 80150.0, 13.5)
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        self.assertIn("$395.00", msg)
        self.assertIn("$13.50", msg)

    @patch("alerts.bot.send_alert")
    def test_send_catalyst_reminder(self, mock_send):
        from alerts.bot import send_catalyst_reminder
        mock_send.return_value = True
        send_catalyst_reminder("FOMC RATE DECISION", 15)
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        self.assertIn("15 MIN", msg)
        self.assertIn("FOMC", msg)

    @patch("alerts.bot.send_alert")
    def test_send_catalyst_reminder_60min(self, mock_send):
        from alerts.bot import send_catalyst_reminder
        mock_send.return_value = True
        send_catalyst_reminder("CPI February", 60)
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        self.assertIn("1 HOUR", msg)

    def test_message_logging(self):
        from alerts.bot import _log_message, get_message_log
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            log_file = f.name
        try:
            os.unlink(log_file)
            with patch("alerts.bot.MESSAGE_LOG_FILE", log_file):
                _log_message("Test alert 1", msg_type="text", success=True)
                _log_message("Test alert 2", msg_type="photo", success=False)

            with patch("alerts.bot.MESSAGE_LOG_FILE", log_file):
                logs = get_message_log(10)
                self.assertEqual(len(logs), 2)
                self.assertEqual(logs[0]["message"], "Test alert 1")
                self.assertTrue(logs[0]["success"])
                self.assertFalse(logs[1]["success"])
                self.assertIn("timestamp", logs[0])
        finally:
            if os.path.exists(log_file):
                os.unlink(log_file)

    def test_get_message_log_empty(self):
        from alerts.bot import get_message_log
        with patch("alerts.bot.MESSAGE_LOG_FILE", "/tmp/_no_msg_log.json"):
            self.assertEqual(get_message_log(), [])

    @patch("alerts.bot.requests.post")
    def test_caption_truncation(self, mock_post):
        from alerts.bot import send_alert
        mock_post.return_value = MagicMock(status_code=200)
        mock_post.return_value.raise_for_status = MagicMock()

        long_msg = "A" * 2000
        send_alert(long_msg, image_url="https://example.com/img.jpg")
        # First call should be sendPhoto with truncated caption
        first_call = mock_post.call_args_list[0]
        payload = first_call[1]["json"]
        self.assertLessEqual(len(payload["caption"]), 1024)


# ============================================================================
# Test Price Monitor
# ============================================================================
class TestPriceMonitor(unittest.TestCase):
    """Test price monitoring functions."""

    @patch("alerts.price_monitor.POSITION", TEST_POSITION)
    def test_estimate_spread_value_below_long_strike(self):
        from alerts.price_monitor import estimate_spread_value
        with patch("alerts.price_monitor.get_spread_market_value", return_value=(None, None, None)):
            val = estimate_spread_value(TEST_POSITION["long_strike"] - 10)
            self.assertEqual(val, 0.0)

    @patch("alerts.price_monitor.POSITION", TEST_POSITION)
    def test_estimate_spread_value_above_short_strike(self):
        from alerts.price_monitor import estimate_spread_value
        width = TEST_POSITION["short_strike"] - TEST_POSITION["long_strike"]
        with patch("alerts.price_monitor.get_spread_market_value", return_value=(None, None, None)):
            val = estimate_spread_value(TEST_POSITION["short_strike"] + 20)
            self.assertEqual(val, width)

    @patch("alerts.price_monitor.POSITION", TEST_POSITION)
    def test_estimate_spread_value_between_strikes(self):
        from alerts.price_monitor import estimate_spread_value
        mid = (TEST_POSITION["long_strike"] + TEST_POSITION["short_strike"]) / 2
        expected = mid - TEST_POSITION["long_strike"]
        with patch("alerts.price_monitor.get_spread_market_value", return_value=(None, None, None)):
            val = estimate_spread_value(mid)
            self.assertEqual(val, expected)

    @patch("alerts.price_monitor.POSITION", TEST_POSITION)
    def test_estimate_spread_value_uses_market_data(self):
        from alerts.price_monitor import estimate_spread_value
        with patch("alerts.price_monitor.get_spread_market_value", return_value=(12.50, 0.5, 0.5)), \
             patch("alerts.price_monitor.is_market_hours", return_value=True):
            val = estimate_spread_value(395.0)
            self.assertEqual(val, 12.50)

    def test_reset_daily_state(self):
        from alerts.price_monitor import reset_daily_state
        state = {"some": "old"}
        reset_daily_state(state, "2026-03-06", 385.0)
        self.assertEqual(state["date"], "2026-03-06")
        self.assertEqual(state["prev_close"], 385.0)
        self.assertEqual(state["alerted_price_levels"], [])
        self.assertFalse(state["alerted_big_move"])

    def test_is_market_hours_returns_bool(self):
        from alerts.price_monitor import is_market_hours
        self.assertIsInstance(is_market_hours(), bool)

    def test_is_extended_hours_returns_bool(self):
        from alerts.price_monitor import is_extended_hours
        self.assertIsInstance(is_extended_hours(), bool)

    def test_load_state_missing_file(self):
        from alerts.price_monitor import load_state
        with patch("alerts.price_monitor.STATE_FILE", "/tmp/_nonexistent_pm_state.json"):
            state = load_state()
            self.assertIsNone(state["last_mu_price"])
            self.assertIsNone(state["date"])

    @patch("alerts.price_monitor.save_state")
    @patch("alerts.price_monitor.load_state")
    @patch("alerts.price_monitor.get_live_price")
    @patch("alerts.price_monitor.get_prev_close")
    @patch("alerts.price_monitor.send_price_alert")
    @patch("alerts.price_monitor.send_big_move_alert")
    @patch("alerts.price_monitor.send_vix_alert")
    @patch("alerts.price_monitor.send_spread_update")
    @patch("alerts.price_monitor.send_alert")
    @patch("alerts.price_monitor.estimate_spread_value")
    def test_check_prices_level_crossing(
        self, mock_est, mock_alert, mock_spread, mock_vix,
        mock_big, mock_price_alert, mock_prev, mock_live, mock_load, mock_save
    ):
        from alerts.price_monitor import check_prices
        mock_load.return_value = {
            "last_mu_price": 389.0,
            "last_vix": 20.0,
            "prev_close": 388.0,
            "alerted_price_levels": [],
            "alerted_vix_levels": [],
            "alerted_big_move": False,
            "alerted_peers": [],
            "alerted_futures": [],
            "last_spread_alert_level": 0,
            "date": datetime.now().strftime("%Y-%m-%d"),
        }
        mock_live.side_effect = lambda t: {
            "MU": 391.0, "^VIX": 20.0,
            "NVDA": 800, "AMD": 150, "AVGO": 200, "MRVL": 90, "SOXX": 600,
            "ES=F": 5800, "NQ=F": 20000, "YM=F": 43000,
        }.get(t)
        mock_prev.side_effect = lambda t: {
            "NVDA": 800, "AMD": 150, "AVGO": 200, "MRVL": 90, "SOXX": 600,
            "ES=F": 5800, "NQ=F": 20000, "YM=F": 43000,
        }.get(t)
        mock_est.return_value = 11.0

        check_prices()
        # Should have alerted on crossing 390
        mock_price_alert.assert_called_once_with("MU", 391.0, 390, "up")
        mock_save.assert_called_once()


# ============================================================================
# Test News Scanner
# ============================================================================
class TestNewsScanner(unittest.TestCase):
    """Test RSS news scanning and dedup."""

    def test_normalize_title(self):
        from alerts.news_scanner import normalize_title
        t1 = normalize_title("Micron Beats Estimates - Reuters")
        t2 = normalize_title("Micron beats estimates | Yahoo Finance")
        self.assertEqual(t1, t2)

    def test_normalize_title_strips_punctuation(self):
        from alerts.news_scanner import normalize_title
        t = normalize_title("Micron's HBM3e chips! Ready?")
        self.assertNotIn("!", t)
        self.assertNotIn("?", t)

    def test_hash_headline(self):
        from alerts.news_scanner import hash_headline
        h1 = hash_headline("Micron beats Q2 estimates")
        h2 = hash_headline("MICRON BEATS Q2 ESTIMATES!")
        self.assertEqual(h1, h2)

    def test_hash_url_strips_tracking(self):
        from alerts.news_scanner import hash_url
        h1 = hash_url("https://example.com/article?utm_source=twitter")
        h2 = hash_url("https://example.com/article")
        self.assertEqual(h1, h2)

    def test_hash_url_empty(self):
        from alerts.news_scanner import hash_url
        self.assertEqual(hash_url(""), "")

    def test_classify_headline_high(self):
        from alerts.news_scanner import classify_headline
        self.assertEqual(classify_headline("Micron earnings beat expectations"), "high")
        self.assertEqual(classify_headline("FOMC holds rates steady"), "high")
        self.assertEqual(classify_headline("HBM demand surges"), "high")
        self.assertEqual(classify_headline("DeepSeek releases new model"), "high")
        self.assertEqual(classify_headline("Memory demand surges on AI"), "high")
        self.assertEqual(classify_headline("China AI chip restrictions tighten"), "high")

    def test_classify_headline_normal(self):
        from alerts.news_scanner import classify_headline
        self.assertEqual(classify_headline("NVIDIA stock surges on earnings beat"), "normal")
        self.assertEqual(classify_headline("Blackwell GPU demand exceeds supply"), "normal")
        self.assertEqual(classify_headline("Semiconductor sector rallies"), "normal")
        # DRAM/HBM are high priority (directly impact MU)
        self.assertEqual(classify_headline("DRAM prices rise 10%"), "high")
        self.assertEqual(classify_headline("SK Hynix ramps HBM production"), "high")

    def test_classify_headline_none(self):
        from alerts.news_scanner import classify_headline
        self.assertIsNone(classify_headline("Apple launches new iPhone"))
        self.assertIsNone(classify_headline("Tesla stock drops 5%"))
        # Generic news should NOT match
        self.assertIsNone(classify_headline("Iran strikes escalate"))
        self.assertIsNone(classify_headline("Baidu launches AI assistant"))
        self.assertIsNone(classify_headline("AMD launches new GPU"))

    @patch("alerts.news_scanner.requests.get")
    def test_fetch_rss_success(self, mock_get):
        from alerts.news_scanner import fetch_rss
        xml_content = """<?xml version="1.0"?>
        <rss><channel>
            <item>
                <title>Micron beats estimates</title>
                <link>https://example.com/article1</link>
            </item>
            <item>
                <title>HBM demand surges</title>
                <link>https://example.com/article2</link>
            </item>
        </channel></rss>"""
        mock_get.return_value = MagicMock(
            status_code=200, content=xml_content.encode(),
            raise_for_status=MagicMock()
        )
        items = fetch_rss("https://example.com/rss", "Test")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["title"], "Micron beats estimates")
        self.assertEqual(items[0]["source"], "Test")

    @patch("alerts.news_scanner.requests.get")
    def test_fetch_rss_failure(self, mock_get):
        from alerts.news_scanner import fetch_rss
        mock_get.side_effect = Exception("timeout")
        items = fetch_rss("https://bad.url", "Test")
        self.assertEqual(items, [])

    def test_first_run_seeding(self):
        from alerts.news_scanner import check_news
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            state_file = f.name

        try:
            os.unlink(state_file)
            with patch("alerts.news_scanner.STATE_FILE", state_file), \
                 patch("alerts.news_scanner.fetch_rss") as mock_fetch, \
                 patch("alerts.news_scanner.send_alert") as mock_send:
                mock_fetch.return_value = [
                    {"title": "Old news 1", "link": "https://a.com/1", "source": "Test"},
                    {"title": "Old news 2", "link": "https://a.com/2", "source": "Test"},
                ]
                check_news()
                mock_send.assert_not_called()
                with open(state_file) as sf:
                    state = json.load(sf)
                self.assertTrue(state.get("seeded"))
        finally:
            if os.path.exists(state_file):
                os.unlink(state_file)

    def test_get_recent_headlines_empty(self):
        from alerts.news_scanner import get_recent_headlines
        with patch("alerts.news_scanner.HEADLINES_LOG", "/tmp/_nonexistent_log.json"):
            result = get_recent_headlines()
            self.assertEqual(result, [])


# ============================================================================
# Test Summarizer
# ============================================================================
class TestSummarizer(unittest.TestCase):
    """Test DeepSeek article summarization."""

    @patch("alerts.summarizer.requests.get")
    def test_fetch_article_success(self, mock_get):
        from alerts.summarizer import fetch_article
        html = """<html><head>
        <meta property="og:image" content="https://img.com/photo.jpg"/>
        </head><body>
        <p>Micron Technology reported strong earnings beating analyst expectations with record HBM revenue.</p>
        </body></html>"""
        mock_get.return_value = MagicMock(
            text=html, status_code=200, raise_for_status=MagicMock()
        )
        result = fetch_article("https://example.com/article")
        self.assertIsNotNone(result["text"])
        self.assertIn("Micron", result["text"])
        self.assertEqual(result["image"], "https://img.com/photo.jpg")

    def test_fetch_article_empty_url(self):
        from alerts.summarizer import fetch_article
        result = fetch_article("")
        self.assertIsNone(result["text"])
        self.assertIsNone(result["image"])

    @patch("alerts.summarizer.requests.get")
    def test_fetch_article_failure(self, mock_get):
        from alerts.summarizer import fetch_article
        mock_get.side_effect = Exception("Connection error")
        result = fetch_article("https://bad.url")
        self.assertIsNone(result["text"])

    @patch("alerts.summarizer.requests.post")
    def test_summarize_article_success(self, mock_post):
        from alerts.summarizer import summarize_article
        mock_post.return_value = MagicMock(
            status_code=200,
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={
                "choices": [{"message": {"content":
                    '{"summary": "Micron beat Q2 estimates.", "sentiment": "BULLISH", "relevance": "HIGH"}'
                }}]
            })
        )
        result = summarize_article("Micron beats Q2", "Full article text")
        self.assertEqual(result["sentiment"], "BULLISH")
        self.assertEqual(result["relevance"], "HIGH")
        self.assertIn("Micron", result["summary"])

    @patch("alerts.summarizer.requests.post")
    def test_summarize_article_failure(self, mock_post):
        from alerts.summarizer import summarize_article
        mock_post.side_effect = Exception("API error")
        result = summarize_article("Test headline", None)
        self.assertEqual(result["sentiment"], "NEUTRAL")
        self.assertEqual(result["relevance"], "LOW")

    @patch("alerts.summarizer.requests.post")
    def test_summarize_article_markdown_json(self, mock_post):
        from alerts.summarizer import summarize_article
        mock_post.return_value = MagicMock(
            status_code=200,
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={
                "choices": [{"message": {"content":
                    '```json\n{"summary": "Test.", "sentiment": "BEARISH", "relevance": "MED"}\n```'
                }}]
            })
        )
        result = summarize_article("Test", "text")
        self.assertEqual(result["sentiment"], "BEARISH")

    @patch("alerts.summarizer.requests.post")
    def test_batch_sentiment(self, mock_post):
        from alerts.summarizer import batch_sentiment
        mock_post.return_value = MagicMock(
            status_code=200,
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={
                "choices": [{"message": {"content":
                    '[{"summary": "s1", "sentiment": "BULLISH", "relevance": "HIGH"},'
                    '{"summary": "s2", "sentiment": "NEUTRAL", "relevance": "LOW"}]'
                }}]
            })
        )
        headlines = [
            {"title": "H1", "source": "S1", "link": ""},
            {"title": "H2", "source": "S2", "link": ""},
        ]
        result = batch_sentiment(headlines)
        self.assertEqual(result[0]["sentiment"], "BULLISH")
        self.assertEqual(result[1]["sentiment"], "NEUTRAL")

    def test_batch_sentiment_empty(self):
        from alerts.summarizer import batch_sentiment
        result = batch_sentiment([])
        self.assertEqual(result, [])


# ============================================================================
# Test Catalyst Scheduler
# ============================================================================
class TestCatalystScheduler(unittest.TestCase):
    """Test catalyst reminder timing."""

    def test_load_state_missing(self):
        from alerts.catalyst_scheduler import load_state
        with patch("alerts.catalyst_scheduler.STATE_FILE", "/tmp/_no_catalyst.json"):
            state = load_state()
            self.assertEqual(state, {"alerted": []})

    @patch("alerts.catalyst_scheduler.send_catalyst_reminder")
    @patch("alerts.catalyst_scheduler.save_state")
    @patch("alerts.catalyst_scheduler.load_state")
    def test_check_catalysts_runs(self, mock_load, mock_save, mock_send):
        from alerts.catalyst_scheduler import check_catalysts
        mock_load.return_value = {"alerted": []}
        check_catalysts()
        mock_save.assert_called_once()


# ============================================================================
# Test Smart Alerts
# ============================================================================
class TestSmartAlerts(unittest.TestCase):
    """Test smart level crossing analysis."""

    @patch("alerts.smart_alerts.requests.post")
    @patch("alerts.price_monitor.get_live_price")
    @patch("alerts.price_monitor.get_prev_close")
    def test_analyze_level_cross(self, mock_prev, mock_live, mock_post):
        from alerts.smart_alerts import analyze_level_cross
        mock_live.side_effect = lambda t: {"^VIX": 22.0, "NVDA": 800, "AMD": 150,
                                            "AVGO": 200, "MRVL": 90,
                                            "ES=F": 5800, "NQ=F": 20000, "YM=F": 43000}.get(t)
        mock_prev.side_effect = lambda t: {"NVDA": 795, "AMD": 148, "AVGO": 198,
                                            "MRVL": 89, "ES=F": 5780, "NQ=F": 19900, "YM=F": 42900}.get(t)
        mock_post.return_value = MagicMock(
            status_code=200,
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={
                "choices": [{"message": {"content": "Sector-wide rally driven by AI demand."}}]
            })
        )

        result = analyze_level_cross("MU", 391.0, 390, "up")
        self.assertIn("rally", result.lower())
        mock_post.assert_called_once()

    @patch("alerts.smart_alerts.requests.post")
    @patch("alerts.price_monitor.get_live_price")
    @patch("alerts.price_monitor.get_prev_close")
    def test_analyze_level_cross_api_fail(self, mock_prev, mock_live, mock_post):
        from alerts.smart_alerts import analyze_level_cross
        mock_live.return_value = None
        mock_prev.return_value = None
        mock_post.side_effect = Exception("API error")

        result = analyze_level_cross("MU", 385.0, 385, "down")
        self.assertEqual(result, "Analysis unavailable.")


# ============================================================================
# Test Options Flow
# ============================================================================
class TestOptionsFlow(unittest.TestCase):
    """Test options flow detection (rewritten module)."""

    @patch("alerts.options_flow.POSITION", TEST_POSITION)
    def test_scan_unusual_activity(self):
        from alerts.options_flow import _scan_unusual_activity
        import pandas as pd

        ls = TEST_POSITION["long_strike"]
        ss = TEST_POSITION["short_strike"]
        mid = (ls + ss) / 2
        expiry = TEST_POSITION["expiry"]

        calls_data = pd.DataFrame({
            "strike": [ls, mid, ss],
            "volume": [500, 50, 1000],
            "openInterest": [100, 500, 200],
            "impliedVolatility": [0.45, 0.40, 0.50],
            "lastPrice": [15.0, 8.0, 3.0],
        })
        puts_data = pd.DataFrame({
            "strike": [ls, mid],
            "volume": [20, 10],
            "openInterest": [100, 100],
            "impliedVolatility": [0.40, 0.35],
            "lastPrice": [2.0, 5.0],
        })

        chains = {expiry: {"calls": calls_data, "puts": puts_data}}
        state = {"prev_oi": {}, "prev_pcr": None, "prev_iv": {}}

        unusual, new_oi = _scan_unusual_activity(chains, state)
        self.assertGreater(len(unusual), 0)
        # Our strikes get priority, then sorted by notional
        strike_labels = [u["strike_label"] for u in unusual if u["strike_label"]]
        self.assertIn("YOUR LONG STRIKE", strike_labels)
        self.assertIn("YOUR SHORT STRIKE", strike_labels)
        # OI snapshot should be saved
        self.assertIn(f"CALL_{float(ls)}_{expiry}", new_oi)

    @patch("alerts.options_flow._save_state")
    @patch("alerts.options_flow._load_state")
    @patch("alerts.options_flow.yf.Ticker")
    @patch("alerts.options_flow.send_alert")
    def test_analyze_and_alert_no_chains(self, mock_send, mock_ticker_cls, mock_load, mock_save):
        from alerts.options_flow import analyze_and_alert
        mock_load.return_value = {"date": None, "prev_oi": {}, "prev_pcr": None, "prev_iv": {},
                                   "last_pcr_alert_value": None, "last_liquidity_alert_date": None}
        mock_ticker = MagicMock()
        mock_ticker.options = []
        mock_ticker.option_chain.side_effect = Exception("No chain")
        mock_ticker_cls.return_value = mock_ticker

        analyze_and_alert()
        mock_send.assert_not_called()

    @patch("alerts.options_flow.requests.post")
    @patch("alerts.options_flow._save_state")
    @patch("alerts.options_flow._load_state")
    @patch("alerts.options_flow.yf.Ticker")
    @patch("alerts.options_flow.send_alert")
    def test_analyze_and_alert_with_flow(self, mock_send, mock_ticker_cls, mock_load, mock_save, mock_post):
        from alerts.options_flow import analyze_and_alert
        import pandas as pd

        mock_load.return_value = {"date": None, "prev_oi": {}, "prev_pcr": None, "prev_iv": {},
                                   "last_pcr_alert_value": None, "last_liquidity_alert_date": None}

        calls_data = pd.DataFrame({
            "strike": [380, 400],
            "volume": [1000, 2000],
            "openInterest": [100, 200],
            "impliedVolatility": [0.45, 0.50],
            "lastPrice": [15.0, 3.0],
            "bid": [14.5, 2.8],
            "ask": [15.5, 3.2],
        })
        puts_data = pd.DataFrame({
            "strike": [380], "volume": [10], "openInterest": [100],
            "impliedVolatility": [0.40], "lastPrice": [2.0],
        })
        mock_chain = MagicMock()
        mock_chain.calls = calls_data
        mock_chain.puts = puts_data

        mock_ticker = MagicMock()
        mock_ticker.option_chain.return_value = mock_chain
        mock_ticker.options = ["2026-03-20"]
        mock_ticker_cls.return_value = mock_ticker

        mock_post.return_value = MagicMock(
            status_code=200, raise_for_status=MagicMock(),
            json=MagicMock(return_value={"choices": [{"message": {"content": "Bullish flow."}}]}),
        )

        analyze_and_alert()
        # Should have sent at least the unusual flow alert
        self.assertTrue(mock_send.called)
        msg = mock_send.call_args_list[0][0][0]
        self.assertIn("CALL $400", msg)

    def test_pcr_calculation(self):
        from alerts.options_flow import _calculate_pcr
        import pandas as pd

        calls = pd.DataFrame({"volume": [100, 200]})
        puts = pd.DataFrame({"volume": [150, 50]})
        chains = {"2026-03-20": {"calls": calls, "puts": puts}}

        pcr = _calculate_pcr(chains)
        self.assertAlmostEqual(pcr, 200 / 300, places=2)

    def test_pcr_zero_calls(self):
        from alerts.options_flow import _calculate_pcr
        import pandas as pd

        calls = pd.DataFrame({"volume": [0, 0]})
        puts = pd.DataFrame({"volume": [100]})
        chains = {"2026-03-20": {"calls": calls, "puts": puts}}
        self.assertIsNone(_calculate_pcr(chains))


# ============================================================================
# Test Economic Data
# ============================================================================
class TestEconomicData(unittest.TestCase):
    """Test economic data scraping and analysis."""

    @patch("alerts.economic_data.requests.get")
    def test_fetch_cpi(self, mock_get):
        from alerts.economic_data import fetch_latest_economic_data
        mock_get.return_value = MagicMock(
            text="<html><body><pre>CPI rose 0.3% in February, the Consumer Price Index increased 0.3 percent seasonally adjusted</pre></body></html>",
            raise_for_status=MagicMock()
        )
        result = fetch_latest_economic_data("CPI")
        self.assertIsNotNone(result)
        self.assertIn("CPI", result)

    @patch("alerts.economic_data.requests.get")
    def test_fetch_nfp(self, mock_get):
        from alerts.economic_data import fetch_latest_economic_data
        mock_get.return_value = MagicMock(
            text="<html><body><pre>Nonfarm payrolls rose by 200,000 in February according to the Bureau of Labor Statistics</pre></body></html>",
            raise_for_status=MagicMock()
        )
        result = fetch_latest_economic_data("NFP")
        self.assertIsNotNone(result)

    @patch("alerts.economic_data.requests.get")
    def test_fetch_failure(self, mock_get):
        from alerts.economic_data import fetch_latest_economic_data
        mock_get.side_effect = Exception("Network error")
        result = fetch_latest_economic_data("CPI")
        self.assertIsNone(result)

    @patch("alerts.economic_data._roundtable_implications")
    @patch("alerts.economic_data.send_alert")
    @patch("alerts.economic_data.requests.post")
    def test_analyze_economic_release(self, mock_post, mock_send, mock_rt):
        from alerts.economic_data import analyze_economic_release
        mock_post.return_value = MagicMock(
            status_code=200,
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={
                "choices": [{"message": {"content": "CPI came in hot. Bears win."}}]
            })
        )
        analyze_economic_release("CPI", "CPI rose 0.4%")
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        self.assertIn("CPI REPORT ANALYSIS", msg)
        mock_rt.assert_called_once()

    @patch("alerts.economic_data._roundtable_implications")
    @patch("alerts.economic_data.send_alert")
    @patch("alerts.economic_data.requests.post")
    @patch("alerts.economic_data.fetch_fomc_statement")
    def test_analyze_fomc_with_diff(self, mock_fetch, mock_post, mock_send, mock_rt):
        from alerts.economic_data import analyze_fomc_with_diff
        mock_fetch.return_value = ("The Committee decided to maintain...", "Previous statement...")
        mock_post.return_value = MagicMock(
            status_code=200,
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={
                "choices": [{"message": {"content": "Dovish shift detected."}}]
            })
        )
        analyze_fomc_with_diff()
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        self.assertIn("FOMC STATEMENT", msg)
        mock_rt.assert_called_once()

    @patch("alerts.economic_data.send_alert")
    @patch("alerts.economic_data.fetch_fomc_statement")
    def test_analyze_fomc_no_statement(self, mock_fetch, mock_send):
        from alerts.economic_data import analyze_fomc_with_diff
        mock_fetch.return_value = (None, None)
        analyze_fomc_with_diff()
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        self.assertIn("Could not scrape", msg)

    @patch("alerts.economic_data._roundtable_implications")
    @patch("alerts.economic_data.send_alert")
    @patch("alerts.economic_data.requests.post")
    @patch("alerts.economic_data.fetch_mu_earnings_report")
    def test_analyze_mu_earnings(self, mock_fetch, mock_post, mock_send, mock_rt):
        from alerts.economic_data import analyze_mu_earnings
        mock_fetch.return_value = "FINANCIAL DATA:\nRevenue | $18.7B\nGM | 68%\nEPS | $8.42"
        mock_post.return_value = MagicMock(
            status_code=200,
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={
                "choices": [{"message": {"content": "Strong beat on all metrics."}}]
            })
        )
        analyze_mu_earnings()
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        self.assertIn("MU EARNINGS", msg)
        mock_rt.assert_called_once()

    @patch("alerts.economic_data.send_alert")
    @patch("alerts.economic_data.fetch_mu_earnings_report")
    def test_analyze_mu_earnings_scrape_failure(self, mock_fetch, mock_send):
        from alerts.economic_data import analyze_mu_earnings
        mock_fetch.return_value = None
        analyze_mu_earnings()
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        self.assertIn("Could not scrape", msg)

    @patch("alerts.economic_data.requests.get")
    def test_fetch_fomc_statement(self, mock_get):
        from alerts.economic_data import fetch_fomc_statement
        # First call: FOMC calendar page with statement links
        calendar_html = (
            '<html><body>'
            '<a href="/monetarypolicy/fomcpresconf20260318.htm">Press Conf</a>'
            '<a href="/newsevents/pressreleases/monetary20260318a.htm">Statement</a>'
            '<a href="/newsevents/pressreleases/monetary20260129a.htm">Statement</a>'
            '</body></html>'
        )
        # Statement page
        statement_html = (
            '<html><body>'
            '<div id="article"><p>The Federal Open Market Committee decided to maintain the target range.</p></div>'
            '</body></html>'
        )
        mock_get.side_effect = [
            MagicMock(text=calendar_html, raise_for_status=MagicMock()),
            MagicMock(text=statement_html, raise_for_status=MagicMock()),
            MagicMock(text=statement_html, raise_for_status=MagicMock()),
        ]
        latest, previous = fetch_fomc_statement()
        self.assertIsNotNone(latest)
        self.assertIn("Federal Open Market Committee", latest)

    @patch("alerts.economic_data.requests.get")
    def test_fetch_mu_earnings(self, mock_get):
        from alerts.economic_data import fetch_mu_earnings_report
        # First call: quarterly-results page
        listing_html = (
            '<html><body>'
            '<a href="/news-releases/reports-results/micron-q2-2026">View Press Release</a>'
            '</body></html>'
        )
        # Second call: actual press release
        release_html = (
            '<html><body>'
            '<p>Micron Technology reported fiscal Q2 results exceeding expectations.</p>'
            '<table><tr><th>Revenue</th><td>$18.7B</td></tr>'
            '<tr><th>Gross Margin</th><td>68.0%</td></tr></table>'
            '</body></html>'
        )
        mock_get.side_effect = [
            MagicMock(text=listing_html, raise_for_status=MagicMock()),
            MagicMock(text=release_html, raise_for_status=MagicMock()),
        ]
        result = fetch_mu_earnings_report()
        self.assertIsNotNone(result)
        self.assertIn("18.7B", result)


class TestEarningsContext(unittest.TestCase):
    """Test earnings context data fed to DeepSeek is correct and unambiguous."""

    def _mock_yf_ticker(self):
        """Build a mock yfinance Ticker with known FQ1-26 data."""
        import pandas as pd
        from datetime import datetime

        ticker = MagicMock()

        # earnings_history: Non-GAAP EPS actual vs estimate
        eh_data = {
            "epsActual": [1.56, 1.91, 3.03, 4.78],
            "epsEstimate": [1.42, 1.59, 2.86, 3.96],
            "epsDifference": [0.14, 0.32, 0.17, 0.82],
            "surprisePercent": [0.0949, 0.1975, 0.0594, 0.2071],
        }
        eh_idx = pd.to_datetime(["2025-02-28", "2025-05-31", "2025-08-31", "2025-11-30"])
        eh_idx.name = "quarter"
        ticker.earnings_history = pd.DataFrame(eh_data, index=eh_idx)

        # quarterly_income_stmt: GAAP financials
        dates = pd.to_datetime(["2025-11-30", "2025-08-31", "2025-05-31", "2025-02-28", "2024-11-30"])
        inc_data = {
            "Total Revenue": [13643e6, 11315e6, 9301e6, 8053e6, 8709e6],
            "Gross Profit": [7646e6, 5054e6, 3508e6, 2963e6, 3348e6],
            "Net Income": [5240e6, 3201e6, 1885e6, 1583e6, 1870e6],
            "Diluted EPS": [4.60, 2.83, 1.68, 1.41, 1.67],
        }
        ticker.quarterly_income_stmt = pd.DataFrame(inc_data, index=dates).T

        # earnings_estimate: forward-looking (0q = FQ2, +1q = FQ3)
        eps_est = pd.DataFrame({
            "avg": [8.58, 10.16, 34.62, 46.34],
            "low": [6.99, 7.53, 28.42, 36.00],
            "high": [9.70, 15.00, 45.70, 70.77],
            "yearAgoEps": [1.56, 1.91, 8.29, 34.62],
            "numberOfAnalysts": [26, 26, 32, 29],
            "growth": [4.50, 4.32, 3.18, 0.34],
        }, index=["0q", "+1q", "0y", "+1y"])
        ticker.earnings_estimate = eps_est

        # revenue_estimate
        rev_est = pd.DataFrame({
            "avg": [19.07e9, 21.85e9, 77.67e9, 101.71e9],
            "low": [17.38e9, 19.42e9, 53.75e9, 60.95e9],
            "high": [21.00e9, 30.20e9, 102.73e9, 176.83e9],
            "numberOfAnalysts": [30, 30, 39, 38],
            "yearAgoRevenue": [8.053e9, 9.301e9, 37.378e9, 77.674e9],
            "growth": [1.37, 1.35, 1.08, 0.31],
        }, index=["0q", "+1q", "0y", "+1y"])
        ticker.revenue_estimate = rev_est

        return ticker

    @patch("alerts.economic_data.yf.Ticker" if hasattr(__import__("alerts.economic_data", fromlist=["yf"]), "yf") else "yfinance.Ticker")
    def test_context_contains_gaap_eps(self, mock_ticker_cls=None):
        """Context must show GAAP EPS for each quarter."""
        with patch("yfinance.Ticker", return_value=self._mock_yf_ticker()):
            from alerts.economic_data import _fetch_earnings_context
            ctx = _fetch_earnings_context()
        # FQ1-26 GAAP EPS is $4.60
        self.assertIn("GAAP EPS: $4.60", ctx)
        self.assertIn("GAAP EPS: $2.83", ctx)
        self.assertIn("GAAP EPS: $1.68", ctx)
        self.assertIn("GAAP EPS: $1.41", ctx)

    def test_context_contains_nongaap_eps_vs_estimate(self):
        """Context must show Non-GAAP EPS actual vs estimate with beat/miss."""
        with patch("yfinance.Ticker", return_value=self._mock_yf_ticker()):
            from alerts.economic_data import _fetch_earnings_context
            ctx = _fetch_earnings_context()
        self.assertIn("Non-GAAP EPS: $4.78 vs est $3.96 = BEAT", ctx)
        self.assertIn("Non-GAAP EPS: $3.03 vs est $2.86 = BEAT", ctx)

    def test_context_contains_revenue(self):
        """Context must show actual revenue for each quarter."""
        with patch("yfinance.Ticker", return_value=self._mock_yf_ticker()):
            from alerts.economic_data import _fetch_earnings_context
            ctx = _fetch_earnings_context()
        self.assertIn("Revenue: $13.643B", ctx)
        self.assertIn("Revenue: $11.315B", ctx)

    def test_context_contains_gross_margin_pct(self):
        """Context must show gross margin as a percentage."""
        with patch("yfinance.Ticker", return_value=self._mock_yf_ticker()):
            from alerts.economic_data import _fetch_earnings_context
            ctx = _fetch_earnings_context()
        self.assertIn("56.0%", ctx)
        self.assertIn("44.7%", ctx)

    def test_context_contains_net_income(self):
        """Context must show net income."""
        with patch("yfinance.Ticker", return_value=self._mock_yf_ticker()):
            from alerts.economic_data import _fetch_earnings_context
            ctx = _fetch_earnings_context()
        self.assertIn("Net Income: $5.240B", ctx)

    def test_context_next_q_clearly_labeled(self):
        """Next quarter consensus must be clearly labeled as NEXT, not current."""
        with patch("yfinance.Ticker", return_value=self._mock_yf_ticker()):
            from alerts.economic_data import _fetch_earnings_context
            ctx = _fetch_earnings_context()
        self.assertIn("NEXT QUARTER", ctx)
        # Next Q revenue est $19.07B must appear ONLY under next quarter section
        next_q_idx = ctx.index("NEXT QUARTER")
        rev_est_idx = ctx.index("$19.07B")
        self.assertGreater(rev_est_idx, next_q_idx,
                           "Revenue estimate $19.07B should appear AFTER 'NEXT QUARTER' label")

    def test_context_does_not_confuse_quarters(self):
        """Must not mix FQ1 actuals with FQ2 estimates."""
        with patch("yfinance.Ticker", return_value=self._mock_yf_ticker()):
            from alerts.economic_data import _fetch_earnings_context
            ctx = _fetch_earnings_context()
        # $13.643B (FQ1 actual) should NOT appear near $19.07B (FQ2 est)
        lines = ctx.split("\n")
        for line in lines:
            self.assertFalse(
                "13.643" in line and "19.07" in line,
                f"FQ1 revenue and FQ2 estimate should not be on the same line: {line}"
            )

    def test_context_revenue_est_for_next_quarter(self):
        """Must show revenue estimate for guidance comparison."""
        with patch("yfinance.Ticker", return_value=self._mock_yf_ticker()):
            from alerts.economic_data import _fetch_earnings_context
            ctx = _fetch_earnings_context()
        self.assertIn("Revenue est: $19.07B", ctx)

    def test_context_eps_est_for_next_quarter(self):
        """Must show EPS estimate for guidance comparison."""
        with patch("yfinance.Ticker", return_value=self._mock_yf_ticker()):
            from alerts.economic_data import _fetch_earnings_context
            ctx = _fetch_earnings_context()
        self.assertIn("EPS est: $8.58", ctx)

    def test_context_has_full_year_estimates(self):
        """Must show full year consensus."""
        with patch("yfinance.Ticker", return_value=self._mock_yf_ticker()):
            from alerts.economic_data import _fetch_earnings_context
            ctx = _fetch_earnings_context()
        self.assertIn("FULL YEAR", ctx)
        self.assertIn("$34.62", ctx)

    def test_context_margin_shown_as_pct_not_just_dollars(self):
        """Gross margin must be shown as percentage, not just dollar amount."""
        with patch("yfinance.Ticker", return_value=self._mock_yf_ticker()):
            from alerts.economic_data import _fetch_earnings_context
            ctx = _fetch_earnings_context()
        # Find the line with gross profit for FQ1-26
        lines = ctx.split("\n")
        gm_lines = [l for l in lines if "Gross Profit" in l and "56" in l]
        self.assertTrue(len(gm_lines) > 0, "Must have a gross profit line for FQ1-26")
        gm_line = gm_lines[0]
        self.assertIn("GM:", gm_line, f"Must show GM% label: {gm_line}")
        self.assertIn("%", gm_line, f"Gross profit line must show percentage: {gm_line}")

    def test_context_gross_margin_label_not_confusing(self):
        """Label should say 'Gross Profit' for dollar amount, not 'Gross Margin'."""
        with patch("yfinance.Ticker", return_value=self._mock_yf_ticker()):
            from alerts.economic_data import _fetch_earnings_context
            ctx = _fetch_earnings_context()
        # Should NOT have "Gross Margin: $X.XXXB" which confuses margin (%) with profit ($)
        lines = ctx.split("\n")
        for line in lines:
            if "Gross Margin:" in line and "$" in line and "B" in line:
                self.fail(f"Should use 'Gross Profit' not 'Gross Margin' for dollar amounts: {line}")

    def test_context_no_revenue_estimate_for_reported_quarter(self):
        """Must NOT fabricate a revenue estimate for the already-reported quarter."""
        with patch("yfinance.Ticker", return_value=self._mock_yf_ticker()):
            from alerts.economic_data import _fetch_earnings_context
            ctx = _fetch_earnings_context()
        # In the QUARTERLY RESULTS section (before NEXT QUARTER), Revenue lines
        # should show actuals only, no "est"
        quarterly_section = ctx.split("NEXT QUARTER")[0]
        lines = quarterly_section.split("\n")
        for line in lines:
            if "Revenue:" in line:
                self.assertNotIn("est", line.lower(),
                                 f"Reported quarter revenue should not have an estimate: {line}")


# ============================================================================
# Test Analyst Tracker
# ============================================================================
class TestAnalystTracker(unittest.TestCase):
    """Test analyst rating change detection."""

    def test_load_state_empty(self):
        from alerts.analyst_tracker import load_state
        with patch("alerts.analyst_tracker.STATE_FILE", "/tmp/_no_analyst.json"):
            state = load_state()
            self.assertEqual(state, {"seen": {}})

    @patch("alerts.analyst_tracker.send_alert")
    @patch("alerts.analyst_tracker.save_state")
    @patch("alerts.analyst_tracker.load_state")
    @patch("alerts.analyst_tracker.yf.Ticker")
    @patch("alerts.analyst_tracker.requests.post")
    def test_check_analyst_changes_new_rating(self, mock_post, mock_ticker_cls, mock_load, mock_save, mock_send):
        from alerts.analyst_tracker import check_analyst_changes
        import pandas as pd

        mock_load.return_value = {"seen": {}}

        recs = pd.DataFrame({
            "Firm": ["Morgan Stanley"],
            "ToGrade": ["Overweight"],
            "FromGrade": ["Equal-Weight"],
            "Action": ["upgrade"],
        }, index=["2026-03-05"])

        mock_ticker = MagicMock()
        mock_ticker.upgrades_downgrades = recs
        mock_ticker_cls.return_value = mock_ticker

        mock_post.return_value = MagicMock(
            status_code=200,
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={
                "choices": [{"message": {"content": "Morgan Stanley upgrade is bullish."}}]
            })
        )

        check_analyst_changes()
        mock_send.assert_called()
        msg = mock_send.call_args[0][0]
        self.assertIn("ANALYST", msg)
        self.assertIn("Morgan Stanley", msg)

    @patch("alerts.analyst_tracker.send_alert")
    @patch("alerts.analyst_tracker.save_state")
    @patch("alerts.analyst_tracker.load_state")
    @patch("alerts.analyst_tracker.yf.Ticker")
    def test_check_analyst_no_recs(self, mock_ticker_cls, mock_load, mock_save, mock_send):
        from alerts.analyst_tracker import check_analyst_changes
        mock_load.return_value = {"seen": {}}
        mock_ticker = MagicMock()
        mock_ticker.upgrades_downgrades = None
        mock_ticker_cls.return_value = mock_ticker
        check_analyst_changes()
        mock_send.assert_not_called()


# ============================================================================
# Test Market Intel
# ============================================================================
class TestMarketIntel(unittest.TestCase):
    """Test short interest and sector rotation."""

    @patch("alerts.market_intel.send_alert")
    @patch("alerts.market_intel.save_state")
    @patch("alerts.market_intel.load_state")
    @patch("alerts.market_intel.yf.Ticker")
    @patch("alerts.market_intel.requests.post")
    def test_check_short_interest(self, mock_post, mock_ticker_cls, mock_load, mock_save, mock_send):
        from alerts.market_intel import check_short_interest
        mock_load.return_value = {"last_si_date": None, "last_rotation_date": None}

        mock_ticker = MagicMock()
        mock_ticker.info = {
            "shortPercentOfFloat": 0.085,
            "shortRatio": 3.2,
            "sharesShort": 50000000,
            "sharesShortPriorMonth": 45000000,
        }
        mock_ticker_cls.return_value = mock_ticker

        mock_post.return_value = MagicMock(
            status_code=200,
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={
                "choices": [{"message": {"content": "SI elevated but not extreme."}}]
            })
        )

        check_short_interest()
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        self.assertIn("SHORT INTEREST", msg)
        self.assertIn("8.5%", msg)

    @patch("alerts.market_intel.send_alert")
    @patch("alerts.market_intel.save_state")
    @patch("alerts.market_intel.load_state")
    @patch("alerts.market_intel.yf.Ticker")
    @patch("alerts.market_intel.requests.post")
    def test_check_sector_rotation(self, mock_post, mock_ticker_cls, mock_load, mock_save, mock_send):
        from alerts.market_intel import check_sector_rotation
        import pandas as pd
        import numpy as np
        mock_load.return_value = {"last_si_date": None, "last_rotation_date": None}

        def make_ticker(etf):
            mock = MagicMock()
            prices = {
                "XLK": (200, 198), "SOXX": (600, 595), "XLF": (40, 39.8),
                "XLE": (80, 80.5), "XLV": (90, 89.5), "XLI": (120, 119),
                "XLP": (75, 75.2), "XLU": (65, 64.8), "XLRE": (40, 40.1),
                "QQQ": (500, 498), "SPY": (580, 578), "IWM": (220, 218),
            }
            p, prev = prices.get(etf, (100, 100))
            # Build a 10-day history DataFrame
            dates = pd.date_range(end="2026-03-06", periods=10, freq="B")
            closes = np.linspace(prev, p, 10)
            hist = pd.DataFrame({"Close": closes}, index=dates)
            mock.history.return_value = hist
            return mock
        mock_ticker_cls.side_effect = make_ticker

        mock_post.return_value = MagicMock(
            status_code=200,
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={
                "choices": [{"message": {"content": "Money flowing into tech."}}]
            })
        )

        check_sector_rotation()
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        self.assertIn("SECTOR ROTATION", msg)
        self.assertIn("5-Day Trend", msg)

    @patch("alerts.market_intel.save_state")
    @patch("alerts.market_intel.load_state")
    @patch("alerts.market_intel.yf.Ticker")
    @patch("alerts.market_intel.send_alert")
    def test_short_interest_skips_if_already_checked(self, mock_send, mock_ticker, mock_load, mock_save):
        from alerts.market_intel import check_short_interest
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("US/Eastern")).strftime("%Y-%m-%d")
        mock_load.return_value = {"last_si_date": today, "last_rotation_date": None}
        check_short_interest()
        mock_send.assert_not_called()


# ============================================================================
# Test Weekend War Room
# ============================================================================
class TestWeekendWarRoom(unittest.TestCase):
    """Test weekend war room generation."""

    @patch("alerts.weekend_warroom.requests.post")
    @patch("alerts.weekend_warroom.get_daily_data")
    @patch("alerts.weekend_warroom.estimate_spread_value")
    @patch("alerts.weekend_warroom.get_recent_headlines")
    def test_build_war_room(self, mock_headlines, mock_spread, mock_daily, mock_post):
        from alerts.weekend_warroom import build_war_room
        mock_daily.side_effect = lambda t: {
            "MU": {"price": 390, "prev_close": 388, "day_high": 392, "day_low": 387},
            "^VIX": {"price": 22.0, "prev_close": 21.0, "day_high": None, "day_low": None},
            "NVDA": {"price": 800, "prev_close": 795, "day_high": None, "day_low": None},
            "AMD": {"price": 150, "prev_close": 148, "day_high": None, "day_low": None},
            "AVGO": {"price": 200, "prev_close": 198, "day_high": None, "day_low": None},
            "MRVL": {"price": 90, "prev_close": 89, "day_high": None, "day_low": None},
        }.get(t)
        mock_spread.return_value = 10.5
        mock_headlines.return_value = [{"title": "Micron HBM demand surges"}]
        mock_post.return_value = MagicMock(
            status_code=200,
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={
                "choices": [{"message": {"content": "Week in review: semis rallied."}}]
            })
        )

        msg = build_war_room()
        self.assertIn("WEEKEND WAR ROOM", msg)
        self.assertIn("$390.00", msg)
        self.assertIn("WAR ROOM ANALYSIS", msg)

    @patch("alerts.weekend_warroom.get_daily_data")
    def test_build_war_room_no_data(self, mock_daily):
        from alerts.weekend_warroom import build_war_room
        mock_daily.return_value = None
        msg = build_war_room()
        self.assertIn("Failed to fetch", msg)


# ============================================================================
# Test Trade Journal
# ============================================================================
class TestTradeJournal(unittest.TestCase):
    """Test trade journal logging."""

    @patch("alerts.trade_journal.POSITION", TEST_POSITION)
    def test_log_and_read(self):
        from alerts.trade_journal import log_trade, load_journal
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            journal_file = f.name

        try:
            os.unlink(journal_file)
            with patch("alerts.trade_journal.JOURNAL_FILE", journal_file), \
                 patch("alerts.trade_journal.get_live_price", return_value=390.0), \
                 patch("alerts.trade_journal.estimate_spread_value", return_value=10.5), \
                 patch("alerts.trade_journal.send_alert") as mock_send:
                entry = log_trade("BUY", "Initial position")
                self.assertEqual(entry["action"], "BUY")
                self.assertAlmostEqual(entry["mu_price"], 390.0)
                mock_send.assert_called_once()

                with patch("alerts.trade_journal.JOURNAL_FILE", journal_file):
                    journal = load_journal()
                    self.assertEqual(len(journal), 1)
        finally:
            if os.path.exists(journal_file):
                os.unlink(journal_file)

    def test_get_journal_summary_empty(self):
        from alerts.trade_journal import get_journal_summary
        with patch("alerts.trade_journal.load_journal", return_value=[]):
            result = get_journal_summary()
            self.assertIn("No trades logged", result)

    def test_get_journal_summary_with_entries(self):
        from alerts.trade_journal import get_journal_summary
        entries = [
            {"id": 1, "timestamp": "2026-03-06T10:00", "action": "BUY",
             "mu_price": 390.0, "pnl": 0, "details": "entry"},
        ]
        with patch("alerts.trade_journal.load_journal", return_value=entries):
            result = get_journal_summary()
            self.assertIn("TRADE JOURNAL", result)
            self.assertIn("BUY", result)

    @patch("alerts.trade_journal.POSITION", TEST_POSITION)
    @patch("alerts.trade_journal.requests.post")
    @patch("alerts.trade_journal.get_live_price")
    @patch("alerts.trade_journal.estimate_spread_value")
    @patch("alerts.trade_journal.load_journal")
    def test_post_mortem(self, mock_journal, mock_spread, mock_price, mock_post):
        from alerts.trade_journal import post_mortem
        mock_journal.return_value = [
            {"id": 1, "timestamp": "2026-03-06T10:00", "action": "BUY",
             "mu_price": 390.0, "pnl": 0, "details": ""},
        ]
        mock_price.return_value = 395.0
        mock_spread.return_value = 13.0
        mock_post.return_value = MagicMock(
            status_code=200,
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={
                "choices": [{"message": {"content": "Grade B+. Good entry."}}]
            })
        )
        result = post_mortem()
        self.assertIn("POST-MORTEM", result)
        self.assertIn("Grade", result)


# ============================================================================
# Test Watchlist Scanner
# ============================================================================
class TestWatchlist(unittest.TestCase):
    """Test watchlist setup scanning."""

    @patch("alerts.watchlist.yf.Ticker")
    def test_scan_setups(self, mock_ticker_cls):
        from alerts.watchlist import scan_setups
        import pandas as pd
        import numpy as np

        def make_ticker(sym):
            mock = MagicMock()
            hist = pd.DataFrame({
                "Close": np.linspace(100, 110, 20),
            })
            mock.history.return_value = hist
            mock.fast_info = {
                "lastPrice": 110.0,
                "previousClose": 108.0,
                "yearHigh": 115.0,
            }
            return mock
        mock_ticker_cls.side_effect = make_ticker

        setups = scan_setups()
        self.assertGreater(len(setups), 0)
        self.assertIn("price", setups[0])
        self.assertIn("month_pct", setups[0])
        # Sorted by month_pct descending
        for i in range(len(setups) - 1):
            self.assertGreaterEqual(setups[i]["month_pct"], setups[i + 1]["month_pct"])


# ============================================================================
# Test Interactive Bot
# ============================================================================
class TestBotInteractive(unittest.TestCase):
    """Test interactive Telegram bot command handlers."""

    def test_handle_help(self):
        from alerts.bot_interactive import handle_command
        result = handle_command("/help")
        self.assertIn("COMMANDS", result)
        self.assertIn("/ask", result)
        self.assertIn("/price", result)
        self.assertIn("/sim", result)

    @patch("alerts.bot_interactive.POSITION", TEST_POSITION)
    @patch("alerts.bot_interactive.estimate_spread_value", return_value=12.0)
    @patch("alerts.bot_interactive.get_prev_close", return_value=390.00)
    @patch("alerts.bot_interactive.get_live_price", return_value=392.50)
    def test_handle_price_mu(self, mock_price, mock_prev, mock_spread):
        from alerts.bot_interactive import handle_price
        result = handle_price("MU")
        self.assertIn("$392.50", result)
        self.assertIn("Spread", result)
        self.assertIn("P&L", result)

    @patch("alerts.bot_interactive.get_prev_close", return_value=795.0)
    @patch("alerts.bot_interactive.get_live_price", return_value=800.0)
    def test_handle_price_other(self, mock_price, mock_prev):
        from alerts.bot_interactive import handle_price
        result = handle_price("NVDA")
        self.assertIn("$800.00", result)
        self.assertIn("NVDA", result)
        self.assertNotIn("Spread", result)

    @patch("alerts.bot_interactive.get_live_price", return_value=None)
    def test_handle_price_not_found(self, mock_price):
        from alerts.bot_interactive import handle_price
        result = handle_price("BADTICKER")
        self.assertIn("Could not fetch", result)

    @patch("alerts.bot_interactive.requests.post")
    @patch("alerts.bot_interactive._get_full_context", return_value="POSITION: 500x MU 380/400, MU $390, P&L +$5000")
    def test_handle_ask(self, mock_ctx, mock_post):
        from alerts.bot_interactive import handle_ask
        mock_post.return_value = MagicMock(
            status_code=200,
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={
                "choices": [{"message": {"content":
                    "Rex: Bullish. Vera: Cautious. Arbiter: HOLD."
                }}]
            })
        )
        result = handle_ask("Should I sell before FOMC?")
        self.assertIn("ROUNDTABLE", result)
        self.assertIn("Should I sell", result)
        self.assertIn("HOLD", result)

    @patch("alerts.bot_interactive.POSITION", TEST_POSITION)
    @patch("alerts.bot_interactive.requests.post")
    @patch("alerts.bot_interactive.estimate_spread_value")
    @patch("alerts.bot_interactive.get_live_price", return_value=390.0)
    def test_handle_sim_with_target(self, mock_price, mock_spread, mock_post):
        from alerts.bot_interactive import handle_sim
        mock_spread.side_effect = lambda p: max(0, p - 380) if p < 400 else 20.0
        mock_post.return_value = MagicMock(
            status_code=200,
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={
                "choices": [{"message": {"content": "25% probability of this scenario."}}]
            })
        )
        result = handle_sim("what if MU drops to 385")
        self.assertIn("SCENARIO SIM", result)
        self.assertIn("$385", result)

    @patch("alerts.bot_interactive.POSITION", TEST_POSITION)
    @patch("alerts.bot_interactive.requests.post")
    @patch("alerts.bot_interactive.estimate_spread_value", return_value=10.5)
    @patch("alerts.bot_interactive.get_live_price", return_value=390.0)
    def test_handle_sim_no_target(self, mock_price, mock_spread, mock_post):
        from alerts.bot_interactive import handle_sim
        mock_post.return_value = MagicMock(
            status_code=200,
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={
                "choices": [{"message": {"content": "Scenario analysis."}}]
            })
        )
        result = handle_sim("what if FOMC surprises with a cut")
        self.assertIn("SCENARIO SIM", result)

    def test_handle_unknown_command(self):
        from alerts.bot_interactive import handle_command
        result = handle_command("/unknowncmd")
        self.assertIsNone(result)

    def test_handle_message_routing_ask(self):
        from alerts.bot_interactive import handle_command
        with patch("alerts.bot_interactive.handle_ask", return_value="ask result") as mock_ask:
            result = handle_command("/ask should I sell?")
            mock_ask.assert_called_once_with("should I sell?")
            self.assertEqual(result, "ask result")

    def test_handle_message_routing_price(self):
        from alerts.bot_interactive import handle_command
        with patch("alerts.bot_interactive.handle_price", return_value="price result") as mock_price:
            result = handle_command("/price NVDA")
            mock_price.assert_called_once_with("NVDA")

    def test_handle_message_routing_sim(self):
        from alerts.bot_interactive import handle_command
        with patch("alerts.bot_interactive.handle_sim", return_value="sim result") as mock_sim:
            result = handle_command("/sim MU drops to 370")
            mock_sim.assert_called_once_with("MU drops to 370")

    def test_handle_journal(self):
        from alerts.bot_interactive import handle_command
        with patch("alerts.bot_interactive.get_journal_summary", return_value="journal data"):
            result = handle_command("/journal")
            self.assertEqual(result, "journal data")

    def test_handle_history(self):
        from alerts.bot_interactive import handle_command
        with patch("alerts.bot_interactive.get_message_log", return_value=[
            {"timestamp": "2026-03-06T10:00", "type": "text", "success": True, "message": "Test msg"},
        ]):
            result = handle_command("/history")
            self.assertIn("MESSAGE LOG", result)
            self.assertIn("Test msg", result)

    @patch("alerts.bot_interactive.requests.get")
    def test_get_updates(self, mock_get):
        from alerts.bot_interactive import get_updates
        mock_get.return_value = MagicMock(
            status_code=200,
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={
                "result": [
                    {"update_id": 1, "message": {"chat": {"id": 123}, "text": "/help"}}
                ]
            })
        )
        updates = get_updates()
        self.assertEqual(len(updates), 1)

    @patch("alerts.bot_interactive.requests.get")
    def test_get_updates_failure(self, mock_get):
        from alerts.bot_interactive import get_updates
        mock_get.side_effect = Exception("timeout")
        updates = get_updates()
        self.assertEqual(updates, [])

    @patch("alerts.bot_interactive.requests.post")
    @patch("alerts.bot_interactive._get_full_context", return_value="POSITION: 500x MU 380/400")
    def test_handle_reply(self, mock_ctx, mock_post):
        from alerts.bot_interactive import handle_reply
        mock_post.return_value = MagicMock(
            status_code=200, raise_for_status=MagicMock(),
            json=MagicMock(return_value={
                "choices": [{"message": {"content": "The IV spike means your spread is gaining value."}}]
            })
        )
        result = handle_reply("what does this mean for me?", "IV SPIKE: 380C IV 45% -> 62%")
        self.assertIn("REPLY", result)
        self.assertIn("IV spike", result)

    @patch("alerts.bot_interactive.requests.post")
    @patch("alerts.bot_interactive._get_full_context", return_value="POSITION: 500x MU 380/400")
    def test_handle_freetext(self, mock_ctx, mock_post):
        from alerts.bot_interactive import handle_freetext
        mock_post.return_value = MagicMock(
            status_code=200, raise_for_status=MagicMock(),
            json=MagicMock(return_value={
                "choices": [{"message": {"content": "MU is currently at $390, up 0.5% today."}}]
            })
        )
        result = handle_freetext("how is MU doing today?")
        self.assertIn("ANSWER", result)
        self.assertIn("MU is currently", result)

    @patch("alerts.bot_interactive.requests.post")
    @patch("alerts.bot_interactive._get_full_context", return_value="POSITION: test")
    def test_handle_reply_api_failure(self, mock_ctx, mock_post):
        from alerts.bot_interactive import handle_reply
        mock_post.side_effect = Exception("API down")
        result = handle_reply("question", "original alert text")
        self.assertIn("unavailable", result)

    def test_strip_html(self):
        from alerts.bot_interactive import _strip_html
        self.assertEqual(_strip_html("<b>BOLD</b> text"), "BOLD text")
        self.assertEqual(_strip_html("<i>italic</i> <a href='x'>link</a>"), "italic link")

    def test_handle_spreads_command(self):
        from alerts.bot_interactive import handle_command
        result = handle_command("/spreads")
        # Either returns spread data or an error message (no live data in test)
        self.assertIsNotNone(result)


# ============================================================================
# Test Daily Briefing
# ============================================================================
class TestDailyBriefing(unittest.TestCase):
    """Test daily briefing generation."""

    def _mock_daily_data(self, t):
        data = {
            "MU": {"price": 390, "prev_close": 388, "day_high": 392, "day_low": 387},
            "^VIX": {"price": 22.0, "prev_close": 21.0, "day_high": None, "day_low": None},
            "SOXX": {"price": 600, "prev_close": 598, "day_high": None, "day_low": None},
            "NVDA": {"price": 800, "prev_close": 795, "day_high": None, "day_low": None},
            "AMD": {"price": 150, "prev_close": 148, "day_high": None, "day_low": None},
            "AVGO": {"price": 200, "prev_close": 198, "day_high": None, "day_low": None},
            "MRVL": {"price": 90, "prev_close": 89, "day_high": None, "day_low": None},
            "ES=F": {"price": 5800, "prev_close": 5780, "day_high": None, "day_low": None},
            "NQ=F": {"price": 20000, "prev_close": 19900, "day_high": None, "day_low": None},
            "YM=F": {"price": 43000, "prev_close": 42900, "day_high": None, "day_low": None},
        }
        return data.get(t)

    @patch("alerts.daily_briefing.deepseek_daily_analysis", return_value="Roundtable says HOLD.")
    @patch("alerts.daily_briefing.get_recent_headlines", return_value=[])
    @patch("alerts.daily_briefing.estimate_spread_value", return_value=10.5)
    @patch("alerts.daily_briefing.get_daily_data")
    def test_build_briefing(self, mock_daily, mock_spread, mock_headlines, mock_analysis):
        from alerts.daily_briefing import build_briefing
        from alerts.config import POSITION
        mock_daily.side_effect = self._mock_daily_data
        msg = build_briefing()
        self.assertIn("DAILY BRIEFING", msg)
        self.assertIn("$390", msg)
        if POSITION.get("contracts"):
            self.assertIn("YOUR POSITION", msg)
        else:
            self.assertNotIn("YOUR POSITION", msg)
        self.assertIn("AI ROUNDTABLE", msg)

    @patch("alerts.daily_briefing.get_daily_data", return_value=None)
    def test_build_briefing_no_data(self, mock_daily):
        from alerts.daily_briefing import build_briefing
        msg = build_briefing()
        self.assertIn("Failed to fetch", msg)

    def test_get_upcoming_catalysts(self):
        from alerts.daily_briefing import get_upcoming_catalysts
        result = get_upcoming_catalysts(days_ahead=30)
        self.assertIsInstance(result, list)

    @patch("alerts.daily_briefing.detect_earnings_result", return_value=None)
    def test_get_framework_action_pre_earnings(self, mock_earn):
        from alerts.daily_briefing import get_framework_action
        action = get_framework_action(390.0, 10)
        self.assertIn("HOLD", action)

    @patch("alerts.daily_briefing.detect_earnings_result", return_value=None)
    def test_get_framework_action_3dte_high(self, mock_earn):
        from alerts.daily_briefing import get_framework_action
        action = get_framework_action(405.0, 3)
        self.assertIn("max profit", action.lower())

    @patch("alerts.daily_briefing.detect_earnings_result")
    def test_get_framework_action_beat(self, mock_earn):
        mock_earn.return_value = {"result": "BEAT", "move_pct": 8.0, "analysis": "Strong beat."}
        from alerts.daily_briefing import get_framework_action
        action = get_framework_action(405.0, 1)
        self.assertIn("BEAT", action)
        self.assertIn("HOLD", action)

    @patch("alerts.daily_briefing.detect_earnings_result")
    def test_get_framework_action_miss(self, mock_earn):
        mock_earn.return_value = {"result": "MISS", "move_pct": -5.0, "analysis": "Missed."}
        from alerts.daily_briefing import get_framework_action
        action = get_framework_action(370.0, 1)
        self.assertIn("MISS", action)
        self.assertIn("SELL", action)

    @patch("alerts.daily_briefing.requests.post")
    @patch("alerts.daily_briefing.get_recent_headlines", return_value=[])
    @patch("alerts.daily_briefing.estimate_spread_value", return_value=10.5)
    @patch("alerts.daily_briefing.get_daily_data")
    def test_deepseek_daily_analysis(self, mock_daily, mock_spread, mock_headlines, mock_post):
        from alerts.daily_briefing import deepseek_daily_analysis
        mock_daily.side_effect = self._mock_daily_data
        mock_post.return_value = MagicMock(
            status_code=200,
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={
                "choices": [{"message": {"content": "Rex: Bullish. Arbiter: HOLD."}}]
            })
        )
        result = deepseek_daily_analysis("preview")
        self.assertIn("HOLD", result)


# ============================================================================
# Test Run (main orchestrator)
# ============================================================================
class TestRun(unittest.TestCase):
    """Test the main run.py orchestration logic."""

    def test_should_check_prices(self):
        from alerts.run import should_check_prices
        self.assertIsInstance(should_check_prices(), bool)

    def test_should_send_briefing(self):
        from alerts.run import should_send_briefing
        self.assertIsInstance(should_send_briefing(), bool)

    def test_should_send_morning_recap(self):
        from alerts.run import should_send_morning_recap
        self.assertIsInstance(should_send_morning_recap(), bool)

    def test_should_send_war_room(self):
        from alerts.run import should_send_war_room
        self.assertIsInstance(should_send_war_room(), bool)

    def test_mark_briefing_sent(self):
        from alerts.run import mark_briefing_sent
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp = f.name
        try:
            os.unlink(tmp)
            with patch("alerts.run.BRIEFING_STATE_FILE", tmp):
                mark_briefing_sent("last_briefing_date")
                with open(tmp) as sf:
                    state = json.load(sf)
                self.assertIn("last_briefing_date", state)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    @patch("alerts.run.check_catalysts")
    @patch("alerts.run.should_check_prices", return_value=False)
    @patch("alerts.run.check_news")
    @patch("alerts.run.is_market_hours", return_value=False)
    @patch("alerts.run.should_send_briefing", return_value=False)
    @patch("alerts.run.should_send_morning_recap", return_value=False)
    @patch("alerts.run.should_send_war_room", return_value=False)
    @patch("alerts.run.check_youtube_news")
    def test_main_minimal(self, mock_yt, mock_war, mock_recap, mock_brief, mock_mkt, mock_news, mock_prices, mock_cat):
        from alerts.run import main
        main()
        mock_cat.assert_called_once()
        mock_news.assert_called_once()

    @patch("alerts.run.check_catalysts")
    @patch("alerts.run.should_check_prices", return_value=True)
    @patch("alerts.run.check_prices")
    @patch("alerts.run.check_news")
    @patch("alerts.run.is_market_hours", return_value=True)
    @patch("alerts.run.check_options_flow")
    @patch("alerts.run.check_analyst_changes")
    @patch("alerts.run.check_short_interest")
    @patch("alerts.run.check_sector_rotation")
    @patch("alerts.run.should_send_briefing", return_value=False)
    @patch("alerts.run.should_send_morning_recap", return_value=False)
    @patch("alerts.run.should_send_war_room", return_value=False)
    @patch("alerts.run.check_youtube_news")
    def test_main_market_hours(self, mock_yt, mock_war, mock_recap, mock_brief,
                                mock_rot, mock_si, mock_analyst, mock_flow,
                                mock_mkt, mock_news, mock_prices, mock_check, mock_cat):
        from alerts.run import main
        main()
        mock_cat.assert_called_once()
        mock_prices.assert_called_once()
        mock_news.assert_called_once()


# ============================================================================
# Test YouTube Monitor
# ============================================================================
class TestYouTubeMonitor(unittest.TestCase):
    """Test YouTube news monitoring."""

    def test_keyword_matching_tier1(self):
        from alerts.youtube_monitor import _is_relevant
        rel, tier, kws = _is_relevant("Micron Stock Surges on HBM Demand")
        self.assertTrue(rel)
        self.assertEqual(tier, 1)
        self.assertIn("micron", kws)

    def test_keyword_matching_tier2(self):
        from alerts.youtube_monitor import _is_relevant
        rel, tier, kws = _is_relevant("NVIDIA Blackwell GPU Production Ramps")
        self.assertTrue(rel)
        self.assertEqual(tier, 2)

    def test_keyword_matching_tier3_macro(self):
        from alerts.youtube_monitor import _is_relevant
        rel, tier, kws = _is_relevant("Fed Rate Decision: Powell Holds Steady")
        self.assertTrue(rel)
        self.assertEqual(tier, 3)

    def test_keyword_matching_tier3_high_impact(self):
        from alerts.youtube_monitor import _is_relevant
        rel, tier, kws = _is_relevant("FOMC Rate Decision: Fed Holds Steady")
        self.assertTrue(rel)
        self.assertEqual(tier, 3)

    def test_keyword_matching_tier3_multi_keyword(self):
        from alerts.youtube_monitor import _is_relevant
        rel, tier, kws = _is_relevant("Oil Price Spike Fuels Recession Risk")
        self.assertTrue(rel)
        self.assertEqual(tier, 3)

    def test_keyword_tier3_single_low_impact_rejected(self):
        from alerts.youtube_monitor import _is_relevant
        # Single low-impact Tier 3 keyword should NOT match
        rel, tier, kws = _is_relevant("Oil Price Hits $90")
        self.assertFalse(rel)

    def test_keyword_matching_jobs(self):
        from alerts.youtube_monitor import _is_relevant
        rel, tier, kws = _is_relevant("NFP Jobs Report: Economy Adds 200K")
        self.assertTrue(rel)
        self.assertEqual(tier, 3)

    def test_keyword_matching_tier2_ai_labs(self):
        from alerts.youtube_monitor import _is_relevant
        rel, tier, kws = _is_relevant("DeepSeek R2 crushes GPT-5 benchmarks")
        self.assertTrue(rel)
        self.assertEqual(tier, 2)
        # Generic AI lab names (anthropic, claude) should NOT match alone
        rel2, tier2, kws2 = _is_relevant("Anthropic Claude 4 new capabilities")
        self.assertFalse(rel2)

    def test_keyword_no_match(self):
        from alerts.youtube_monitor import _is_relevant
        rel, tier, kws = _is_relevant("Best Recipe for Banana Bread")
        self.assertFalse(rel)

    def test_keyword_transcript_tier2(self):
        from alerts.youtube_monitor import _is_relevant
        transcript = "Today we discuss nvidia and the semiconductor market outlook"
        rel, tier, kws = _is_relevant("Market Update", transcript)
        self.assertTrue(rel)
        self.assertEqual(tier, 2)

    def test_keyword_transcript_single_not_enough(self):
        from alerts.youtube_monitor import _is_relevant
        transcript = "Someone mentioned nvidia briefly"
        rel, tier, kws = _is_relevant("Cooking Show", transcript)
        self.assertFalse(rel)

    def test_escape_html(self):
        from alerts.youtube_monitor import _escape_html
        self.assertEqual(_escape_html("A & B <C>"), "A &amp; B &lt;C&gt;")

    def test_state_load_save(self):
        from alerts.youtube_monitor import load_state, save_state
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp = f.name
        try:
            os.unlink(tmp)
            with patch("alerts.youtube_monitor.STATE_FILE", tmp):
                state = load_state()
                self.assertEqual(state["seen_ids"], [])

                state["seen_ids"] = ["abc123"]
                state["initialized"] = True
                save_state(state)

                loaded = load_state()
                self.assertIn("abc123", loaded["seen_ids"])
                self.assertTrue(loaded["initialized"])
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    @patch("alerts.youtube_monitor._fetch_channel_feed")
    def test_first_run_no_alerts(self, mock_feed):
        from alerts.youtube_monitor import check_youtube_news
        mock_feed.return_value = [
            {"video_id": "v1", "title": "Micron News", "published": None, "channel": "Test"},
            {"video_id": "v2", "title": "NVIDIA Update", "published": None, "channel": "Test"},
        ]
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp = f.name
        try:
            os.unlink(tmp)
            with patch("alerts.youtube_monitor.STATE_FILE", tmp), \
                 patch("alerts.youtube_monitor.send_alert") as mock_send:
                check_youtube_news()
                mock_send.assert_not_called()  # First run = no alerts
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    @patch("alerts.youtube_monitor._fetch_channel_feed")
    @patch("alerts.youtube_monitor._fetch_transcript", return_value="Micron HBM demand is soaring")
    @patch("alerts.youtube_monitor._summarize_video", return_value="Test summary")
    def test_second_run_alerts_new(self, mock_summary, mock_transcript, mock_feed):
        from alerts.youtube_monitor import check_youtube_news, save_state
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp = f.name
        try:
            os.unlink(tmp)
            with patch("alerts.youtube_monitor.STATE_FILE", tmp), \
                 patch("alerts.youtube_monitor.send_alert") as mock_send:
                # First call: returns v1, marks as seen
                mock_feed.return_value = [
                    {"video_id": "v1", "title": "Old Video", "published": None, "channel": "Test"},
                ]
                check_youtube_news()
                mock_send.assert_not_called()

                # Second call: v1 still there + new v2 with MU keyword
                mock_feed.return_value = [
                    {"video_id": "v1", "title": "Old Video", "published": None, "channel": "Test"},
                    {"video_id": "v2", "title": "Micron Earnings Preview", "published": None, "channel": "CNBC"},
                ]
                check_youtube_news()
                mock_send.assert_called_once()  # Only v2 triggers alert
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    @patch("alerts.youtube_monitor.send_alert")
    def test_send_title_only_alert(self, mock_send):
        from alerts.youtube_monitor import _send_title_only_alert
        video = {"video_id": "abc", "title": "Test <Video>", "channel": "Test", "matched": ["micron"]}
        _send_title_only_alert(video)
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        self.assertIn("#YOUTUBE", msg)
        self.assertIn("Test &lt;Video&gt;", msg)  # HTML escaped

    @patch("alerts.youtube_monitor.send_alert")
    def test_send_video_alert(self, mock_send):
        from alerts.youtube_monitor import _send_video_alert
        video = {"video_id": "xyz", "title": "Micron HBM", "channel": "Bloomberg",
                 "channel_name": "Bloomberg", "tier": 1, "published": None}
        _send_video_alert(video, "Great summary here")
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        self.assertIn("#YOUTUBE", msg)
        self.assertIn("DIRECT MU", msg)
        self.assertIn("Great summary here", msg)
        self.assertIn("youtube.com/watch?v=xyz", msg)


# ============================================================================
# Test Macro Dashboard
# ============================================================================
class TestMacroDashboard(unittest.TestCase):
    """Test macro dashboard indicators, alerts, and formatting."""

    def test_load_state_missing(self):
        from alerts.macro_dashboard import load_state
        import tempfile
        with patch("alerts.macro_dashboard.STATE_FILE", tempfile.mktemp()):
            state = load_state()
            self.assertIn("last_values", state)
            self.assertIn("alerted_levels", state)

    def test_save_and_load_state(self):
        from alerts.macro_dashboard import load_state, save_state
        import tempfile
        tmp = tempfile.mktemp()
        try:
            with patch("alerts.macro_dashboard.STATE_FILE", tmp):
                save_state({"last_values": {"^VIX": 22}, "alerted_levels": {}, "date": "2026-03-06"})
                state = load_state()
                self.assertEqual(state["last_values"]["^VIX"], 22)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def test_calc_change(self):
        from alerts.macro_dashboard import _calc_change
        pct, arrow = _calc_change(105.0, 100.0)
        self.assertAlmostEqual(pct, 5.0, places=1)
        self.assertEqual(arrow, "\u25b2")

        pct, arrow = _calc_change(95.0, 100.0)
        self.assertAlmostEqual(pct, -5.0, places=1)
        self.assertEqual(arrow, "\u25bc")

        pct, arrow = _calc_change(100.0, 0.0)
        self.assertEqual(pct, 0.0)

    @patch("alerts.macro_dashboard._get_fear_greed")
    @patch("alerts.macro_dashboard._get_prev_close")
    @patch("alerts.macro_dashboard._get_price_fast")
    def test_fetch_all_indicators(self, mock_price, mock_prev, mock_fg):
        from alerts.macro_dashboard import fetch_all_indicators
        mock_price.return_value = 25.0
        mock_prev.return_value = 23.0
        mock_fg.return_value = {"score": 45, "label": "Neutral"}
        results = fetch_all_indicators()
        self.assertIn("fear_greed", results)
        self.assertEqual(results["fear_greed"]["value"], 45)
        # Should have entries for yfinance tickers
        self.assertGreater(len(results), 5)

    @patch("alerts.macro_dashboard._get_fear_greed")
    @patch("alerts.macro_dashboard._get_prev_close")
    @patch("alerts.macro_dashboard._get_price_fast")
    def test_fetch_indicators_derived(self, mock_price, mock_prev, mock_fg):
        """Test derived indicators (yield curve, credit ratio, copper/gold)."""
        from alerts.macro_dashboard import fetch_all_indicators, YF_TICKERS
        # Return different prices per ticker to test derived calculations
        prices = {"^TNX": 4.5, "^IRX": 4.2, "^FVX": 4.3, "HYG": 75.0, "LQD": 100.0,
                  "HG=F": 4.5, "GC=F": 3000.0}
        mock_price.side_effect = lambda t: prices.get(t, 100.0)
        mock_prev.return_value = 99.0
        mock_fg.return_value = {"score": 50, "label": "Neutral"}

        results = fetch_all_indicators()
        # Yield curve: 4.5 - 4.2 = 0.3
        self.assertIn("yield_curve_10y3m", results)
        self.assertAlmostEqual(results["yield_curve_10y3m"]["value"], 0.3, places=1)
        # Credit ratio: 75/100 = 0.75
        self.assertIn("credit_ratio", results)
        self.assertAlmostEqual(results["credit_ratio"]["value"], 0.75, places=2)
        # Copper/Gold
        self.assertIn("copper_gold", results)

    @patch("alerts.macro_dashboard.send_alert")
    @patch("alerts.macro_dashboard.fetch_all_indicators")
    def test_check_macro_alerts_threshold_crossing(self, mock_fetch, mock_send):
        from alerts.macro_dashboard import check_macro_alerts
        import tempfile
        tmp = tempfile.mktemp()
        try:
            with patch("alerts.macro_dashboard.STATE_FILE", tmp):
                # First call: seed values (VIX at 19)
                mock_fetch.return_value = {
                    "^VIX": {"name": "VIX", "value": 19.0, "prev": 18.0, "change_pct": 5.5, "arrow": "\u25b2"},
                }
                check_macro_alerts()
                mock_send.assert_not_called()  # No crossing yet

                # Second call: VIX crosses 20
                mock_fetch.return_value = {
                    "^VIX": {"name": "VIX", "value": 21.0, "prev": 19.0, "change_pct": 10.5, "arrow": "\u25b2"},
                }
                check_macro_alerts()
                mock_send.assert_called_once()
                msg = mock_send.call_args[0][0]
                self.assertIn("VIX", msg)
                self.assertIn("crossed 20", msg)
                self.assertIn("#MACRO", msg)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    @patch("alerts.macro_dashboard.send_alert")
    @patch("alerts.macro_dashboard.fetch_all_indicators")
    def test_check_macro_alerts_no_double_alert(self, mock_fetch, mock_send):
        """Should not alert the same level twice in one day."""
        from alerts.macro_dashboard import check_macro_alerts
        import tempfile
        tmp = tempfile.mktemp()
        try:
            with patch("alerts.macro_dashboard.STATE_FILE", tmp):
                # Seed
                mock_fetch.return_value = {
                    "^VIX": {"name": "VIX", "value": 19.0, "prev": 18.0, "change_pct": 5.5, "arrow": "\u25b2"},
                }
                check_macro_alerts()

                # Cross 20
                mock_fetch.return_value = {
                    "^VIX": {"name": "VIX", "value": 21.0, "prev": 19.0, "change_pct": 10.5, "arrow": "\u25b2"},
                }
                check_macro_alerts()
                self.assertEqual(mock_send.call_count, 1)

                # Still above 20 - should NOT alert again
                mock_fetch.return_value = {
                    "^VIX": {"name": "VIX", "value": 22.0, "prev": 21.0, "change_pct": 4.8, "arrow": "\u25b2"},
                }
                check_macro_alerts()
                self.assertEqual(mock_send.call_count, 1)  # Still 1
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def test_build_dashboard_formatting(self):
        from alerts.macro_dashboard import build_dashboard
        indicators = {
            "^VIX": {"name": "VIX", "value": 22.5, "prev": 21.0, "change_pct": 7.1, "arrow": "\u25b2"},
            "^SOX": {"name": "SOX Index", "value": 7800, "prev": 7700, "change_pct": 1.3, "arrow": "\u25b2"},
            "fear_greed": {"name": "Fear & Greed", "value": 35, "label": "Fear", "prev": None, "change_pct": 0, "arrow": ""},
            "^TNX": {"name": "10Y Yield", "value": 4.25, "prev": 4.20, "change_pct": 1.2, "arrow": "\u25b2"},
            "^IRX": {"name": "3M Yield", "value": 4.10, "prev": 4.10, "change_pct": 0, "arrow": "\u25ac"},
            "yield_curve_10y3m": {"name": "10Y-3M Spread", "value": 0.15, "prev": None, "change_pct": 0, "arrow": "\u25b2", "unit": "bps"},
        }
        msg = build_dashboard(indicators)
        self.assertIn("MACRO DASHBOARD", msg)
        self.assertIn("Fear & Greed", msg)
        self.assertIn("35", msg)
        self.assertIn("Fear", msg)
        self.assertIn("#MACRO_DASH", msg)
        self.assertIn("VIX", msg)
        self.assertIn("SOX", msg)

    @patch("alerts.macro_dashboard._send_ai_interpretation")
    @patch("alerts.macro_dashboard.send_alert")
    @patch("alerts.macro_dashboard.fetch_all_indicators")
    def test_send_dashboard(self, mock_fetch, mock_send, mock_ai):
        from alerts.macro_dashboard import send_dashboard
        mock_fetch.return_value = {
            "^VIX": {"name": "VIX", "value": 22.5, "prev": 21.0, "change_pct": 7.1, "arrow": "\u25b2"},
            "fear_greed": {"name": "Fear & Greed", "value": 45, "label": "Neutral", "prev": None, "change_pct": 0, "arrow": ""},
        }
        send_dashboard()
        mock_send.assert_called_once()
        mock_ai.assert_called_once()

    @patch("alerts.macro_dashboard.send_alert")
    @patch("alerts.macro_dashboard.requests.post")
    def test_ai_interpretation(self, mock_post, mock_send):
        from alerts.macro_dashboard import _send_ai_interpretation
        mock_post.return_value = MagicMock(
            status_code=200,
            raise_for_status=MagicMock(),
            json=MagicMock(return_value={
                "choices": [{"message": {"content": "Risk-off regime. VIX elevated. Watch 10Y."}}]
            })
        )
        indicators = {
            "^VIX": {"name": "VIX", "value": 28.0, "prev": 25.0, "change_pct": 12.0, "arrow": "\u25b2"},
        }
        _send_ai_interpretation(indicators)
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        self.assertIn("MACRO INTERPRETATION", msg)
        self.assertIn("#MACRO_AI", msg)
        self.assertIn("Risk-off regime", msg)

    @patch("alerts.macro_dashboard.send_alert")
    @patch("alerts.macro_dashboard.requests.post")
    def test_ai_interpretation_failure(self, mock_post, mock_send):
        from alerts.macro_dashboard import _send_ai_interpretation
        mock_post.side_effect = Exception("API error")
        _send_ai_interpretation({"^VIX": {"name": "VIX", "value": 25.0}})
        mock_send.assert_not_called()  # Should fail gracefully

    def test_fear_greed_labels(self):
        from alerts.macro_dashboard import FG_LABELS
        # Verify all ranges are covered
        self.assertEqual(len(FG_LABELS), 5)
        # Check boundary values
        ranges = list(FG_LABELS.keys())
        self.assertEqual(ranges[0], (0, 25))
        self.assertEqual(ranges[-1], (75, 100))

    def test_alert_thresholds_defined(self):
        from alerts.macro_dashboard import ALERT_THRESHOLDS
        self.assertIn("^VIX", ALERT_THRESHOLDS)
        self.assertIn("fear_greed", ALERT_THRESHOLDS)
        self.assertIn("CL=F", ALERT_THRESHOLDS)
        # VIX thresholds should be sorted
        vix = ALERT_THRESHOLDS["^VIX"]
        self.assertEqual(vix, sorted(vix))


# ============================================================================
# Test Catalyst Fetcher
# ============================================================================
class TestCatalystFetcher(unittest.TestCase):
    """Test automatic catalyst fetching and discovery."""

    def test_get_release_time(self):
        from alerts.catalyst_fetcher import _get_release_time
        self.assertEqual(_get_release_time("CPI January"), (8, 30))
        self.assertEqual(_get_release_time("ISM Manufacturing"), (10, 0))
        self.assertEqual(_get_release_time("FOMC Rate Decision"), (14, 0))
        self.assertEqual(_get_release_time("Unknown event"), (8, 30))  # default

    def test_get_relevance_tag(self):
        from alerts.catalyst_fetcher import _get_relevance_tag
        self.assertEqual(_get_relevance_tag("CPI January"), "KEY")
        self.assertEqual(_get_relevance_tag("FOMC Rate Decision"), "KEY")
        self.assertEqual(_get_relevance_tag("NFP February"), "KEY")
        self.assertEqual(_get_relevance_tag("ISM Manufacturing"), "")
        self.assertEqual(_get_relevance_tag("Random event"), "")

    def test_compute_expiry_dates(self):
        from alerts.catalyst_fetcher import _compute_expiry_dates
        expiries = _compute_expiry_dates()
        self.assertGreater(len(expiries), 0)
        # All should be on Fridays (use actual year from the computation)
        now = datetime.now()
        for month, day, hour, minute, desc in expiries:
            year = now.year if month >= now.month else now.year + 1
            d = date(year, month, day)
            self.assertEqual(d.weekday(), 4, f"{d} is not a Friday")

    @patch("alerts.catalyst_fetcher.yf.Ticker")
    def test_fetch_earnings_dates(self, mock_ticker_cls):
        from alerts.catalyst_fetcher import _fetch_earnings_dates
        from datetime import date as dt_date
        mock_t = MagicMock()
        mock_t.calendar = {
            "Earnings Date": [dt_date(2026, 6, 18)],
        }
        mock_ticker_cls.return_value = mock_t

        results = _fetch_earnings_dates()
        self.assertGreater(len(results), 0)
        # Should contain MU EARNINGS
        descs = [r[4] for r in results]
        self.assertTrue(any("MU EARNINGS" in d for d in descs))

    def test_load_cache_missing(self):
        from alerts.catalyst_fetcher import _load_cache
        with patch("alerts.catalyst_fetcher.CACHE_FILE", "/tmp/_no_catalyst_cache.json"):
            self.assertIsNone(_load_cache())

    def test_save_and_load_cache(self):
        from alerts.catalyst_fetcher import _save_cache, _load_cache
        tmp = tempfile.mktemp()
        try:
            with patch("alerts.catalyst_fetcher.CACHE_FILE", tmp):
                _save_cache({"catalysts": [[3, 18, 14, 0, "FOMC"]], "counts": {}})
                cache = _load_cache()
                self.assertIsNotNone(cache)
                self.assertEqual(len(cache["catalysts"]), 1)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def test_get_catalysts_from_cache(self):
        from alerts.catalyst_fetcher import get_catalysts, _save_cache
        tmp = tempfile.mktemp()
        try:
            with patch("alerts.catalyst_fetcher.CACHE_FILE", tmp):
                _save_cache({
                    "catalysts": [[3, 18, 14, 0, "FOMC"], [5, 5, 16, 15, "AMD EARNINGS"]],
                    "counts": {},
                })
                catalysts = get_catalysts()
                self.assertEqual(len(catalysts), 2)
                self.assertEqual(catalysts[0], (3, 18, 14, 0, "FOMC"))
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def test_add_discovered_catalyst(self):
        from alerts.catalyst_fetcher import add_discovered_catalyst, _save_cache
        import alerts.config as config_mod
        tmp = tempfile.mktemp()
        try:
            with patch("alerts.catalyst_fetcher.CACHE_FILE", tmp):
                _save_cache({"catalysts": [[3, 18, 14, 0, "FOMC"]], "counts": {}})
                old_cache = config_mod._catalysts_cache
                add_discovered_catalyst(4, 15, "Samsung HBM4 Announcement")
                # Read back
                with open(tmp) as f:
                    cache = json.load(f)
                self.assertEqual(len(cache["catalysts"]), 2)
                self.assertIn("discovered", cache["catalysts"][1][4])
                config_mod._catalysts_cache = old_cache
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def test_add_discovered_catalyst_no_duplicate(self):
        from alerts.catalyst_fetcher import add_discovered_catalyst, _save_cache
        tmp = tempfile.mktemp()
        try:
            with patch("alerts.catalyst_fetcher.CACHE_FILE", tmp):
                _save_cache({"catalysts": [[4, 15, 8, 30, "Samsung HBM4 Announcement (discovered)"]], "counts": {}})
                add_discovered_catalyst(4, 15, "Samsung HBM4 Announcement")
                with open(tmp) as f:
                    cache = json.load(f)
                self.assertEqual(len(cache["catalysts"]), 1)  # No duplicate
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def test_manual_events_defined(self):
        from alerts.catalyst_fetcher import MANUAL_EVENTS
        self.assertGreater(len(MANUAL_EVENTS), 0)
        # Check GTC is there
        descs = [e[4] for e in MANUAL_EVENTS]
        self.assertTrue(any("GTC" in d for d in descs))
        self.assertTrue(any("SEMICON" in d for d in descs))

    def test_catalyst_proxy_in_config(self):
        """Test that config.CATALYSTS works as lazy proxy."""
        from alerts.config import CATALYSTS
        # Should be iterable and have length
        self.assertGreater(len(CATALYSTS), 0)
        # Should be able to iterate
        for cat in CATALYSTS:
            self.assertEqual(len(cat), 5)
            break  # Just test first one

    def test_youtube_catalyst_extraction(self):
        from alerts.youtube_monitor import _extract_catalyst_from_summary
        with patch("alerts.catalyst_fetcher.add_discovered_catalyst") as mock_add:
            _extract_catalyst_from_summary("KEY POINTS:\n- stuff\n\nEVENT: 4/15 - Samsung HBM4 launch event")
            mock_add.assert_called_once_with(4, 15, "Samsung HBM4 launch event")

    def test_youtube_catalyst_extraction_no_event(self):
        from alerts.youtube_monitor import _extract_catalyst_from_summary
        with patch("alerts.catalyst_fetcher.add_discovered_catalyst") as mock_add:
            _extract_catalyst_from_summary("KEY POINTS:\n- stuff\n\nIMPACT ON MU: HIGH")
            mock_add.assert_not_called()


# ============================================================================
# Test Sector Rotation
# ============================================================================
class TestSectorRotation(unittest.TestCase):

    def test_imports(self):
        from alerts.sector_rotation import check_sector_rotation, get_sector_heatmap

    def test_sector_etfs_defined(self):
        from alerts.sector_rotation import SECTOR_ETFS
        self.assertIn("SOXX", SECTOR_ETFS)
        self.assertIn("SPY", SECTOR_ETFS)
        self.assertGreater(len(SECTOR_ETFS), 10)

    @patch("alerts.sector_rotation.send_alert")
    @patch("alerts.sector_rotation._get_returns")
    def test_detect_risk_off(self, mock_returns, mock_send):
        from alerts.sector_rotation import _detect_pattern
        returns = {
            "SOXX": {"ret_1d": -1, "ret_5d": -3.0, "ret_20d": -5, "price": 240, "vol_ratio": 1.2},
            "SMH": {"ret_1d": -1, "ret_5d": -2.5, "ret_20d": -4, "price": 250, "vol_ratio": 1.1},
            "XLK": {"ret_1d": -0.5, "ret_5d": -1.5, "ret_20d": -2, "price": 200, "vol_ratio": 1.0},
            "XLU": {"ret_1d": 1.0, "ret_5d": 2.5, "ret_20d": 4, "price": 70, "vol_ratio": 1.0},
            "XLP": {"ret_1d": 0.8, "ret_5d": 2.0, "ret_20d": 3, "price": 80, "vol_ratio": 1.0},
            "XLV": {"ret_1d": 0.5, "ret_5d": 1.5, "ret_20d": 2, "price": 140, "vol_ratio": 1.0},
            "XLY": {"ret_1d": -0.3, "ret_5d": -1.0, "ret_20d": -1, "price": 180, "vol_ratio": 1.0},
            "XLC": {"ret_1d": -0.2, "ret_5d": -0.5, "ret_20d": 0, "price": 85, "vol_ratio": 1.0},
            "SPY": {"ret_1d": -0.3, "ret_5d": -0.5, "ret_20d": -1, "price": 525, "vol_ratio": 1.0},
        }
        pattern, desc = _detect_pattern(returns)
        self.assertEqual(pattern, "RISK_OFF")

    def test_heatmap_returns_string(self):
        from alerts.sector_rotation import get_sector_heatmap
        with patch("alerts.sector_rotation._get_returns") as mock:
            mock.return_value = {"ret_1d": 0.5, "ret_5d": 1.0, "ret_20d": 2.0, "price": 100, "vol_ratio": 1.0}
            result = get_sector_heatmap()
            self.assertIn("Sector Heatmap", result)

    @patch("alerts.sector_rotation.save_state")
    @patch("alerts.sector_rotation.load_state")
    @patch("alerts.sector_rotation.send_alert")
    def test_no_alert_if_already_sent(self, mock_send, mock_load, mock_save):
        from alerts.sector_rotation import check_sector_rotation
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("US/Eastern")).strftime("%Y-%m-%d")
        mock_load.return_value = {"last_alert_date": today, "last_pattern": "RISK_OFF"}
        check_sector_rotation()
        mock_send.assert_not_called()


# ============================================================================
# Test ETF Flows
# ============================================================================
class TestETFFlows(unittest.TestCase):

    def test_imports(self):
        from alerts.etf_flows import check_etf_flows, get_etf_flow_summary

    def test_etf_weights_defined(self):
        from alerts.etf_flows import MU_ETF_WEIGHTS
        self.assertIn("SOXX", MU_ETF_WEIGHTS)
        self.assertIn("SMH", MU_ETF_WEIGHTS)
        for ticker, meta in MU_ETF_WEIGHTS.items():
            self.assertIn("mu_weight", meta)
            self.assertGreater(meta["mu_weight"], 0)

    def test_fmt_dollar(self):
        from alerts.etf_flows import _fmt_dollar
        self.assertEqual(_fmt_dollar(1.5e9), "$1.5B")
        self.assertEqual(_fmt_dollar(25e6), "$25.0M")

    @patch("alerts.etf_flows.save_state")
    @patch("alerts.etf_flows.load_state")
    @patch("alerts.etf_flows.send_alert")
    def test_no_alert_if_already_checked(self, mock_send, mock_load, mock_save):
        from alerts.etf_flows import check_etf_flows
        today = datetime.now().strftime("%Y-%m-%d")
        mock_load.return_value = {"last_date": today, "etf_data": {}, "alerts_sent": {}}
        check_etf_flows()
        mock_send.assert_not_called()


# ============================================================================
# Test Gap Monitor
# ============================================================================
class TestGapMonitor(unittest.TestCase):

    def test_imports(self):
        from alerts.gap_monitor import check_overnight_gaps, check_premarket_mu

    def test_determine_sentiment(self):
        from alerts.gap_monitor import _determine_sentiment
        self.assertIn("Bullish", _determine_sentiment({"NQ=F": 1.0, "ES=F": 0.8}, 1.5))
        self.assertIn("Bearish", _determine_sentiment({"NQ=F": -1.0, "ES=F": -0.8}, -1.5))
        self.assertIn("Flat", _determine_sentiment({"NQ=F": 0.1, "ES=F": -0.05}, 0.0))

    def test_estimate_mu_open(self):
        from alerts.gap_monitor import _estimate_mu_open
        low, high = _estimate_mu_open(400.0, 1.0, 1.5)
        self.assertGreater(low, 400)
        self.assertGreater(high, low)

    @patch("alerts.gap_monitor.save_state")
    @patch("alerts.gap_monitor.load_state")
    def test_no_alert_if_already_sent(self, mock_load, mock_save):
        from alerts.gap_monitor import check_overnight_gaps
        from zoneinfo import ZoneInfo
        today_sgt = datetime.now(ZoneInfo("Asia/Singapore")).strftime("%Y-%m-%d")
        mock_load.return_value = {"last_alert_date": today_sgt, "last_premarket_alert_date": today_sgt}
        with patch("alerts.gap_monitor.send_alert") as mock_send:
            check_overnight_gaps()
            mock_send.assert_not_called()


# ============================================================================
# Test Short Tracker
# ============================================================================
class TestShortTracker(unittest.TestCase):

    def test_imports(self):
        from alerts.short_tracker import check_short_updates, get_short_summary

    def test_fmt_number(self):
        from alerts.short_tracker import _fmt_number
        self.assertEqual(_fmt_number(45.2e6), "45.2M")
        self.assertEqual(_fmt_number(2.5e9), "2.5B")
        self.assertEqual(_fmt_number(1500), "1.5K")

    @patch("alerts.short_tracker.yf")
    def test_squeeze_potential_low(self, mock_yf):
        from alerts.short_tracker import _estimate_squeeze_potential
        mock_ticker = MagicMock()
        mock_hist = MagicMock()
        mock_hist.empty = True
        mock_hist.__len__ = lambda x: 0
        mock_ticker.history.return_value = mock_hist
        mock_yf.Ticker.return_value = mock_ticker

        result = _estimate_squeeze_potential({
            "short_ratio": 1.5,
            "short_pct_float": 3.0,
            "dtc_calc": 1.5,
        })
        self.assertEqual(result["potential"], "LOW")

    @patch("alerts.short_tracker.save_state")
    @patch("alerts.short_tracker.load_state")
    def test_no_alert_if_already_checked(self, mock_load, mock_save):
        from alerts.short_tracker import check_short_updates
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("US/Eastern")).strftime("%Y-%m-%d")
        mock_load.return_value = {"last_alert_date": today, "last_report_date": None, "prev_shares_short": None, "prev_short_pct": None, "history": []}
        with patch("alerts.short_tracker.send_alert") as mock_send:
            check_short_updates()
            mock_send.assert_not_called()


# ============================================================================
# Test Estimate Tracker
# ============================================================================
class TestEstimateTracker(unittest.TestCase):

    def test_imports(self):
        from alerts.estimate_tracker import check_estimate_changes, get_estimate_summary

    def test_estimate_tickers(self):
        from alerts.estimate_tracker import ESTIMATE_TICKERS
        self.assertIn("MU", ESTIMATE_TICKERS)
        self.assertIn("NVDA", ESTIMATE_TICKERS)

    def test_fmt_dollar(self):
        from alerts.estimate_tracker import _fmt_dollar
        self.assertEqual(_fmt_dollar(18.7e9), "$18.7B")

    @patch("alerts.estimate_tracker.yf")
    def test_get_estimates_handles_missing(self, mock_yf):
        from alerts.estimate_tracker import _get_estimates_for_ticker
        mock_ticker = MagicMock()
        mock_ticker.earnings_estimate = None
        mock_ticker.revenue_estimate = None
        mock_ticker.analyst_price_targets = None
        mock_ticker.info = {}
        mock_yf.Ticker.return_value = mock_ticker
        result = _get_estimates_for_ticker("MU")
        self.assertEqual(result["ticker"], "MU")
        self.assertIsNone(result["eps_current_avg"])

    @patch("alerts.estimate_tracker.save_state")
    @patch("alerts.estimate_tracker.load_state")
    def test_no_alert_if_already_checked(self, mock_load, mock_save):
        from alerts.estimate_tracker import check_estimate_changes
        today = datetime.now().strftime("%Y-%m-%d")
        mock_load.return_value = {"last_alert_date": today, "estimates": {}, "pt_data": {}}
        with patch("alerts.estimate_tracker.send_alert") as mock_send:
            check_estimate_changes()
            mock_send.assert_not_called()


# ============================================================================
# Test Geopolitical Monitor
# ============================================================================
class TestGeopoliticalMonitor(unittest.TestCase):

    def test_imports(self):
        from alerts.geopolitical_monitor import check_geopolitical_risks, get_geopolitical_summary

    def test_risk_categories_defined(self):
        from alerts.geopolitical_monitor import RISK_CATEGORIES
        self.assertIn("taiwan_strait", RISK_CATEGORIES)
        self.assertIn("middle_east", RISK_CATEGORIES)
        self.assertIn("us_china_chips", RISK_CATEGORIES)
        self.assertIn("oil_energy", RISK_CATEGORIES)

    def test_match_category(self):
        from alerts.geopolitical_monitor import _match_category
        matches = _match_category("China conducts military exercises near Taiwan Strait")
        self.assertIn("taiwan_strait", matches)

    def test_match_category_trade_war(self):
        from alerts.geopolitical_monitor import _match_category
        matches = _match_category("New tariff semiconductor imports from China")
        self.assertIn("trade_war", matches)

    def test_match_category_oil_energy(self):
        from alerts.geopolitical_monitor import _match_category
        matches = _match_category("G7 discusses strategic petroleum reserve release to stabilize oil prices")
        self.assertIn("oil_energy", matches)

    def test_match_category_oil_spr(self):
        from alerts.geopolitical_monitor import _match_category
        matches = _match_category("IEA coordinates emergency reserve release amid Hormuz crisis")
        self.assertIn("oil_energy", matches)

    def test_match_category_naval_escort(self):
        from alerts.geopolitical_monitor import _match_category
        matches = _match_category("France proposes naval escort convoys through Strait of Hormuz")
        self.assertIn("oil_energy", matches)

    def test_match_category_oil_price_level(self):
        from alerts.geopolitical_monitor import _match_category
        matches = _match_category("Crude oil surges past $110 as supply fears mount")
        self.assertIn("oil_energy", matches)

    def test_match_category_no_match(self):
        from alerts.geopolitical_monitor import _match_category
        matches = _match_category("Apple releases new iPhone with better camera")
        self.assertEqual(len(matches), 0)

    def test_geopolitical_summary_empty(self):
        from alerts.geopolitical_monitor import get_geopolitical_summary
        with patch("alerts.geopolitical_monitor.load_state") as mock_load:
            mock_load.return_value = {"active_risks": {}}
            result = get_geopolitical_summary()
            self.assertIn("No active alerts", result)


# ============================================================================
# Test Event Tracker
# ============================================================================
class TestEventTracker(unittest.TestCase):

    def test_imports(self):
        from alerts.event_tracker import check_live_events, check_event_countdown, add_event

    def test_tracked_events_defined(self):
        from alerts.event_tracker import TRACKED_EVENTS
        self.assertGreater(len(TRACKED_EVENTS), 0)
        for event in TRACKED_EVENTS:
            self.assertIn("name", event)
            self.assertIn("start", event)
            self.assertIn("keywords", event)
            self.assertIn("memory_keywords", event)

    def test_get_active_events_none(self):
        from alerts.event_tracker import _get_active_events
        # If no events are active today, should return empty list
        # (depends on current date vs hardcoded events)
        result = _get_active_events()
        self.assertIsInstance(result, list)

    def test_matches_keywords(self):
        from alerts.event_tracker import _matches_keywords
        matched = _matches_keywords("Jensen announced SOCAMM 2 with Micron LPDDR5X", ["socamm", "micron", "hbm"])
        self.assertIn("socamm", matched)
        self.assertIn("micron", matched)
        self.assertNotIn("hbm", matched)

    def test_content_hash_deterministic(self):
        from alerts.event_tracker import _content_hash
        h1 = _content_hash("test content here")
        h2 = _content_hash("test content here")
        self.assertEqual(h1, h2)

    def test_content_hash_normalization(self):
        from alerts.event_tracker import _content_hash
        h1 = _content_hash("Test  Content   Here")
        h2 = _content_hash("test content here")
        self.assertEqual(h1, h2)

    @patch("alerts.event_tracker.save_state")
    @patch("alerts.event_tracker.load_state")
    def test_add_event(self, mock_load, mock_save):
        from alerts.event_tracker import add_event
        mock_load.return_value = {"seen_hashes": [], "countdown_sent": {}, "custom_events": []}
        add_event("Test Event", "2026-06-01", "2026-06-03", ["test"], ["memory"])
        mock_save.assert_called_once()
        saved_state = mock_save.call_args[0][0]
        self.assertEqual(len(saved_state["custom_events"]), 1)
        self.assertEqual(saved_state["custom_events"][0]["name"], "Test Event")

    @patch("alerts.event_tracker.save_state")
    @patch("alerts.event_tracker.load_state")
    def test_add_event_no_duplicate(self, mock_load, mock_save):
        from alerts.event_tracker import add_event
        mock_load.return_value = {"seen_hashes": [], "countdown_sent": {}, "custom_events": [
            {"name": "Test Event", "start": "2026-06-01", "end": "2026-06-03", "keywords": ["test"], "memory_keywords": ["memory"]}
        ]}
        add_event("Test Event", "2026-06-01", "2026-06-03", ["test"], ["memory"])
        mock_save.assert_not_called()


# ============================================================================
# Test Weekend Crypto
# ============================================================================
class TestWeekendCrypto(unittest.TestCase):

    def test_imports(self):
        from alerts.weekend_crypto import check_weekend_crypto, get_crypto_sentiment

    def test_crypto_tickers_defined(self):
        from alerts.weekend_crypto import CRYPTO_TICKERS
        self.assertIn("BTC-USD", CRYPTO_TICKERS)
        self.assertIn("ETH-USD", CRYPTO_TICKERS)

    @patch("alerts.weekend_crypto.save_state")
    @patch("alerts.weekend_crypto.load_state")
    def test_skips_weekday(self, mock_load, mock_save):
        from alerts.weekend_crypto import check_weekend_crypto
        # Weekday should skip (function checks internally)
        mock_load.return_value = {"last_alert": None, "friday_closes": {}}
        check_weekend_crypto()
        # Won't error, just returns early on weekdays


# ============================================================================
# Test Sunday Futures
# ============================================================================
class TestSundayFutures(unittest.TestCase):

    def test_imports(self):
        from alerts.sunday_futures import check_sunday_futures_open, check_monday_asia_preview

    def test_asian_indices_defined(self):
        from alerts.sunday_futures import ASIAN_INDICES
        self.assertIn("^N225", ASIAN_INDICES)
        self.assertIn("000660.KS", ASIAN_INDICES)

    @patch("alerts.sunday_futures.save_state")
    @patch("alerts.sunday_futures.load_state")
    def test_skips_non_sunday(self, mock_load, mock_save):
        from alerts.sunday_futures import check_sunday_futures_open
        mock_load.return_value = {"last_futures_alert": None, "last_asia_monday_alert": None}
        check_sunday_futures_open()  # Will skip on non-Sunday


# ============================================================================
# Test Weekend Digest
# ============================================================================
class TestWeekendDigest(unittest.TestCase):

    def test_imports(self):
        from alerts.weekend_digest import send_weekend_digest, collect_weekend_headline

    @patch("alerts.weekend_digest.save_state")
    @patch("alerts.weekend_digest.load_state")
    def test_no_duplicate_digest(self, mock_load, mock_save):
        from alerts.weekend_digest import send_weekend_digest
        from zoneinfo import ZoneInfo
        week_key = datetime.now(ZoneInfo("Asia/Singapore")).strftime("%Y-W%W")
        mock_load.return_value = {"last_digest_week": week_key, "collected_headlines": []}
        with patch("alerts.weekend_digest.send_alert") as mock_send:
            send_weekend_digest()
            mock_send.assert_not_called()


# ============================================================================
# Test Week Ahead
# ============================================================================
class TestWeekAhead(unittest.TestCase):

    def test_imports(self):
        from alerts.week_ahead import send_week_ahead

    @patch("alerts.week_ahead.POSITION", TEST_POSITION)
    @patch("alerts.week_ahead.yf")
    def test_position_health(self, mock_yf):
        from alerts.week_ahead import _get_position_health
        mock_ticker = MagicMock()
        mock_ticker.info = {"regularMarketPreviousClose": 395.0, "currentPrice": 395.0}
        mock_yf.Ticker.return_value = mock_ticker
        result = _get_position_health()
        self.assertIn("current_price", result)
        self.assertIn("dte", result)
        self.assertIn("prob_above_short", result)

    @patch("alerts.week_ahead.save_state")
    @patch("alerts.week_ahead.load_state")
    def test_no_duplicate_preview(self, mock_load, mock_save):
        from alerts.week_ahead import send_week_ahead
        from zoneinfo import ZoneInfo
        week_key = datetime.now(ZoneInfo("Asia/Singapore")).strftime("%Y-W%W")
        mock_load.return_value = {"last_preview_week": week_key}
        with patch("alerts.week_ahead.send_alert") as mock_send:
            send_week_ahead()
            mock_send.assert_not_called()


# ============================================================================
# Test Weekend Social
# ============================================================================
class TestWeekendSocial(unittest.TestCase):

    def test_imports(self):
        from alerts.weekend_social import check_weekend_social

    def test_search_terms_defined(self):
        from alerts.weekend_social import SEARCH_TERMS
        self.assertIn("MU", SEARCH_TERMS)
        self.assertIn("micron", SEARCH_TERMS)

    @patch("alerts.weekend_social.save_state")
    @patch("alerts.weekend_social.load_state")
    def test_skips_non_sunday(self, mock_load, mock_save):
        from alerts.weekend_social import check_weekend_social
        mock_load.return_value = {"last_alert_week": None}
        check_weekend_social()  # Skips on non-Sunday


# ============================================================================
# Test Expiry Recap
# ============================================================================
class TestExpiryRecap(unittest.TestCase):

    def test_imports(self):
        from alerts.expiry_recap import send_expiry_recap

    @patch("alerts.expiry_recap.POSITION", TEST_POSITION)
    def test_spread_status(self):
        from alerts.expiry_recap import _get_spread_status
        mid = (TEST_POSITION["long_strike"] + TEST_POSITION["short_strike"]) / 2
        result = _get_spread_status(mid)
        self.assertIn("intrinsic", result)
        self.assertIn("total_pnl", result)
        self.assertEqual(result["intrinsic"], mid - TEST_POSITION["long_strike"])

    @patch("alerts.expiry_recap.POSITION", TEST_POSITION)
    def test_spread_status_below_long(self):
        from alerts.expiry_recap import _get_spread_status
        result = _get_spread_status(TEST_POSITION["long_strike"] - 10)
        self.assertEqual(result["intrinsic"], 0)

    @patch("alerts.expiry_recap.POSITION", TEST_POSITION)
    def test_spread_status_above_short(self):
        from alerts.expiry_recap import _get_spread_status
        width = TEST_POSITION["short_strike"] - TEST_POSITION["long_strike"]
        result = _get_spread_status(TEST_POSITION["short_strike"] + 20)
        self.assertEqual(result["intrinsic"], width)


# ============================================================================
# Test FX Monitor
# ============================================================================
class TestFXMonitor(unittest.TestCase):

    def test_imports(self):
        from alerts.fx_monitor import check_fx_moves, get_fx_summary

    def test_fx_pairs_defined(self):
        from alerts.fx_monitor import FX_PAIRS
        self.assertIn("JPY=X", FX_PAIRS)
        self.assertIn("KRW=X", FX_PAIRS)
        self.assertIn("DX-Y.NYB", FX_PAIRS)

    @patch("alerts.fx_monitor.save_state")
    @patch("alerts.fx_monitor.load_state")
    def test_no_duplicate_alert(self, mock_load, mock_save):
        from alerts.fx_monitor import check_fx_moves
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("Asia/Singapore")).strftime("%Y-%m-%d")
        mock_load.return_value = {"last_alert_date": today, "prev_prices": {}}
        with patch("alerts.fx_monitor.send_alert") as mock_send:
            check_fx_moves()
            mock_send.assert_not_called()


# ============================================================================
# Test Prediction Market
# ============================================================================
class TestPredictionMarket(unittest.TestCase):

    def test_imports(self):
        from alerts.prediction_market import check_prediction_markets, get_prediction_summary

    def test_relevant_keywords(self):
        from alerts.prediction_market import RELEVANT_KEYWORDS
        self.assertIn("fed rate", RELEVANT_KEYWORDS)
        self.assertIn("iran", RELEVANT_KEYWORDS)

    @patch("alerts.prediction_market.save_state")
    @patch("alerts.prediction_market.load_state")
    def test_no_duplicate_alert(self, mock_load, mock_save):
        from alerts.prediction_market import check_prediction_markets
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("Asia/Singapore")).strftime("%Y-%m-%d")
        mock_load.return_value = {"last_alert_date": today, "tracked_markets": {}}
        with patch("alerts.prediction_market.send_alert") as mock_send:
            check_prediction_markets()
            mock_send.assert_not_called()

    def test_prediction_summary_empty(self):
        from alerts.prediction_market import get_prediction_summary
        with patch("alerts.prediction_market.load_state") as mock_load:
            mock_load.return_value = {"tracked_markets": {}}
            result = get_prediction_summary()
            self.assertIn("No data", result)


# ============================================================================
# Test Memory Pricing
# ============================================================================
class TestMemoryPricing(unittest.TestCase):

    def test_imports(self):
        from alerts.memory_pricing import check_memory_pricing, get_memory_pricing_summary

    def test_memory_tickers_defined(self):
        from alerts.memory_pricing import MEMORY_TICKERS
        self.assertIn("MU", MEMORY_TICKERS)

    @patch("alerts.memory_pricing.save_state")
    @patch("alerts.memory_pricing.load_state")
    def test_no_duplicate_alert(self, mock_load, mock_save):
        from alerts.memory_pricing import check_memory_pricing
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("US/Eastern")).strftime("%Y-%m-%d")
        mock_load.return_value = {"last_alert_date": today, "price_history": {}}
        with patch("alerts.memory_pricing.send_alert") as mock_send:
            check_memory_pricing()
            mock_send.assert_not_called()


# ============================================================================
# Test Hyperscaler Tracker
# ============================================================================
class TestHyperscalerTracker(unittest.TestCase):

    def test_imports(self):
        from alerts.hyperscaler_tracker import check_hyperscaler_signals, get_hyperscaler_summary

    def test_hyperscaler_tickers_defined(self):
        from alerts.hyperscaler_tracker import HYPERSCALERS
        self.assertIn("MSFT", HYPERSCALERS)
        self.assertIn("GOOGL", HYPERSCALERS)
        self.assertIn("META", HYPERSCALERS)
        self.assertIn("AMZN", HYPERSCALERS)

    @patch("alerts.hyperscaler_tracker._save_state")
    @patch("alerts.hyperscaler_tracker._load_state")
    def test_no_duplicate_alert(self, mock_load, mock_save):
        from alerts.hyperscaler_tracker import check_hyperscaler_signals
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("US/Eastern")).strftime("%Y-%m-%d")
        mock_load.return_value = {
            "last_alert_date": today,
            "last_selloff_alert": today,
            "last_divergence_alert": today,
            "prev_data": {}, "prev_demand_score": None,
        }
        with patch("alerts.hyperscaler_tracker.send_alert") as mock_send:
            check_hyperscaler_signals()
            mock_send.assert_not_called()


# ============================================================================
# Test Supply Chain
# ============================================================================
class TestSupplyChain(unittest.TestCase):

    def test_imports(self):
        from alerts.supply_chain import check_supply_chain, get_supply_chain_summary

    def test_supply_chain_tickers_defined(self):
        from alerts.supply_chain import GAS_TICKERS, SHIPPING_TICKERS
        self.assertIn("APD", GAS_TICKERS)  # Air Products (helium)
        self.assertIn("LIN", GAS_TICKERS)  # Linde (gases)
        self.assertGreater(len(SHIPPING_TICKERS), 0)

    @patch("alerts.supply_chain._save_state")
    @patch("alerts.supply_chain._load_state")
    def test_no_duplicate_alert(self, mock_load, mock_save):
        from alerts.supply_chain import check_supply_chain
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("US/Eastern")).strftime("%Y-%m-%d")
        mock_load.return_value = {"last_check_date": today, "last_stress": None, "baselines": {}}
        with patch("alerts.supply_chain.send_alert") as mock_send:
            check_supply_chain()
            mock_send.assert_not_called()


# ============================================================================
# Test Geopolitical Monitor Enhancements
# ============================================================================
class TestGeopoliticalEnhancements(unittest.TestCase):

    def test_oil_energy_category_exists(self):
        from alerts.geopolitical_monitor import RISK_CATEGORIES
        self.assertIn("oil_energy", RISK_CATEGORIES)

    def test_chips_act_category_exists(self):
        from alerts.geopolitical_monitor import RISK_CATEGORIES
        self.assertIn("chips_act", RISK_CATEGORIES)

    def test_match_chips_act(self):
        from alerts.geopolitical_monitor import _match_category
        matches = _match_category("CHIPS Act funding cut threatens Micron Idaho fab")
        self.assertIn("chips_act", matches)

    def test_match_china_retaliation(self):
        from alerts.geopolitical_monitor import _match_category
        matches = _match_category("China launches cybersecurity review of Micron, threatens ban")
        self.assertIn("us_china_chips", matches)

    def test_match_taiwan_tsmc(self):
        from alerts.geopolitical_monitor import _match_category
        matches = _match_category("TSMC halts production amid Taiwan military drill escalation")
        self.assertIn("taiwan_strait", matches)


# ============================================================================
# Test Options Flow GEX Enhancement
# ============================================================================
class TestOptionsFlowGEX(unittest.TestCase):

    def test_gex_function_exists(self):
        from alerts.options_flow import _check_dealer_gamma
        self.assertTrue(callable(_check_dealer_gamma))


# ============================================================================
# Test Social Sentiment Retail Enhancement
# ============================================================================
class TestRetailPositioning(unittest.TestCase):

    def test_retail_function_exists(self):
        from alerts.social_sentiment import check_retail_positioning
        self.assertTrue(callable(check_retail_positioning))


# ============================================================================
# Test FX Monitor Carry Trade Enhancement
# ============================================================================
class TestFXCarryTrade(unittest.TestCase):

    def test_fx_monitor_has_carry_trade_logic(self):
        import inspect
        from alerts.fx_monitor import check_fx_moves
        source = inspect.getsource(check_fx_moves)
        self.assertIn("carry_trade", source.lower())


# ============================================================================
# Test Oil Tracker
# ============================================================================
class TestOilTracker(unittest.TestCase):

    def test_imports(self):
        from alerts.oil_tracker import check_oil_prices, get_oil_summary

    def test_oil_tickers_defined(self):
        from alerts.oil_tracker import OIL_TICKERS
        self.assertIn("CL=F", OIL_TICKERS)
        self.assertIn("BZ=F", OIL_TICKERS)

    def test_level_alerts_defined(self):
        from alerts.oil_tracker import LEVEL_ALERTS
        self.assertIn(100, LEVEL_ALERTS)
        self.assertIn(110, LEVEL_ALERTS)
        self.assertIn(120, LEVEL_ALERTS)

    def test_thresholds(self):
        from alerts.oil_tracker import MOVE_THRESHOLD_PCT, SPIKE_THRESHOLD_PCT
        self.assertEqual(MOVE_THRESHOLD_PCT, 3.0)
        self.assertEqual(SPIKE_THRESHOLD_PCT, 5.0)

    @patch("alerts.oil_tracker.save_state")
    @patch("alerts.oil_tracker.load_state")
    @patch("alerts.oil_tracker._get_oil_prices")
    @patch("alerts.oil_tracker.send_alert")
    def test_first_run_sets_baseline(self, mock_send, mock_prices, mock_load, mock_save):
        from alerts.oil_tracker import check_oil_prices
        mock_load.return_value = {
            "last_alert_prices": {},
            "last_alert_time": None,
            "crossed_levels": [],
            "session_high": None,
            "session_low": None,
            "session_date": None,
            "alert_count_today": 0,
            "alert_date": None,
        }
        mock_prices.return_value = {
            "CL=F": {"name": "WTI Crude", "price": 98.5, "prev_close": 95.0, "day_high": 100, "day_low": 96, "change_pct": 3.68},
        }
        check_oil_prices()
        mock_send.assert_not_called()  # First run just sets baseline
        mock_save.assert_called_once()
        saved = mock_save.call_args[0][0]
        self.assertEqual(saved["last_alert_prices"]["CL=F"], 98.5)

    @patch("alerts.oil_tracker.save_state")
    @patch("alerts.oil_tracker.load_state")
    @patch("alerts.oil_tracker._get_oil_prices")
    @patch("alerts.oil_tracker._get_oil_history")
    @patch("alerts.oil_tracker.send_alert")
    def test_spike_alert(self, mock_send, mock_history, mock_prices, mock_load, mock_save):
        from alerts.oil_tracker import check_oil_prices
        mock_load.return_value = {
            "last_alert_prices": {"CL=F": 100.0},
            "last_alert_time": None,
            "crossed_levels": [],
            "session_high": 100.0,
            "session_low": 98.0,
            "session_date": "2026-03-09",
            "alert_count_today": 0,
            "alert_date": "2026-03-09",
        }
        mock_prices.return_value = {
            "CL=F": {"name": "WTI Crude", "price": 113.0, "prev_close": 100.0, "day_high": 115, "day_low": 99, "change_pct": 13.0},
            "BZ=F": {"name": "Brent Crude", "price": 114.0, "prev_close": 101.0, "day_high": 116, "day_low": 100, "change_pct": 12.9},
        }
        mock_history.return_value = {"week_change_pct": 25.0, "month_change_pct": 60.0, "period_high": 119, "period_low": 70}
        check_oil_prices()
        mock_send.assert_called()
        # Multiple alerts may fire (level crossings + spike). Check any contains SURGING or OIL
        all_msgs = " ".join(call[0][0] for call in mock_send.call_args_list)
        self.assertIn("#OIL", all_msgs)
        self.assertTrue("SURGING" in all_msgs or "ABOVE" in all_msgs)

    @patch("alerts.oil_tracker._get_oil_prices")
    def test_get_oil_summary(self, mock_prices):
        from alerts.oil_tracker import get_oil_summary
        mock_prices.return_value = {
            "CL=F": {"name": "WTI Crude", "price": 98.5, "prev_close": 95.0, "change_pct": 3.68},
        }
        with patch("alerts.oil_tracker._get_oil_history") as mock_hist:
            mock_hist.return_value = {"week_change_pct": 15.0}
            result = get_oil_summary()
            self.assertIn("WTI", result)
            self.assertIn("98.5", result)


# ============================================================================
# Run all tests
# ============================================================================
if __name__ == "__main__":
    unittest.main(verbosity=2)
