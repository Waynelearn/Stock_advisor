"""Geopolitical risk monitor - semiconductor supply chain risk tracking."""

import json
import os
import re
import hashlib
import xml.etree.ElementTree as ET
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import POSITION, TZ_ET, TZ_SGT, DEEPSEEK_API_KEY
from .bot import send_alert

STATE_FILE = os.path.join(os.path.dirname(__file__), ".geopolitical_state.json")
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

RISK_CATEGORIES = {
    "taiwan_strait": {
        "keywords": [r"taiwan\s+strait", r"china\s+taiwan", r"\bpla\b", r"chinese\s+military.*taiwan",
                     r"tsmc\s+risk", r"taiwan\s+invasion", r"taiwan\s+blockade",
                     r"tsmc.*disrupt", r"tsmc.*halt", r"taiwan.*military\s+drill",
                     r"china.*taiwan.*exercise", r"micron.*taiwan\s+fab"],
        "label": "Taiwan Strait Tensions",
        "impact": "TSMC fab disruption + MU has 2 DRAM fabs in Taiwan - critical supply risk",
    },
    "korea_political": {
        "keywords": [r"south\s+korea\s+president", r"samsung\s+fab", r"sk\s+hynix.*disrupt",
                     r"korea\s+political", r"martial\s+law.*korea", r"korea.*instability"],
        "label": "Korea Political Risk",
        "impact": "SK Hynix/Samsung memory supply disruption",
    },
    "us_china_chips": {
        "keywords": [r"chip\s+export\s+control", r"entity\s+list.*chip", r"huawei\s+chip",
                     r"china\s+semiconductor\s+ban", r"chip\s+ban.*china", r"advanced\s+packaging.*china",
                     r"export\s+restriction.*semiconductor",
                     r"china.*ban.*micron", r"china.*cybersecurity.*review.*micron",
                     r"china.*retaliat", r"china.*sanction.*us\s+chip",
                     r"china.*iran.*support", r"china.*hormuz.*naval"],
        "label": "US-China Chip Controls / China Retaliation",
        "impact": "MU gets ~25% revenue from China. Ban/retaliation = devastating. Export controls reshape demand.",
    },
    "chips_act": {
        "keywords": [r"chips\s+act.*fund", r"chips\s+act.*cut", r"chips\s+act.*defund",
                     r"chips\s+act.*delay", r"chips\s+act.*micron", r"chips\s+act.*grant",
                     r"semiconductor\s+subsid", r"fab\s+subsid", r"chips\s+act.*billion",
                     r"chips\s+act.*budget", r"chips\s+act.*appropriat"],
        "label": "CHIPS Act Funding Risk",
        "impact": "MU receiving billions in CHIPS grants for Idaho/Virginia fabs. Defunding = capex risk.",
    },
    "middle_east": {
        "keywords": [r"iran\s+israel", r"strait\s+of\s+hormuz", r"oil\s+price\s+spike",
                     r"middle\s+east\s+war", r"iran\s+nuclear", r"iran.*attack", r"israel.*iran",
                     r"iran.*ceasefire", r"iran.*surrender", r"iran.*supreme\s+leader",
                     r"hormuz.*escort", r"hormuz.*convoy", r"hormuz.*reopen",
                     r"iran.*irgc", r"hezbollah.*rocket", r"hezbollah.*israel",
                     r"iran.*oil\s+infrastructure", r"iran.*refinery", r"iran.*missile",
                     # Escalation signals
                     r"iran.*ground\s+(invasion|troops|forces)", r"iran.*nuclear\s+weapon",
                     r"iran.*breakout", r"iran.*enrichment", r"kharg\s+island",
                     r"iran.*tanker\s+(attack|hit|struck|sunk)", r"iran.*mine\s+(lay|warfare|sweep)",
                     r"iran.*drone\s+strike", r"iran.*retaliat", r"iran.*escalat",
                     r"qatar.*attack", r"qatar.*strike", r"qatar.*lng", r"qatar.*helium",
                     r"uae.*attack", r"uae.*strike", r"saudi.*attack", r"saudi.*strike",
                     r"us\s+troops.*iran", r"marines.*iran", r"marines.*middle\s+east",
                     r"us\s+casualties.*iran", r"american.*killed.*iran",
                     # De-escalation signals
                     r"iran.*ceasefire", r"iran.*peace\s+(talk|deal|negotiat)",
                     r"iran.*truce", r"iran.*de.escalat", r"iran.*diplomacy",
                     r"china.*mediat.*iran", r"china.*broker.*iran",
                     r"hormuz.*open", r"hormuz.*traffic", r"hormuz.*ship.*pass",
                     r"iran.*war.*end", r"iran.*war.*over", r"iran.*withdraw"],
        "label": "Middle East Conflict",
        "impact": "Oil spike -> inflation -> rate hike risk -> growth stocks down. Helium/LNG supply chain risk to chip fabs.",
    },
    "oil_energy": {
        "keywords": [r"strategic\s+petroleum\s+reserve", r"\bspr\b", r"oil\s+reserve\s+release",
                     r"g7.*oil", r"g7.*reserve", r"g7.*petroleum", r"iea.*reserve",
                     r"emergency\s+oil", r"crude\s+oil.*surge", r"crude\s+oil.*spike",
                     r"crude\s+oil.*crash", r"crude\s+oil.*drop", r"oil.*\$1[0-9]{2}",
                     r"brent.*surge", r"wti.*surge", r"brent.*spike", r"wti.*spike",
                     r"opec.*cut", r"opec.*production", r"saudi.*oil.*production",
                     r"naval\s+escort", r"ship\s+escort.*hormuz", r"tanker.*escort",
                     r"oil.*embargo", r"energy\s+crisis", r"oil.*stagflation",
                     r"oil.*supply\s+disrupt", r"oil.*shortage"],
        "label": "Oil / Energy Crisis",
        "impact": "Oil price drives inflation expectations, Fed policy, and tech multiples",
    },
    "trade_war": {
        "keywords": [r"tariff\s+semiconductor", r"section\s+232.*chip", r"chip\s+tariff",
                     r"trade\s+war.*tech", r"semiconductor\s+tariff", r"tariff.*memory"],
        "label": "Trade War / Tariffs",
        "impact": "Direct cost impact on semiconductor imports/exports",
    },
    "helium_materials": {
        "keywords": [r"helium\s+shortage", r"helium\s+supply", r"helium\s+crisis",
                     r"helium\s+price", r"helium\s+ration", r"helium\s+allocation",
                     r"qatar.*helium", r"helium.*qatar", r"helium.*semiconductor",
                     r"helium.*chip", r"helium.*fab", r"helium.*wafer",
                     r"neon\s+gas.*shortage", r"neon.*semiconductor",
                     r"air\s+products.*helium", r"\bapd\b.*helium",
                     r"industrial\s+gas.*shortage", r"fab.*gas\s+supply",
                     r"helium.*stockpile", r"helium.*reserve",
                     r"lng.*chip", r"lng.*semiconductor", r"lng.*fab"],
        "label": "Helium / Critical Materials",
        "impact": "Helium essential for chip fab (wafer cooling, leak detection, lithography). Qatar=30%+ global supply, offline due to Iran war. 2-week fab stockpile clock.",
    },
}

GEO_RSS_FEEDS = [
    # Ticker-specific
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=TSM&region=US&lang=en-US",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=MU&region=US&lang=en-US",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=SOXX&region=US&lang=en-US",
    # Oil / Energy
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=CL%3DF&region=US&lang=en-US",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=BZ%3DF&region=US&lang=en-US",
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=USO&region=US&lang=en-US",
    # Broad geopolitical / macro
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EDJI&region=US&lang=en-US",
    # BBC world news
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    # BBC Middle East (dedicated)
    "https://feeds.bbci.co.uk/news/world/middle_east/rss.xml",
    # Al Jazeera (best Middle East coverage)
    "https://www.aljazeera.com/xml/rss/all.xml",
    # NPR world
    "https://feeds.npr.org/1004/rss.xml",
    # CNBC world news
    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100727362",
    # CNBC energy
    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19836768",
    # Reuters world (via Google News RSS proxy)
    "https://news.google.com/rss/search?q=iran+war+2026&hl=en-US&gl=US&ceid=US:en",
    # Google News - Iran war specific
    "https://news.google.com/rss/search?q=iran+hormuz+oil+ceasefire&hl=en-US&gl=US&ceid=US:en",
    # Google News - helium semiconductor
    "https://news.google.com/rss/search?q=helium+shortage+semiconductor&hl=en-US&gl=US&ceid=US:en",
    # Google News - Qatar energy
    "https://news.google.com/rss/search?q=qatar+energy+attack+lng&hl=en-US&gl=US&ceid=US:en",
    # Google News - Middle East conflict
    "https://news.google.com/rss/search?q=iran+israel+war+strike&hl=en-US&gl=US&ceid=US:en",
    # OilPrice.com (energy market)
    "https://oilprice.com/rss/main",
]

MAX_ALERTS_PER_DAY = 15  # Active Iran war — user wants real-time escalation/de-escalation tracking


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "seen_hashes": [],
        "active_risks": {},
        "alert_count_today": 0,
        "alert_date": None,
    }


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _hash_headline(text: str) -> str:
    return hashlib.md5(text.lower().strip().encode()).hexdigest()[:12]


def _fetch_headlines() -> list:
    """Fetch headlines from RSS feeds."""
    headlines = []
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}

    for feed_url in GEO_RSS_FEEDS:
        try:
            resp = requests.get(feed_url, headers=headers, timeout=10)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)

            for item in root.iter("item"):
                title = item.findtext("title", "").strip()
                link = item.findtext("link", "").strip()
                desc = item.findtext("description", "").strip()
                if title:
                    headlines.append({
                        "title": title,
                        "link": link,
                        "description": desc,
                        "hash": _hash_headline(title),
                    })
        except Exception:
            continue

    return headlines


def _match_category(text: str) -> list:
    """Match text against risk categories. Returns list of matched category keys."""
    text_lower = text.lower()
    matches = []
    for cat_key, cat in RISK_CATEGORIES.items():
        for pattern in cat["keywords"]:
            if re.search(pattern, text_lower):
                matches.append(cat_key)
                break
    return matches


def _deepseek_analyze(headline: str, category: str) -> dict:
    """Use DeepSeek to analyze geopolitical headline for MU impact."""
    cat_info = RISK_CATEGORIES.get(category, {})
    prompt = f"""Analyze this geopolitical headline for semiconductor/memory stock impact.

Headline: "{headline}"
Category: {cat_info.get('label', category)}
Known impact channel: {cat_info.get('impact', 'Unknown')}

Respond in this EXACT JSON format:
{{
    "summary": "1-2 sentence summary of the development",
    "severity": <1-5 integer, 5 being most severe>,
    "escalation_probability": "<low/medium/high> - likelihood this escalates further",
    "mu_impact": "2-3 sentences on how this specifically impacts Micron (MU). Consider: direct fab risk, supply chain, memory pricing, demand shifts, market sentiment"
}}

Only return the JSON, nothing else."""

    try:
        headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
        payload = {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 300,
            "temperature": 0.3,
        }
        resp = requests.post(DEEPSEEK_URL, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()

        # Parse JSON from response
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
    except Exception:
        pass

    return {
        "summary": headline,
        "severity": 2,
        "escalation_probability": "unknown",
        "mu_impact": "Unable to assess — review manually",
    }


def check_geopolitical_risks():
    """Main function - scan headlines for geopolitical risks affecting semis."""
    state = load_state()
    today = datetime.now(ZoneInfo(TZ_ET)).strftime("%Y-%m-%d")

    # Reset daily counter
    if state.get("alert_date") != today:
        state["alert_count_today"] = 0
        state["alert_date"] = today

    if state["alert_count_today"] >= MAX_ALERTS_PER_DAY:
        return

    # Trim old hashes (keep last 500)
    state["seen_hashes"] = state.get("seen_hashes", [])[-500:]

    headlines = _fetch_headlines()

    for h in headlines:
        if h["hash"] in state["seen_hashes"]:
            continue

        state["seen_hashes"].append(h["hash"])

        # Check both title and description
        full_text = f"{h['title']} {h['description']}"
        matched_cats = _match_category(full_text)

        if not matched_cats:
            continue

        # Use primary (first) matched category
        primary_cat = matched_cats[0]
        cat_info = RISK_CATEGORIES[primary_cat]

        # AI analysis
        analysis = _deepseek_analyze(h["title"], primary_cat)
        severity = analysis.get("severity", 2)

        # Alert on severity >= 2 for middle_east/helium (active war tracking)
        # severity >= 3 for other categories
        is_new_category = primary_cat not in state.get("active_risks", {})
        is_war_category = primary_cat in ("middle_east", "oil_energy", "helium_materials")
        min_severity = 2 if is_war_category else 3
        if severity < min_severity and not is_new_category:
            continue

        if state["alert_count_today"] >= MAX_ALERTS_PER_DAY:
            break

        # Determine risk level change
        prev_severity = state.get("active_risks", {}).get(primary_cat, {}).get("severity", 0)
        if prev_severity > 0:
            if severity > prev_severity:
                risk_change = f"ESCALATED (was: {prev_severity}/5)"
            elif severity < prev_severity:
                risk_change = f"DE-ESCALATED (was: {prev_severity}/5)"
            else:
                risk_change = f"UNCHANGED (still {severity}/5)"
        else:
            risk_change = "NEW RISK ACTIVATED"

        severity_label = {1: "LOW", 2: "MODERATE", 3: "ELEVATED", 4: "HIGH", 5: "CRITICAL"}

        msg = f"""GEOPOLITICAL RISK ALERT

Category: {cat_info['label']}
Severity: {severity}/5 ({severity_label.get(severity, 'UNKNOWN')})

Development:
{analysis.get('summary', h['title'])}

Escalation Probability: {analysis.get('escalation_probability', 'unknown').upper()}

MU Impact Assessment:
{analysis.get('mu_impact', 'Review manually')}

Risk Level: {risk_change}

#GEOPOLITICAL"""

        send_alert(msg)

        # Update state
        if "active_risks" not in state:
            state["active_risks"] = {}
        state["active_risks"][primary_cat] = {
            "severity": severity,
            "last_headline": h["title"],
            "last_date": today,
        }
        state["alert_count_today"] += 1

    save_state(state)


def get_geopolitical_summary() -> str:
    """Return current geopolitical risk dashboard for other modules."""
    state = load_state()
    active = state.get("active_risks", {})

    if not active:
        return "Geopolitical Risk: No active alerts"

    lines = ["Geopolitical Risk Dashboard:"]
    severity_label = {1: "LOW", 2: "MOD", 3: "ELEV", 4: "HIGH", 5: "CRIT"}

    for cat_key, data in sorted(active.items(), key=lambda x: x[1].get("severity", 0), reverse=True):
        cat_info = RISK_CATEGORIES.get(cat_key, {})
        sev = data.get("severity", 0)
        label = severity_label.get(sev, "?")
        lines.append(f"  [{label}] {cat_info.get('label', cat_key)} - {data.get('last_date', '?')}")

    return "\n".join(lines)
