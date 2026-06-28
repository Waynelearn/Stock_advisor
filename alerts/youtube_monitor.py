"""YouTube financial news monitor - scrapes transcripts and summarizes relevant videos.

Monitors 63 financial YouTube channels via RSS feeds (no API key needed).
Only alerts on NEW videos published since last check (no backfill).

Transcript fetching (3-tier fallback):
1. YouTube transcript API (fastest, but blocked on some VPS IPs)
2. yt-dlp audio download + faster-whisper local transcription (reliable)
3. Title-only alert with keywords (when audio unavailable)

Broad keyword coverage: semiconductors, macro, geopolitics, rates, jobs, war, tariffs.
"""

import json
import os
import subprocess
import tempfile
import requests
from datetime import datetime, timedelta
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from .config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL_FAST, TZ_SGT, position_summary
from .llm import ask
from .bot import send_alert

STATE_FILE = os.path.join(os.path.dirname(__file__), ".youtube_state.json")
COOKIES_FILE = os.path.join(os.path.dirname(__file__), ".yt_cookies.txt")
WHISPER_MODEL = "tiny"  # tiny=fast, base=better accuracy; loads on first use

# Financial YouTube channels: (channel_id, name) - curated for MU/semi/macro relevance
# General news channels removed (too much noise). Only financial/market-focused kept.
CHANNELS = [
    # === Financial News (7) ===
    ("UCqGH6GRWhBEtc14Vuc0vYCw", "Bloomberg"),
    ("UCvJJ_dzjViJCoLf5uKUTwoA", "CNBC"),
    ("UCrp_UI8XtuYfpiqluWLD7Lw", "CNBC Television"),
    ("UCEAZeUIeJs0IjQiqTCdVSIg", "Yahoo Finance"),
    ("UCqoSrYgusd8ZddtMoWhjHYA", "Schwab Network"),
    ("UChqUTb7kYRX8-EiaN3XFrSQ", "Reuters"),
    ("UCK7tptUDHh-RYDsdxO1-5QQ", "Wall Street Journal"),

    # === Finance & Markets (9) ===
    ("UCPaSu8qnjJhF1vkXVOGojBQ", "MarketWatch"),
    ("UC0p5jTq6Xx_DosDFxVXnWaQ", "The Economist"),
    ("UCoUxsWakJucWg46KW5RsvPw", "Financial Times"),
    ("UCmh7afBz-uWwOSSNTqUBAhg", "Forbes"),
    ("UCvwFhI0mrIWDiZUabRapS5Q", "Investopedia"),
    ("UCOfQuCs1g5_wjr3GPKKYSoA", "Barron's"),
    ("UCcyq283he07B7_KUX07mmtA", "Business Insider"),
    ("UCpRQuynBX9Qy9tPrcswpPag", "The Motley Fool"),
    ("UCZ4AMrDcNrfy3X6nsU8-rPg", "Economics Explained"),

    # === Wall Street & Institutional (4) ===
    ("UCz6RzD6KG_hH_oHb2kyW5jQ", "Morgan Stanley"),
    ("UC-hKNOj4P-Y8hR9QEHpZPig", "Bridgewater Associates"),
    ("UCtHZ1qs5h4sx9TijVBQCMIA", "Bank of America"),
    ("UCGXWKlq1Oxr3ddEtmKhAkPg", "Real Vision"),

    # === Investing & Analysis (7) ===
    ("UCLvnJL8htRR1T9cbSccaoVw", "Aswath Damodaran"),
    ("UCFCEuCsyWP0YkP3CZ3Mr01Q", "The Plain Bagel"),
    ("UChBVf9YnourrEDTsbbwJPRA", "Everything Money"),
    ("UCUvvj5lwue7PspotMDjk5UA", "Meet Kevin"),
    ("UCGy7SkBjcIAgTiwkXEtPnYg", "Andrei Jikh"),
    ("UCLJiSMXJ9K-1AOTqIqdXJgQ", "Tastylive"),
    ("UC6IGAbJq4yTLHcIPEuzB97A", "Trader TV"),

    # === AI Labs & AI News (7) ===
    ("UCZzz69u3MGBmJ3APUTyyXPA", "DeepSeek"),
    ("UCXZCJLdBC09xxGZ6gcdrc6A", "OpenAI"),
    ("UCP7jMXSY2xbc3KCAE0MHQ-A", "Google DeepMind"),
    ("UCHuiy8bXnmK5nisYHUd1J5g", "NVIDIA"),
    ("UCBHcMCGaiJhv-ESTcWGJPcw", "NVIDIA Developer"),
    ("UCNJ1Ymd5yFuUPtn21xtRbbw", "AI Explained"),
    ("UCawZsQWqfGSbCI5yjkdVkTA", "Matthew Berman"),

    # === Tech (3) ===
    ("UCddiUEpeqJcYeBxX1IVBKvQ", "The Verge"),
    ("UCCjyq_K1Xwfg8Lndy7lKMpA", "TechCrunch"),
    ("UCCDU1fsmgvWljcW2aodfJsA", "Ars Technica"),
]

# --- KEYWORD TIERS ---
# Tier 1: Direct MU/memory relevance (always alert)
TIER1_KEYWORDS = [
    "micron", " mu ", "dram", "nand", "hbm", "high bandwidth memory",
    "memory chip", "memory market", "memory demand",
]

# Tier 2: Semiconductor sector
TIER2_KEYWORDS = [
    "semiconductor", "semicon", " chip ", "chips act", "chip stock", "chip war",
    "nvidia", "nvda", " amd ", " intel ", "asml", "sk hynix", "samsung semi",
    "tsmc", "broadcom", "avgo", "marvell", "mrvl",
    "soxx", "smh", "chip etf", "semi stock",
    "fab ", "foundry", "wafer",
    "ai chip", " gpu", "data center",
    "blackwell", "rubin", "hopper",
    # AI demand signals (only terms that imply GPU/memory demand, not general AI news)
    "deepseek", "gpt-5", "gpt-4o",
    "ai chip", "ai training", "ai inference", "ai demand", "ai spending",
    "ai capex", "ai infrastructure", "ai data center",
    "large language model", "llm ", " llm",
]

# Tier 3: Macro events with MARKET context (must appear in title alongside market terms)
# These only match in titles, not transcripts, so they must be specific enough
TIER3_KEYWORDS = [
    # Rates & Fed (always market-relevant)
    "fed rate", "fomc", "rate decision", "rate cut", "rate hike",
    "powell", "federal reserve", "dot plot",
    # Economic data releases (specific)
    "jobs report", "nfp", "payroll", "cpi report", "ppi report",
    "retail sales", "gdp report",
    # Trade & tariffs (specific to tech/markets)
    "chip tariff", "semi tariff", "tech tariff", "export control",
    "china ban", "section 232", "trade war",
    # Market-specific geopolitical (only when title explicitly connects to markets/stocks)
    "oil price", "oil spike", "market crash", "stock crash",
    "tech sell", "tech rally", "market rally", "market plunge",
    "bear market", "recession risk", "soft landing",
    # Earnings
    "tech earnings", "earnings season",
]

# Max audio duration to download (seconds) - skip very long videos
MAX_AUDIO_DURATION = 1200  # 20 minutes
# Only process videos published within this window
MAX_VIDEO_AGE_HOURS = 3
# Max videos to alert per cycle (prevents flooding)
MAX_ALERTS_PER_CYCLE = 5


def load_state() -> dict:
    from .state_utils import safe_load_state
    return safe_load_state(STATE_FILE, {"seen_ids": [], "initialized": False})


def save_state(state: dict):
    state["seen_ids"] = state["seen_ids"][-500:]
    from .state_utils import safe_save_state
    safe_save_state(STATE_FILE, state)


def _fetch_channel_feed(channel_id: str) -> list[dict]:
    """Fetch recent videos from a YouTube channel RSS feed."""
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        root = ElementTree.fromstring(resp.text)

        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "yt": "http://www.youtube.com/xml/schemas/2015",
        }

        videos = []
        for entry in root.findall("atom:entry", ns):
            video_id = entry.find("yt:videoId", ns)
            title = entry.find("atom:title", ns)
            published = entry.find("atom:published", ns)
            author = entry.find("atom:author/atom:name", ns)

            if video_id is None or title is None:
                continue

            pub_dt = None
            if published is not None and published.text:
                try:
                    pub_dt = datetime.fromisoformat(published.text.replace("Z", "+00:00"))
                except Exception:
                    pass

            videos.append({
                "video_id": video_id.text,
                "title": title.text,
                "published": pub_dt.isoformat() if pub_dt else None,
                "channel": author.text if author is not None else "Unknown",
            })
        return videos
    except Exception:
        # Silently skip — YouTube feeds return 404/500 frequently
        return []


def _is_relevant(title: str, transcript_text: str = "") -> tuple[bool, int, list[str]]:
    """Check if a video is relevant. Returns (is_relevant, tier, matched_keywords)."""
    title_lower = f" {title.lower()} "
    transcript_lower = transcript_text.lower()[:5000] if transcript_text else ""
    matched = []

    # Tier 1: Direct MU - title OR transcript
    for kw in TIER1_KEYWORDS:
        if kw in title_lower or (transcript_lower and kw in transcript_lower):
            matched.append(kw.strip())
    if matched:
        return True, 1, list(set(matched))

    # Tier 2: Semi sector - in title
    for kw in TIER2_KEYWORDS:
        if kw in title_lower:
            matched.append(kw.strip())
    if matched:
        return True, 2, list(set(matched))

    # Tier 2 via transcript: need 2+ different keywords
    if transcript_lower:
        t2_transcript = []
        for kw in TIER2_KEYWORDS:
            if kw in transcript_lower:
                t2_transcript.append(kw.strip())
        if len(set(t2_transcript)) >= 2:
            return True, 2, list(set(t2_transcript))

    # Tier 3: Macro - title only, need 2+ keywords OR 1 high-impact keyword
    HIGH_IMPACT_T3 = {"fomc", "fed rate", "rate decision", "dot plot", "nfp", "cpi report",
                       "market crash", "stock crash", "tech sell"}
    matched = []
    for kw in TIER3_KEYWORDS:
        if kw in title_lower:
            matched.append(kw.strip())
    unique = list(set(matched))
    if len(unique) >= 2 or any(kw in HIGH_IMPACT_T3 for kw in unique):
        return True, 3, unique

    return False, 0, []


def _fetch_transcript(video_id: str) -> str | None:
    """Fetch transcript with multi-tier fallback:
    1. yt-dlp subtitle download (fast, uses cookies, no audio needed)
    2. YouTube transcript API (fast, but often IP-blocked on VPS)
    3. yt-dlp audio + faster-whisper local transcription (heavy, last resort)
    4. None (caller falls back to title-only alert)
    """
    # Tier 1: yt-dlp subtitle download (fast, reliable with cookies)
    transcript = _fetch_via_ytdlp_subs(video_id)
    if transcript:
        return transcript

    # Tier 2: YouTube transcript API
    transcript = _fetch_yt_transcript_api(video_id)
    if transcript:
        return transcript

    # Tier 3: yt-dlp audio download + whisper (heavy, last resort)
    transcript = _fetch_via_whisper(video_id)
    if transcript:
        return transcript

    return None


def _get_ytdlp_env() -> tuple[str | None, dict]:
    """Get yt-dlp binary path and env with deno on PATH."""
    import shutil
    yt_dlp = shutil.which("yt-dlp")
    if not yt_dlp:
        yt_dlp = os.path.expanduser("~/miniconda3/envs/mu_advisor/bin/yt-dlp")
    if not os.path.exists(yt_dlp):
        return None, {}

    env = os.environ.copy()
    deno_path = os.path.expanduser("~/.deno/bin")
    if os.path.isdir(deno_path):
        env["PATH"] = deno_path + ":" + env.get("PATH", "")
    return yt_dlp, env


def _fetch_via_ytdlp_subs(video_id: str) -> str | None:
    """Download auto-generated subtitles via yt-dlp (no audio, fast)."""
    import re
    try:
        yt_dlp, env = _get_ytdlp_env()
        if not yt_dlp:
            return None

        with tempfile.TemporaryDirectory() as tmpdir:
            out_tpl = os.path.join(tmpdir, "subs")
            url = f"https://www.youtube.com/watch?v={video_id}"

            cmd = [
                yt_dlp,
                "--no-playlist",
                "--write-auto-sub",
                "--sub-lang", "en",
                "--skip-download",
                "--sub-format", "vtt",
                "--remote-components", "ejs:github",
                "-o", out_tpl,
                url,
            ]
            if os.path.exists(COOKIES_FILE):
                cmd.insert(1, "--cookies")
                cmd.insert(2, COOKIES_FILE)

            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60, env=env,
            )

            # Find the .vtt file
            vtt_files = [f for f in os.listdir(tmpdir) if f.endswith(".vtt")]
            if not vtt_files:
                if result.returncode != 0:
                    print(f"[YT-SUBS] {video_id}: {result.stderr[:150]}")
                return None

            vtt_path = os.path.join(tmpdir, vtt_files[0])
            with open(vtt_path) as f:
                content = f.read()

            # Parse VTT: strip timestamps, formatting tags, dedup lines
            lines = content.split("\n")
            seen_lines = set()
            clean = []
            for line in lines:
                line = line.strip()
                if not line or line == "WEBVTT" or "-->" in line:
                    continue
                if line.startswith("Kind:") or line.startswith("Language:"):
                    continue
                # Strip VTT formatting tags like <00:00:01.234><c>
                line = re.sub(r"<[^>]+>", "", line)
                line = line.strip()
                if line and line not in seen_lines:
                    seen_lines.add(line)
                    clean.append(line)

            text = " ".join(clean)
            return text if len(text) > 50 else None

    except subprocess.TimeoutExpired:
        print(f"[YT-SUBS TIMEOUT] {video_id}")
        return None
    except Exception as e:
        print(f"[YT-SUBS ERROR] {video_id}: {e}")
        return None


def _fetch_yt_transcript_api(video_id: str) -> str | None:
    """Try YouTube's built-in transcript/captions API."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        ytt_api = YouTubeTranscriptApi()
        transcript = ytt_api.fetch(video_id, languages=["en"])
        full_text = " ".join(snippet.text for snippet in transcript.snippets)
        return full_text if full_text.strip() else None
    except Exception:
        return None


def _fetch_via_whisper(video_id: str) -> str | None:
    """Download audio with yt-dlp, transcribe with faster-whisper, delete file."""
    try:
        yt_dlp, env = _get_ytdlp_env()
        if not yt_dlp:
            return None

        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = os.path.join(tmpdir, "audio.m4a")
            url = f"https://www.youtube.com/watch?v={video_id}"

            cmd = [
                yt_dlp,
                "--no-playlist",
                "--extract-audio",
                "--audio-format", "m4a",
                "--audio-quality", "worst",
                "--max-filesize", "50m",
                "--match-filter", f"duration<={MAX_AUDIO_DURATION}",
                "--remote-components", "ejs:github",
                "-o", audio_path,
                url,
            ]
            if os.path.exists(COOKIES_FILE):
                cmd.insert(1, "--cookies")
                cmd.insert(2, COOKIES_FILE)

            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120, env=env,
            )

            if result.returncode != 0 or not os.path.exists(audio_path):
                print(f"[YT-DLP] {video_id}: {result.stderr[:200]}")
                return None

            model = _get_whisper_model()
            segments, info = model.transcribe(
                audio_path, language="en", beam_size=1, vad_filter=True,
            )
            text = " ".join(seg.text.strip() for seg in segments if seg.text.strip())

            if not text.strip():
                segments, info = model.transcribe(
                    audio_path, language="en", beam_size=1, vad_filter=False,
                )
                text = " ".join(
                    seg.text.strip() for seg in segments
                    if seg.text.strip() and seg.text.strip().lower() not in ("music", "[music]")
                )

            return text if text.strip() else None

    except subprocess.TimeoutExpired:
        print(f"[YT-DLP TIMEOUT] {video_id}")
        return None
    except Exception as e:
        print(f"[WHISPER ERROR] {video_id}: {e}")
        return None


# Lazy-loaded whisper model singleton
_whisper_model = None

def _get_whisper_model():
    """Load whisper model once, reuse across calls."""
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        _whisper_model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    return _whisper_model


def _summarize_video(title: str, channel: str, transcript: str, tier: int, matched_keywords: list[str]) -> str:
    """Use DeepSeek to summarize video relevance to MU position."""
    transcript_trimmed = transcript[:4000]

    tier_context = {
        1: "This video DIRECTLY mentions Micron/memory chips.",
        2: "This video covers the semiconductor sector.",
        3: "This video covers macro/geopolitical events affecting markets.",
    }

    prompt = (
        f"Summarize this YouTube video for a trader holding "
        f"{position_summary()}.\n\n"
        f"VIDEO: \"{title}\" by {channel}\n"
        f"CONTEXT: {tier_context.get(tier, '')}\n"
        f"KEYWORDS: {', '.join(matched_keywords)}\n\n"
        f"TRANSCRIPT:\n{transcript_trimmed}\n\n"
        f"FORMAT (under 120 words):\n"
        f"KEY POINTS:\n- point 1\n- point 2\n- point 3\n\n"
        f"IMPACT ON MU: HIGH/MEDIUM/LOW - one sentence on what this means for the spread\n\n"
        f"UPCOMING EVENT: If the video mentions a specific upcoming event with a date "
        f"(earnings, product launch, conference, policy decision, data release), "
        f"write: EVENT: MM/DD - description. Otherwise skip this line.\n\n"
        f"Extract only market-moving information. Skip ads, intros, unrelated segments."
    )

    try:
        summary = ask(prompt, tier="fast", temperature=0.2, max_tokens=3000,
                      label="youtube") or "Summary unavailable"

        # Extract discovered catalyst if present
        _extract_catalyst_from_summary(summary)

        return summary
    except Exception as e:
        return f"Summary unavailable: {e}"


def _extract_catalyst_from_summary(summary: str):
    """Parse EVENT: MM/DD - description from YouTube summary and add to catalyst list."""
    import re
    match = re.search(r'EVENT:\s*(\d{1,2})/(\d{1,2})\s*[-\u2013]\s*(.+?)(?:\n|$)', summary)
    if not match:
        return
    try:
        month = int(match.group(1))
        day = int(match.group(2))
        event = match.group(3).strip()
        if 1 <= month <= 12 and 1 <= day <= 31 and len(event) > 3:
            from .catalyst_fetcher import add_discovered_catalyst
            add_discovered_catalyst(month, day, event)
    except Exception:
        pass


def check_youtube_news():
    """Main entry point - check channels for NEW relevant videos only.

    On first run, marks all current videos as seen without alerting (no backfill).
    Subsequent runs only alert on videos published after the last check.
    """
    state = load_state()
    seen_ids = set(state.get("seen_ids", []))
    first_run = not state.get("initialized", False)

    # Collect videos from all channels
    all_videos = []
    for channel_id, channel_name in CHANNELS:
        videos = _fetch_channel_feed(channel_id)
        for v in videos:
            v["channel_name"] = channel_name
            all_videos.append(v)

    # De-duplicate by video_id
    unique = {}
    for v in all_videos:
        if v["video_id"] not in unique:
            unique[v["video_id"]] = v
    all_videos = list(unique.values())

    if first_run:
        # First run: mark everything as seen, no alerts
        for v in all_videos:
            seen_ids.add(v["video_id"])
        state["seen_ids"] = list(seen_ids)
        state["initialized"] = True
        save_state(state)
        print(f"[YOUTUBE] Initialized - marked {len(all_videos)} existing videos as seen")
        return

    # Filter to only new videos published within MAX_VIDEO_AGE_HOURS
    now_utc = datetime.now(ZoneInfo("UTC"))
    cutoff = now_utc - timedelta(hours=MAX_VIDEO_AGE_HOURS)

    new_videos = []
    for v in all_videos:
        if v["video_id"] in seen_ids:
            continue
        # Filter by age - skip videos older than cutoff
        if v.get("published"):
            try:
                pub_dt = datetime.fromisoformat(v["published"])
                if pub_dt < cutoff:
                    seen_ids.add(v["video_id"])  # Mark old videos as seen
                    continue
            except Exception:
                pass
        new_videos.append(v)

    if not new_videos:
        return

    # First pass: check title relevance (fast, no API calls)
    title_matched = []
    title_unmatched = []
    for v in new_videos:
        relevant, tier, matched = _is_relevant(v["title"])
        if relevant:
            v["tier"] = tier
            v["matched"] = matched
            title_matched.append(v)
        else:
            title_unmatched.append(v)

    # Sort title-matched by tier (1=most relevant first)
    title_matched.sort(key=lambda v: v.get("tier", 99))

    # Cap alerts per cycle to prevent flooding
    alerts_sent = 0
    transcript_blocked = False  # Skip transcript attempts after first failure
    for v in title_matched:
        seen_ids.add(v["video_id"])

        if alerts_sent >= MAX_ALERTS_PER_CYCLE:
            continue  # Still mark as seen but don't alert

        transcript = None
        if not transcript_blocked:
            transcript = _fetch_transcript(v["video_id"])
            if not transcript:
                transcript_blocked = True  # Don't waste time on further attempts this cycle

        if transcript:
            summary = _summarize_video(
                v["title"], v.get("channel_name", v["channel"]),
                transcript, v["tier"], v["matched"],
            )
            _send_video_alert(v, summary)
        else:
            _send_title_only_alert(v)
        alerts_sent += 1

    # Mark ALL new videos as seen (even unprocessed) to avoid reprocessing
    for v in new_videos:
        seen_ids.add(v["video_id"])

    state["seen_ids"] = list(seen_ids)
    state["initialized"] = True
    save_state(state)


def _send_video_alert(video: dict, summary: str):
    """Send Telegram alert for a relevant video."""
    tier_label = {1: "DIRECT MU", 2: "SEMI SECTOR", 3: "MACRO/GEO"}
    tier = video.get("tier", 0)
    tier_text = tier_label.get(tier, "RELEVANT")
    icon = {1: "\U0001f534", 2: "\U0001f7e1", 3: "\U0001f535"}.get(tier, "\u26aa")

    url = f"https://www.youtube.com/watch?v={video['video_id']}"
    channel = video.get("channel_name", video.get("channel", ""))

    pub = ""
    if video.get("published"):
        try:
            dt = datetime.fromisoformat(video["published"])
            pub = dt.astimezone(ZoneInfo("Asia/Singapore")).strftime("%H:%M SGT")
        except Exception:
            pass

    msg = (
        f"\U0001f3ac {icon} <b>YOUTUBE: {tier_text}</b>\n"
        f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
        f"\U0001f4fa <b>{_escape_html(video['title'])}</b>\n"
        f"\U0001f4e1 {_escape_html(channel)}"
        f"{f' | {pub}' if pub else ''}\n\n"
        f"{summary}\n\n"
        f"\U0001f517 <a href=\"{url}\">Watch Video</a>\n\n"
        f"<code>#YOUTUBE</code>"
    )
    send_alert(msg)


def _send_title_only_alert(video: dict):
    """Send alert based on title keywords when no transcript available."""
    tier_label = {1: "DIRECT MU", 2: "SEMI SECTOR", 3: "MACRO/GEO"}
    tier = video.get("tier", 0)
    tier_text = tier_label.get(tier, "RELEVANT")
    icon = {1: "\U0001f534", 2: "\U0001f7e1", 3: "\U0001f535"}.get(tier, "\u26aa")

    url = f"https://www.youtube.com/watch?v={video['video_id']}"
    keywords = ", ".join(video.get("matched", []))
    channel = video.get("channel_name", video.get("channel", ""))

    pub = ""
    if video.get("published"):
        try:
            dt = datetime.fromisoformat(video["published"])
            pub = dt.astimezone(ZoneInfo("Asia/Singapore")).strftime("%H:%M SGT")
        except Exception:
            pass

    msg = (
        f"\U0001f3ac {icon} <b>YOUTUBE: {tier_text}</b>\n"
        f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
        f"\U0001f4fa <b>{_escape_html(video['title'])}</b>\n"
        f"\U0001f4e1 {_escape_html(channel)}"
        f"{f' | {pub}' if pub else ''}\n"
        f"\U0001f50d {keywords}\n\n"
        f"\U0001f517 <a href=\"{url}\">Watch Video</a>\n\n"
        f"<code>#YOUTUBE</code>"
    )
    send_alert(msg)


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


if __name__ == "__main__":
    check_youtube_news()
