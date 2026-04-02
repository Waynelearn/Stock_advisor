"""Economic & Earnings Report Scraper + DeepSeek Analysis.

Auto-scrapes actual reports:
  - CPI: BLS press release (bls.gov/news.release/cpi.nr0.htm)
  - NFP: BLS employment situation (bls.gov/news.release/empsit.nr0.htm)
  - FOMC: Federal Reserve statement + compares with previous (federalreserve.gov)
  - MU EARNINGS: Micron IR press release (investors.micron.com)
"""

import json
import os
import re
import requests
from bs4 import BeautifulSoup
from .config import DEEPSEEK_API_KEY, POSITION
from .bot import send_alert

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

FOMC_STATE_FILE = os.path.join(os.path.dirname(__file__), ".fomc_state.json")


# ============================================================================
# Report Scrapers
# ============================================================================

def fetch_cpi_report() -> str | None:
    """Scrape the latest CPI press release from BLS."""
    try:
        resp = requests.get(
            "https://www.bls.gov/news.release/cpi.nr0.htm",
            headers=HEADERS, timeout=15,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # CPI reports are in <pre> tags on BLS
        pre = soup.find("pre")
        if pre:
            return pre.get_text()[:4000]

        # Fallback: grab all paragraphs
        paras = soup.find_all("p")
        text = "\n".join(p.get_text(strip=True) for p in paras if len(p.get_text(strip=True)) > 30)
        return text[:4000] if text else None
    except Exception as e:
        print(f"[CPI SCRAPE ERROR] {e}")
        return None


def fetch_nfp_report() -> str | None:
    """Scrape the latest NFP (Employment Situation) from BLS."""
    try:
        resp = requests.get(
            "https://www.bls.gov/news.release/empsit.nr0.htm",
            headers=HEADERS, timeout=15,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        pre = soup.find("pre")
        if pre:
            return pre.get_text()[:4000]

        paras = soup.find_all("p")
        text = "\n".join(p.get_text(strip=True) for p in paras if len(p.get_text(strip=True)) > 30)
        return text[:4000] if text else None
    except Exception as e:
        print(f"[NFP SCRAPE ERROR] {e}")
        return None


def fetch_fomc_statement() -> tuple[str | None, str | None]:
    """Scrape the latest FOMC statement and the previous one for diff.

    Returns: (latest_statement, previous_statement)
    """
    try:
        # Get FOMC calendar page to find statement links
        resp = requests.get(
            "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
            headers=HEADERS, timeout=15,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Find all FOMC statement URLs (pattern: /monetary<YYYYMMDD>a.htm)
        statement_urls = []
        for a in soup.find_all("a", href=True):
            m = re.search(r"/monetary(\d{8})a\.htm", a["href"])
            if m:
                date_str = m.group(1)
                href = a["href"]
                full = ("https://www.federalreserve.gov" + href) if href.startswith("/") else href
                if full not in [u for _, u in statement_urls]:
                    statement_urls.append((date_str, full))

        statement_urls.sort(key=lambda x: x[0], reverse=True)

        if not statement_urls:
            return None, None

        latest = _scrape_fomc_page(statement_urls[0][1])
        previous = _scrape_fomc_page(statement_urls[1][1]) if len(statement_urls) > 1 else None

        # Cache latest for future comparisons
        if latest:
            try:
                with open(FOMC_STATE_FILE, "w") as f:
                    json.dump({"latest_url": statement_urls[0][1], "latest_text": latest[:2000]}, f)
            except Exception:
                pass

        return latest, previous
    except Exception as e:
        print(f"[FOMC SCRAPE ERROR] {e}")
        return None, None


def _scrape_fomc_page(url: str) -> str | None:
    """Scrape a single FOMC statement page."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove nav/header/footer noise
        for tag in soup(["nav", "header", "footer", "script", "style", "aside"]):
            tag.decompose()

        # Find the article content - Fed pages use col-xs-12 or article div
        article = soup.find("div", id="article")
        if not article:
            article = soup.find("div", class_="col-xs-12 col-sm-8 col-md-8")
        if not article:
            article = soup

        paras = article.find_all("p")
        text = "\n".join(
            p.get_text(strip=True) for p in paras
            if len(p.get_text(strip=True)) > 20
            and "official website" not in p.get_text().lower()
            and ".gov" not in p.get_text()[:30].lower()
        )
        return text[:3000] if text else None
    except Exception:
        return None


def fetch_mu_earnings_report() -> str | None:
    """Scrape the latest Micron earnings press release from IR page."""
    try:
        # Get the quarterly results page which lists all earnings releases
        resp = requests.get(
            "https://investors.micron.com/quarterly-results",
            headers=HEADERS, timeout=15,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Find the first "View Press Release" link (most recent earnings)
        release_url = None
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True).lower()
            href = a["href"]
            if "reports-results" in href and ("press" in text or "release" in text or "view" in text):
                release_url = href if href.startswith("http") else "https://investors.micron.com" + href
                break

        if not release_url:
            # Fallback: search latest-news page
            resp2 = requests.get(
                "https://investors.micron.com/latest-news",
                headers=HEADERS, timeout=15,
            )
            soup2 = BeautifulSoup(resp2.text, "html.parser")
            for a in soup2.find_all("a", href=True):
                if "reports-results" in a["href"]:
                    release_url = a["href"]
                    if not release_url.startswith("http"):
                        release_url = "https://investors.micron.com" + release_url
                    break

        if not release_url:
            return None

        # Fetch the actual press release
        resp3 = requests.get(release_url, headers=HEADERS, timeout=15)
        resp3.raise_for_status()
        soup3 = BeautifulSoup(resp3.text, "html.parser")

        # Remove noise
        for tag in soup3(["nav", "header", "footer", "script", "style"]):
            tag.decompose()

        # Extract paragraphs
        paras = soup3.find_all("p")
        text = "\n".join(p.get_text(strip=True) for p in paras if len(p.get_text(strip=True)) > 20)

        # Also extract tables (financial data is in HTML tables)
        tables_text = ""
        for table in soup3.find_all("table"):
            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all(["td", "th"])
                # Collapse cells, filter out empty
                cell_texts = [c.get_text(strip=True) for c in cells]
                cell_texts = [c for c in cell_texts if c]
                row_text = " | ".join(cell_texts)
                if row_text:
                    tables_text += row_text + "\n"

        # Prioritize: tables first (has actual numbers), then paragraphs (context)
        if tables_text:
            combined = "FINANCIAL DATA:\n" + tables_text[:3000] + "\n\nCOMMENTARY:\n" + text[:2500]
        else:
            combined = text if text else soup3.get_text(separator="\n", strip=True)

        return combined[:6000] if combined else None
    except Exception as e:
        print(f"[MU EARNINGS SCRAPE ERROR] {e}")
        return None


def fetch_latest_economic_data(event_type: str) -> str | None:
    """Dispatch to the right scraper based on event type."""
    if event_type == "CPI":
        return fetch_cpi_report()
    elif event_type == "NFP":
        return fetch_nfp_report()
    elif event_type == "FOMC":
        latest, _ = fetch_fomc_statement()
        return latest
    elif event_type == "EARNINGS":
        return fetch_mu_earnings_report()
    return None


# ============================================================================
# DeepSeek Analysis
# ============================================================================

def _roundtable_implications(event_type: str, initial_analysis: str, raw_data: str):
    """Follow-up roundtable: 9 personas debate implications of a report."""
    prompt = (
        f"You are 9 expert personas debating the implications of a just-released {event_type} report "
        f"for a trader holding 500x MU 380/400 bull call spread, expiry March 20, 2026.\n\n"
        f"INITIAL ANALYSIS:\n{initial_analysis[:1500]}\n\n"
        f"RAW DATA:\n{raw_data[:1000]}\n\n"
        f"PERSONAS (each gives 1-2 sentences):\n"
        f"- Rex (Bull): Why this is good for MU. Upside target.\n"
        f"- Vera (Bear): What could go wrong. Downside risk.\n"
        f"- Sigma (Quant): Probability the spread profits. Key number.\n"
        f"- Atlas (Macro): How this changes the macro picture for semis.\n"
        f"- Chart (Tech): Key price levels to watch now.\n"
        f"- Flux (Regime): Does this shift market regime (risk-on/off)?\n"
        f"- Edge (Flow): How will options/institutional flow react?\n"
        f"- Catalyst (Events): What comes next? Next catalyst?\n"
        f"- Arbiter (Judge): Weighs all views. Final VERDICT with specific action.\n\n"
        f"Keep under 250 words total. Be specific with numbers and levels."
    )

    try:
        resp = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.3, "max_tokens": 400},
            timeout=25,
        )
        resp.raise_for_status()
        discussion = resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        discussion = f"Roundtable unavailable: {e}"

    msg = (
        f"\U0001f9e0 <b>{event_type} ROUNDTABLE</b>\n"
        f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n\n"
        f"{discussion}\n\n"
        f"<code>#ECON_RT</code>"
    )
    send_alert(msg)


def analyze_economic_release(event_type: str, scraped_data: str = None):
    """Analyze an economic data release with full 9-persona roundtable."""
    data_context = ""
    if scraped_data:
        data_context = f"\nACTUAL REPORT DATA:\n{scraped_data[:2000]}\n"

    prompt = (
        f"You are running a roundtable of 9 expert personas analyzing a just-released {event_type} report "
        f"and its impact on Micron (MU) and semiconductors.\n\n"
        f"PERSONAS:\n"
        f"- Rex (Bull): finds upside\n"
        f"- Vera (Bear): identifies risks\n"
        f"- Sigma (Quant): numbers and probability\n"
        f"- Atlas (Macro): Fed policy, rates, macro flows\n"
        f"- Chart (Tech): price levels, support/resistance\n"
        f"- Flux (Market Regime): risk-on/off shift\n"
        f"- Edge (Flow/Sentiment): positioning, narrative\n"
        f"- Catalyst (Events): what comes next\n"
        f"- Arbiter (Judge): weighs all views, final verdict\n\n"
        f"POSITION: 500x MU 380/400 bull call spread, expiry March 20, 2026.\n"
        f"{data_context}\n"
        f"CRITICAL INSTRUCTIONS:\n"
        f"- Extract ONLY numbers that appear in the report text above\n"
        f"- Do NOT guess or hallucinate consensus estimates. If you don't know consensus, say so\n"
        f"- Focus on what the report ACTUALLY says: month-over-month, year-over-year changes\n"
        f"- For CPI: extract MoM%, YoY%, core MoM%, core YoY% from the report text\n"
        f"- For NFP: extract total nonfarm payrolls change, unemployment rate from the report text\n\n"
        f"FORMAT (Telegram, keep under 250 words):\n"
        f"1. KEY NUMBERS: Extract the actual data points from the report\n"
        f"2. ASSESSMENT: Hot/cold/inline based on the numbers and recent trend\n"
        f"3. One line from each persona on what it means for MU\n"
        f"4. Arbiter's VERDICT: immediate impact on MU/semis and what to do\n"
        f"Be specific about numbers. Only use figures from the report data."
    )

    try:
        resp = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.2, "max_tokens": 400},
            timeout=25,
        )
        resp.raise_for_status()
        analysis = resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        analysis = f"Analysis unavailable: {e}"

    emoji_map = {
        "CPI": "\U0001f4ca", "NFP": "\U0001f4bc",
        "FOMC": "\U0001f3e6", "EARNINGS": "\U0001f4b0",
    }
    emoji = emoji_map.get(event_type, "\U0001f4c8")

    title_map = {
        "CPI": "CPI REPORT ANALYSIS",
        "NFP": "JOBS REPORT ANALYSIS",
        "FOMC": "FOMC DECISION ANALYSIS",
        "EARNINGS": "MU EARNINGS ANALYSIS",
    }
    title = title_map.get(event_type, f"{event_type} ANALYSIS")

    source_note = ""
    if scraped_data:
        source_note = "\n<i>Source: scraped from official report</i>\n"

    msg = (
        f"{emoji} <b>{title}</b>\n"
        f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
        f"{source_note}\n"
        f"{analysis}\n\n"
        f"<code>#ECON</code>"
    )
    send_alert(msg)

    # Follow-up: roundtable debate on implications
    _roundtable_implications(event_type, analysis, scraped_data[:2000] if scraped_data else "")


def analyze_fomc_with_diff():
    """Scrape latest + previous FOMC statements, diff them, and analyze."""
    latest, previous = fetch_fomc_statement()
    if not latest:
        send_alert("\U0001f3e6 <b>FOMC</b>\nCould not scrape the latest FOMC statement.")
        return

    diff_context = ""
    if previous:
        diff_context = f"\nPREVIOUS STATEMENT:\n{previous[:1500]}\n"

    prompt = (
        f"You are a Fed watcher analyzing the new FOMC statement by comparing it to the previous one.\n\n"
        f"NEW STATEMENT:\n{latest[:1500]}\n"
        f"{diff_context}\n"
        f"POSITION: 500x MU 380/400 bull call spread, expiry March 20, 2026.\n\n"
        f"FORMAT (under 200 words):\n"
        f"1. KEY CHANGES: What specific words/phrases changed vs previous statement?\n"
        f"2. RATE DECISION: What did they decide? Target range?\n"
        f"3. DOT PLOT SIGNAL: Hawkish or dovish shift? How many cuts priced?\n"
        f"4. IMPACT ON MU: How does this affect semis/memory specifically?\n"
        f"5. ACTION: What to do with the 380/400 spread?\n"
        f"Be specific about language changes."
    )

    try:
        resp = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.2, "max_tokens": 300},
            timeout=20,
        )
        resp.raise_for_status()
        analysis = resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        analysis = "FOMC analysis unavailable."

    msg = (
        f"\U0001f3e6 <b>FOMC STATEMENT ANALYSIS</b>\n"
        f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
        f"<i>Source: scraped from federalreserve.gov</i>\n\n"
        f"{analysis}\n\n"
        f"<code>#FOMC</code>"
    )
    send_alert(msg)

    # Follow-up: roundtable debate on implications
    _roundtable_implications("FOMC", analysis, latest[:2000])


def _fetch_earnings_context() -> str:
    """Fetch earnings history, GAAP financials, and forward estimates from yfinance."""
    try:
        import yfinance as yf
        mu = yf.Ticker("MU")
        lines = []

        # GAAP financials from quarterly income statement
        inc = mu.quarterly_income_stmt
        gaap_data = {}
        if inc is not None:
            for row_name in ["Total Revenue", "Gross Profit", "Net Income", "Diluted EPS"]:
                if row_name in inc.index:
                    gaap_data[row_name] = inc.loc[row_name]

        # Non-GAAP earnings history: actual vs estimate
        eh = mu.earnings_history
        if eh is not None and not eh.empty:
            lines.append("QUARTERLY RESULTS (last 4 quarters):")
            for idx, row in eh.iterrows():
                qtr = idx.strftime("%Y-%m-%d")
                ng_actual = row.get("epsActual", 0)
                ng_est = row.get("epsEstimate", 0)
                surprise = row.get("surprisePercent", 0) * 100
                beat = "BEAT" if ng_actual > ng_est else ("MISS" if ng_actual < ng_est else "INLINE")

                lines.append(f"\n  Q ending {qtr}:")

                # GAAP figures from income statement
                if "Diluted EPS" in gaap_data and idx in gaap_data["Diluted EPS"].index:
                    gaap_eps = gaap_data["Diluted EPS"][idx]
                    if gaap_eps and gaap_eps == gaap_eps:
                        lines.append(f"    GAAP EPS: ${gaap_eps:.2f}")
                if "Total Revenue" in gaap_data and idx in gaap_data["Total Revenue"].index:
                    rev = gaap_data["Total Revenue"][idx]
                    if rev and rev == rev:
                        lines.append(f"    Revenue: ${rev/1e9:.3f}B")
                if "Gross Profit" in gaap_data and idx in gaap_data["Gross Profit"].index:
                    gp = gaap_data["Gross Profit"][idx]
                    rev_val = gaap_data["Total Revenue"][idx] if "Total Revenue" in gaap_data and idx in gaap_data["Total Revenue"].index else None
                    if gp and gp == gp:
                        gm_pct = f" (GM: {gp/rev_val*100:.1f}%)" if rev_val and rev_val > 0 else ""
                        lines.append(f"    Gross Profit: ${gp/1e9:.3f}B{gm_pct}")
                if "Net Income" in gaap_data and idx in gaap_data["Net Income"].index:
                    ni = gaap_data["Net Income"][idx]
                    if ni and ni == ni:
                        lines.append(f"    Net Income: ${ni/1e9:.3f}B")

                lines.append(f"    Non-GAAP EPS: ${ng_actual:.2f} vs est ${ng_est:.2f} = {beat} by {surprise:+.1f}%")

        # Forward estimates
        eps_df = mu.earnings_estimate
        rev_df = mu.revenue_estimate
        # 0q = next quarter to be reported, +1q = quarter after that
        for i, label in [(0, "NEXT QUARTER TO REPORT (0q)"), (1, "QUARTER AFTER (+1q)")]:
            lines.append(f"\n{label} WALL STREET CONSENSUS:")
            if eps_df is not None and len(eps_df) > i:
                row = eps_df.iloc[i]
                lines.append(f"  EPS est: ${row.get('avg', 0):.2f} (range ${row.get('low', 0):.2f}-${row.get('high', 0):.2f}, {int(row.get('numberOfAnalysts', 0))} analysts)")
                growth = row.get("growth", 0)
                if growth:
                    lines.append(f"  EPS YoY growth est: {growth*100:+.0f}%")
            if rev_df is not None and len(rev_df) > i:
                row = rev_df.iloc[i]
                avg = row.get("avg", 0)
                if avg:
                    lines.append(f"  Revenue est: ${avg/1e9:.2f}B (range ${row.get('low', 0)/1e9:.1f}B-${row.get('high', 0)/1e9:.1f}B)")
                    growth = row.get("growth", 0)
                    if growth:
                        lines.append(f"  Revenue YoY growth est: {growth*100:+.0f}%")

        # Full year estimates
        if eps_df is not None and len(eps_df) > 2:
            fy_row = eps_df.iloc[2]  # 0y
            lines.append(f"\nFULL YEAR CONSENSUS:")
            lines.append(f"  EPS est: ${fy_row.get('avg', 0):.2f} ({int(fy_row.get('numberOfAnalysts', 0))} analysts)")
        if rev_df is not None and len(rev_df) > 2:
            fy_row = rev_df.iloc[2]
            avg = fy_row.get("avg", 0)
            if avg:
                lines.append(f"  Revenue est: ${avg/1e9:.2f}B")

        return "\n".join(lines)
    except Exception as e:
        print(f"[CONSENSUS FETCH ERROR] {e}")
        return ""


def analyze_mu_earnings():
    """Scrape and analyze the actual MU earnings press release."""
    report = fetch_mu_earnings_report()
    if not report:
        send_alert(
            "\U0001f4b0 <b>MU EARNINGS</b>\n"
            "Could not scrape Micron earnings press release. "
            "Check investors.micron.com manually."
        )
        return

    # Fetch earnings history + forward estimates from yfinance
    earnings_context = _fetch_earnings_context()
    context_block = ""
    if earnings_context:
        context_block = f"\nWALL STREET DATA (from Yahoo Finance):\n{earnings_context}\n\n"

    prompt = (
        f"You are analyzing Micron's just-released earnings for a Telegram alert.\n\n"
        f"POSITION: 500x MU 380/400 bull call spread, entry $11.897, expiry March 20, 2026.\n\n"
        f"{context_block}"
        f"ACTUAL EARNINGS PRESS RELEASE:\n{report[:3000]}\n\n"
        f"CRITICAL INSTRUCTIONS:\n"
        f"- Identify WHICH QUARTER this press release covers\n"
        f"- Match it to the CORRECT quarter in the QUARTERLY RESULTS above\n"
        f"- The QUARTERLY RESULTS section has EPS estimates (Non-GAAP EPS vs est) but NO revenue estimates for past quarters\n"
        f"- Do NOT use NEXT QUARTER estimates as the reported quarter's estimates\n"
        f"- For revenue: show actual + QoQ/YoY growth (no estimate available for reported quarter)\n"
        f"- For EPS: show GAAP + Non-GAAP vs est = BEAT/MISS (from the data)\n"
        f"- The NEXT QUARTER CONSENSUS section is ONLY for comparing the GUIDANCE/OUTLOOK from the press release\n"
        f"- Only use numbers from the data provided. Do not hallucinate.\n\n"
        f"OUTPUT FORMAT (Telegram HTML, use <b> for bold):\n\n"
        f"<b>FQX-XX</b> (ending YYYY-MM-DD)\n\n"
        f"<b>RESULTS:</b>\n"
        f"Revenue: $X.XB (QoQ +X%, YoY +X%)\n"
        f"GAAP EPS: $X.XX (QoQ +X%, YoY +X%)\n"
        f"Non-GAAP EPS: $X.XX vs est $X.XX = BEAT/MISS +X%\n"
        f"Gross Margin: XX.X% GAAP / XX.X% Non-GAAP (QoQ +X.Xpp)\n"
        f"Net Income: $X.XB\n\n"
        f"<b>BUSINESS UNITS:</b>\n"
        f"Each BU: revenue + GM% on one line\n\n"
        f"<b>GUIDANCE vs WALL STREET:</b>\n"
        f"Revenue guide: $X.XB vs consensus $X.XB = ABOVE/BELOW/INLINE\n"
        f"EPS guide: $X.XX vs consensus $X.XX = ABOVE/BELOW/INLINE\n"
        f"GM% guide: XX%\n\n"
        f"<b>VERDICT: BULLISH / BEARISH / NEUTRAL</b>\n"
        f"2-3 sentences: EPS beat/miss, guidance vs consensus, what to do with spread\n\n"
        f"Keep it clean and scannable. Under 300 words."
    )

    try:
        resp = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.2, "max_tokens": 500},
            timeout=30,
        )
        resp.raise_for_status()
        analysis = resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        analysis = f"Earnings analysis unavailable: {e}"

    msg = (
        f"\U0001f4b0 <b>MU EARNINGS REPORT ANALYSIS</b>\n"
        f"\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
        f"<i>Source: investors.micron.com + Yahoo Finance</i>\n\n"
        f"{analysis}\n\n"
        f"<code>#EARNINGS</code>"
    )
    send_alert(msg)

    # Follow-up: roundtable debate on implications
    _roundtable_implications("MU EARNINGS", analysis, report[:2000])
