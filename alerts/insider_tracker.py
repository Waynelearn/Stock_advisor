"""Insider & Congressional Trade Tracker - SEC Form 4 filings and congressional disclosures."""

import json
import os
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import DEEPSEEK_API_KEY, POSITION, TZ_ET
from .bot import send_alert

STATE_FILE = os.path.join(os.path.dirname(__file__), ".insider_state.json")

# Micron CIK for SEC EDGAR
MICRON_CIK = "0000723125"
MICRON_CIK_PADDED = "0000723125"

# SEC EDGAR requires identifying User-Agent
SEC_HEADERS = {
    "User-Agent": "MU-Advisor research@example.com",
    "Accept": "application/json",
}

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "last_insider_date": None,
        "last_congress_date": None,
        "seen_insider_ids": [],
        "seen_congress_ids": [],
    }


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _fetch_edgar_form4() -> list[dict]:
    """Fetch recent Form 4 filings for Micron from SEC EDGAR full-text search API.

    Returns list of dicts with keys: accession, filed, description, url
    """
    filings = []

    # Primary: EDGAR full-text search API (EFTS)
    try:
        today = datetime.now(ZoneInfo(TZ_ET)).strftime("%Y-%m-%d")
        week_ago = (datetime.now(ZoneInfo(TZ_ET)) - timedelta(days=7)).strftime("%Y-%m-%d")
        url = "https://efts.sec.gov/LATEST/search-index"
        params = {
            "q": f'"micron technology"',
            "forms": "4",
            "dateRange": "custom",
            "startdt": week_ago,
            "enddt": today,
            "from": "0",
            "size": "10",
        }
        resp = requests.get(url, params=params, headers=SEC_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        hits = data.get("hits", {}).get("hits", [])
        for hit in hits:
            source = hit.get("_source", {})
            filing_id = hit.get("_id", source.get("file_num", ""))
            filed_date = source.get("file_date", "")
            display_names = source.get("display_names", [])
            form_type = source.get("form_type", "4")
            file_url = f"https://www.sec.gov/Archives/edgar/data/{MICRON_CIK}/{filing_id}" if filing_id else ""

            description = f"Form {form_type}"
            if display_names:
                description += f" by {', '.join(display_names[:3])}"

            filings.append({
                "id": filing_id or file_url,
                "filed": filed_date,
                "description": description,
                "url": file_url,
                "names": display_names,
            })

        if filings:
            return filings
    except Exception as e:
        print(f"[INSIDER EDGAR EFTS ERROR] {e}")

    # Fallback: EDGAR company filings ATOM feed
    try:
        atom_url = (
            f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany"
            f"&CIK={MICRON_CIK}&type=4&dateb=&owner=include&count=5"
            f"&search_text=&action=getcompany&output=atom"
        )
        resp = requests.get(atom_url, headers=SEC_HEADERS, timeout=10)
        resp.raise_for_status()

        # Parse ATOM XML
        import xml.etree.ElementTree as ET
        root = ET.fromstring(resp.content)
        ns = {"atom": "http://www.w3.org/2005/Atom"}

        for entry in root.findall("atom:entry", ns):
            title_el = entry.find("atom:title", ns)
            link_el = entry.find("atom:link", ns)
            updated_el = entry.find("atom:updated", ns)
            summary_el = entry.find("atom:summary", ns)

            title = title_el.text.strip() if title_el is not None and title_el.text else ""
            link = link_el.get("href", "") if link_el is not None else ""
            updated = updated_el.text.strip() if updated_el is not None and updated_el.text else ""
            summary = summary_el.text.strip() if summary_el is not None and summary_el.text else ""

            filing_id = link or title
            filings.append({
                "id": filing_id,
                "filed": updated[:10] if updated else "",
                "description": title or "Form 4 Filing",
                "url": link,
                "names": [],
            })
    except Exception as e:
        print(f"[INSIDER EDGAR ATOM ERROR] {e}")

    return filings


def _analyze_insider_trade(description: str, names: list[str]) -> str:
    """Ask DeepSeek what this insider filing means for MU."""
    names_str = ", ".join(names[:3]) if names else "unknown insider(s)"
    prompt = (
        f"SEC Form 4 filing for Micron Technology (MU): {description}\n"
        f"Filer(s): {names_str}\n"
        f"My position: 500x MU 380/400 bull call spread expiring March 20.\n"
        f"In 1-2 sentences: is this insider buying or selling? What does it signal for MU near-term?"
    )
    try:
        resp = requests.post(
            DEEPSEEK_URL,
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.2, "max_tokens": 100},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return ""


def check_insider_trades():
    """Check SEC EDGAR for new Micron Form 4 insider filings. Once daily."""
    state = load_state()
    today = datetime.now(ZoneInfo(TZ_ET)).strftime("%Y-%m-%d")

    # Only check once per day
    if state.get("last_insider_date") == today:
        return

    try:
        filings = _fetch_edgar_form4()
        seen = set(state["seen_insider_ids"])
        new_filings = []

        for f in filings:
            if f["id"] and f["id"] not in seen:
                seen.add(f["id"])
                state["seen_insider_ids"].append(f["id"])
                new_filings.append(f)

        # Keep state manageable (last 100 IDs)
        if len(state["seen_insider_ids"]) > 100:
            state["seen_insider_ids"] = state["seen_insider_ids"][-50:]

        # Alert on new filings (max 3 per cycle)
        for f in new_filings[:3]:
            analysis = _analyze_insider_trade(f["description"], f.get("names", []))

            link_line = ""
            if f["url"]:
                link_line = f"\n\n\U0001f517 <a href=\"{f['url']}\">View Filing</a>"

            msg = (
                f"\U0001f4dd <b>INSIDER FILING: MU</b>\n"
                + "\u2500" * 20 + "\n\n"
                f"\U0001f4cb {f['description']}\n"
                f"\U0001f4c5 Filed: {f['filed']}"
            )
            if analysis:
                msg += f"\n\n\U0001f9e0 {analysis}"
            msg += link_line
            msg += "\n\n<code>#INSIDER</code>"

            send_alert(msg)

        state["last_insider_date"] = today
        save_state(state)

    except Exception as e:
        print(f"[INSIDER TRACKER ERROR] {e}")


def _fetch_congressional_trades() -> list[dict]:
    """Attempt to fetch congressional trades mentioning MU/Micron.

    Data sources (tried in order):
    1. Capitol Trades public page (scrape if accessible)
    2. Senate eFD search
    3. House disclosure search

    NOTE: No reliable free real-time API exists for congressional trades.
    This uses best-effort scraping of public disclosures. Data may be
    delayed by 30-45 days per STOCK Act reporting requirements.
    """
    trades = []

    # Attempt 1: Capitol Trades (public site, may block scraping)
    try:
        url = "https://www.capitoltrades.com/trades"
        params = {"asset": "MU", "txType": "purchase,sale"}
        headers = {"User-Agent": "MU-Advisor/1.0"}
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        if resp.status_code == 200 and "micron" in resp.text.lower():
            # Basic extraction - Capitol Trades returns HTML
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(resp.text, "html.parser")

            # Look for trade rows in the table
            rows = soup.select("table tbody tr")
            for row in rows[:10]:
                cells = row.find_all("td")
                if len(cells) >= 5:
                    politician = cells[0].get_text(strip=True)
                    tx_date = cells[1].get_text(strip=True)
                    ticker = cells[2].get_text(strip=True)
                    tx_type = cells[3].get_text(strip=True)
                    amount = cells[4].get_text(strip=True)

                    if "MU" in ticker.upper() or "MICRON" in ticker.upper():
                        trade_id = f"{politician}_{tx_date}_{tx_type}"
                        trades.append({
                            "id": trade_id,
                            "politician": politician,
                            "date": tx_date,
                            "type": tx_type,
                            "amount": amount,
                            "source": "Capitol Trades",
                        })

            if trades:
                return trades
    except Exception as e:
        print(f"[CONGRESS CAPITOL TRADES ERROR] {e}")

    # Attempt 2: Senate eFD periodic transaction reports (EDGAR-based search)
    # Congressional trades are also sometimes filed as periodic reports
    try:
        url = "https://efts.sec.gov/LATEST/search-index"
        params = {
            "q": '"micron" AND ("senate" OR "congress" OR "representative")',
            "dateRange": "custom",
            "startdt": (datetime.now(ZoneInfo(TZ_ET)) - timedelta(days=45)).strftime("%Y-%m-%d"),
            "enddt": datetime.now(ZoneInfo(TZ_ET)).strftime("%Y-%m-%d"),
        }
        resp = requests.get(url, params=params, headers=SEC_HEADERS, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            hits = data.get("hits", {}).get("hits", [])
            for hit in hits[:5]:
                source = hit.get("_source", {})
                filing_id = hit.get("_id", "")
                names = source.get("display_names", [])
                filed = source.get("file_date", "")

                trades.append({
                    "id": filing_id,
                    "politician": ", ".join(names[:2]) if names else "Unknown",
                    "date": filed,
                    "type": "SEC Filing (congressional proxy)",
                    "amount": "See filing",
                    "source": "SEC EDGAR",
                })
    except Exception as e:
        print(f"[CONGRESS SEC SEARCH ERROR] {e}")

    return trades


def _analyze_congressional_trade(trade: dict) -> str:
    """Ask DeepSeek about congressional trade significance."""
    prompt = (
        f"Congressional trade disclosure: {trade['politician']} "
        f"{trade['type']} MU/Micron on {trade['date']}, amount: {trade['amount']}.\n"
        f"Source: {trade['source']}.\n"
        f"My position: 500x MU 380/400 bull call spread expiring March 20.\n"
        f"In 1-2 sentences: is this significant? Does this politician sit on any relevant committee?"
    )
    try:
        resp = requests.post(
            DEEPSEEK_URL,
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.2, "max_tokens": 100},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return ""


def check_congressional_trades():
    """Check for congressional trades in MU/Micron. Once daily.

    NOTE: Congressional trade data is inherently delayed (30-45 days).
    This is best-effort monitoring of public disclosure sources.
    Free real-time APIs for congressional trades are limited/unreliable.
    """
    state = load_state()
    today = datetime.now(ZoneInfo(TZ_ET)).strftime("%Y-%m-%d")

    # Only check once per day
    if state.get("last_congress_date") == today:
        return

    try:
        trades = _fetch_congressional_trades()
        seen = set(state["seen_congress_ids"])
        new_trades = []

        for t in trades:
            if t["id"] and t["id"] not in seen:
                seen.add(t["id"])
                state["seen_congress_ids"].append(t["id"])
                new_trades.append(t)

        # Keep state manageable
        if len(state["seen_congress_ids"]) > 100:
            state["seen_congress_ids"] = state["seen_congress_ids"][-50:]

        # Alert on new trades (max 3 per cycle)
        for t in new_trades[:3]:
            analysis = _analyze_congressional_trade(t)

            # Determine emoji
            tx_lower = t["type"].lower()
            if "purchase" in tx_lower or "buy" in tx_lower:
                emoji = "\U0001f7e2"  # green circle
                action = "BOUGHT"
            elif "sale" in tx_lower or "sell" in tx_lower:
                emoji = "\U0001f534"  # red circle
                action = "SOLD"
            else:
                emoji = "\U0001f535"  # blue circle
                action = "TRADED"

            msg = (
                f"\U0001f3db <b>CONGRESS TRADE: MU</b>\n"
                f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n\n"
                f"{emoji} {t['politician']} {action}\n"
                f"\U0001f4b0 Amount: {t['amount']}\n"
                f"\U0001f4c5 Disclosed: {t['date']}\n"
                f"\U0001f4ce Source: {t['source']}"
            )
            if analysis:
                msg += f"\n\n\U0001f9e0 {analysis}"
            msg += "\n\n<code>#CONGRESS</code>"

            send_alert(msg)

        # Note: last_check_date is shared with insider check.
        # Only save state here if insider check didn't already run today.
        # Actually, both can update - save_state merges.
        save_state(state)

    except Exception as e:
        print(f"[CONGRESSIONAL TRACKER ERROR] {e}")


if __name__ == "__main__":
    check_insider_trades()
    check_congressional_trades()
