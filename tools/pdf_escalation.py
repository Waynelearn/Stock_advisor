"""Generate Iran War Escalation Ladder PDF and send via Telegram."""
from fpdf import FPDF
import requests

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from alerts.config import TELEGRAM_BOT_TOKEN as BOT_TOKEN, TELEGRAM_CHAT_ID as CHAT_ID


class PDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 14)
        self.cell(0, 10, "IRAN WAR ESCALATION LADDER", new_x="LMARGIN", new_y="NEXT", align="C")
        self.set_font("Helvetica", "", 9)
        self.cell(0, 5, "From Current State to Total Unrestricted War | March 13, 2026", new_x="LMARGIN", new_y="NEXT", align="C")
        self.line(10, self.get_y() + 2, 200, self.get_y() + 2)
        self.ln(5)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.cell(0, 10, f"MU Advisor | Page {self.page_no()}", align="C")


pdf = PDF()
pdf.set_auto_page_break(auto=True, margin=20)
pdf.add_page()


def lvl(num, title, status, r, g, b):
    pdf.set_fill_color(r, g, b)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(255, 255, 255)
    label = f"  LEVEL {num}: {title}  [{status}]" if num != "" else f"  {title}  [{status}]"
    pdf.cell(0, 8, label, new_x="LMARGIN", new_y="NEXT", fill=True)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)


def sec(t):
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 6, t, new_x="LMARGIN", new_y="NEXT")


def txt(t):
    pdf.set_font("Helvetica", "", 8)
    pdf.multi_cell(0, 4.5, t)
    pdf.ln(1)


def bul(t):
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(0, 4.5, f"  {chr(149)}  {t}", new_x="LMARGIN", new_y="NEXT")


def trow(cols, ws, bold=False):
    pdf.set_font("Helvetica", "B" if bold else "", 7)
    for c, w in zip(cols, ws):
        pdf.cell(w, 5, str(c), border=1)
    pdf.ln()


# Current status
pdf.set_font("Helvetica", "B", 10)
pdf.cell(0, 6, "CURRENT: Day 15 | MU $427 | Oil WTI $99/Brent $103 | VIX 25", new_x="LMARGIN", new_y="NEXT")
pdf.ln(3)

# LEVEL 0
lvl(0, "PRE-WAR", "PASSED", 100, 100, 100)
bul("JCPOA collapsed, negotiations failed")
bul("Israel struck Iranian nuclear sites (June 2025)")
bul("Iran enriched to 60% HEU - 441kg stockpiled (enough for 5-6 bombs)")
bul("DIA assessed Iran could produce weapons-grade uranium in days to weeks")
pdf.ln(3)

# LEVEL 1
lvl(1, "LIMITED AIR CAMPAIGN", "PASSED", 100, 100, 100)
bul("US-Israel joint strikes on military/nuclear targets (Feb 28)")
bul("Ali Khamenei killed in strikes - most escalatory single event")
bul("Air defenses suppressed, command and control disrupted")
bul("Iran retaliates with ballistic missiles at Israel and Gulf bases")
pdf.ln(3)

# LEVEL 2
lvl(2, "EXPANDED AIR + HORMUZ BLOCKADE", "WE ARE HERE", 0, 120, 0)
sec("Military situation:")
bul("US struck Kharg Island military targets (spared oil infrastructure)")
bul("Trump ultimatum: touch Hormuz again = oil infrastructure destroyed")
bul("Iran selective blockade: blocking West, China Tier 1, India Tier 2")
bul("Iran proposing yuan-denominated oil as condition for passage")
bul("50+ Iranian naval vessels sunk, missile launches down 86%")
bul("1,444 Iranians killed, 13 US dead, 3.2M displaced")
bul("Mojtaba Khamenei named supreme leader (Day 5)")
bul("2,500 Marines (31st MEU) from Japan - ETA March 23-27")
pdf.ln(1)

sec("Iran remaining arsenal:")
w = [50, 30, 35, 75]
trow(["Asset", "Pre-war", "Remaining", "Status"], w, True)
trow(["Medium-range BMs", "~2,000", "~300-600", "Down 70-85%"], w)
trow(["Short-range BMs", "6-8,000", "~2-3,000", "Launchers being hunted"], w)
trow(["Drones (Shahed)", "Thousands", "Significant", "Hard to suppress, low-cost"], w)
trow(["Naval vessels", "~200", "~150", "50+ sunk, speedboats intact"], w)
trow(["Anti-ship missiles", "~5,000", "~3-4,000", "Buried in mountains"], w)
trow(["Submarines", "3 Kilo+midget", "Unknown", "Hiding in shallow water"], w)
trow(["Mines in Hormuz", "N/A", "~12 laid", "Can lay more"], w)
pdf.ln(1)

sec("Proxy activation:")
w2 = [35, 50, 105]
trow(["Proxy", "Status", "Assessment"], w2, True)
trow(["Hezbollah", "Rockets then quiet", "Degraded from 2024 war, ~100K rockets"], w2)
trow(["Houthis", "Signaled Red Sea", "Capable but acting independently"], w2)
trow(["Iraqi militias", "Rhetoric only", "Restrained by Iraqi PM"], w2)
trow(["Hamas", "Destroyed", "Minimal threat"], w2)
pdf.ln(3)

# LEVEL 3
pdf.add_page()
lvl(3, "OIL INFRASTRUCTURE WAR", "30-40% prob, ~2 weeks", 255, 165, 0)
sec("Trigger:")
bul("Iran retaliates vs Kharg by attacking Saudi Aramco, UAE, Kuwait oil")
bul("Trump follows through - destroys Kharg oil infrastructure")
pdf.ln(1)
sec("Consequences:")
bul("Iran loses 90% of oil exports ($53B/yr, 11% of GDP)")
bul("Reconstruction: months to a year, sanctions block tech")
bul("Mutual destruction: Saudi Ras Tanura, UAE Jebel Ali targeted")
bul("Oil: $120-150. Global recession probability 60%+")
bul("VIX: 35-40, S&P -10-15%, MU: -15 to -25%")
pdf.ln(1)
sec("Why it might NOT happen:")
bul("Mutually destructive - both sides need oil revenue/low prices")
bul("Deterrence holding - Trump showed CAN hit Kharg but CHOSE not to")
pdf.ln(3)

# LEVEL 4
lvl(4, "FULL PROXY WAR ACTIVATION", "20-30% prob, ~1 month", 255, 120, 0)
sec("Trigger: Iran orders full proxy activation facing defeat")
pdf.ln(1)
bul("Hezbollah fires thousands of rockets into Israel")
bul("Houthis attack Red Sea shipping + Saudi targets")
bul("Iraqi militias attack 15+ US bases simultaneously")
bul("Sleeper cells activated in Gulf states")
pdf.ln(1)
sec("What breaks restraint:")
bul("Regime collapse fear -> nothing to lose order")
bul("US ground ops -> holy war declaration")
bul("Kharg destroyed -> no economy, proxies are last card")
bul("Oil: $130-160. 35% of global oil disrupted")
pdf.ln(3)

# LEVEL 5
lvl(5, "SPECIAL OPS / GROUND INCURSION", "15-20% prob, 1-2 mo", 255, 80, 0)
sec("Trigger: Air campaign diminishing returns, nukes need verification")
pdf.ln(1)
bul("Delta Force/SEAL raids on Fordow (260ft underground)")
bul("31st MEU seizes islands / coast for mine clearing")
bul("NOT full invasion - surgical raids with extraction")
bul("Risk: boots on soil = full mobilization + nuclear sprint")
pdf.ln(3)

# LEVEL 6
lvl(6, "NUCLEAR THRESHOLD CRISIS", "10-15% prob, 2-3 months", 220, 30, 30)
sec("Trigger: Cornered Iran sprints to nuclear weapon")
pdf.ln(1)
bul("441kg 60% HEU -> weapons-grade in 2-3 weeks")
bul("BUT: enrichment facilities already struck twice")
bul("Weaponization (building bomb) takes months beyond enrichment")
bul("Even a dirty bomb or nuclear TEST changes everything")
bul("Sci American: Iran nowhere close to finished weapon")
bul("A sprint would be detected and struck immediately")
pdf.ln(3)

# LEVEL 7
lvl(7, "GULF STATE DIRECT WAR", "5-10% probability", 180, 0, 0)
bul("Iran attacks escalate to mass casualties in Dubai/Saudi")
bul("Saudi Arabia, UAE formally enter war")
bul("Iran targets desalination plants (drinking water)")
bul("Oil infrastructure destroyed on BOTH sides. Oil: $200+")
pdf.ln(3)

# LEVEL 8
lvl(8, "GREAT POWER INVOLVEMENT", "3-5% probability", 130, 0, 0)
bul("China provides Iran weapons if energy security threatened")
bul("Trigger: US sinks Chinese tanker or sanctions buyers")
bul("NOT direct intervention but prolongs war indefinitely")
bul("US-China spike -> trade war, Taiwan concerns")
pdf.ln(3)

# LEVEL 9
lvl(9, "TOTAL UNRESTRICTED WAR", "1-3% probability", 80, 0, 0)
bul("Full ground invasion of Iran (500K+ troops)")
bul("Iran uses every weapon including potential nuclear")
bul("All proxies activated. Red Sea, Hormuz, Suez ALL blocked")
bul("Oil loses 30-40% -> $200-300/bbl. Global depression")
bul("US casualties: 10,000-50,000+ multi-year")

# SUMMARY PAGE
pdf.add_page()
lvl("", "PROBABILITY MATRIX & TRADING IMPACT", "", 0, 50, 100)
pdf.ln(2)

w3 = [12, 55, 25, 28, 30, 40]
trow(["Lvl", "Scenario", "Prob", "Oil", "VIX", "MU Impact"], w3, True)
trow(["0-1", "Pre-war / Limited air", "PASSED", "-", "-", "-"], w3)
trow(["2", "Expanded air + Hormuz", "NOW", "$95-103", "25", "$427 now"], w3)
trow(["3", "Oil infrastructure war", "30-40%", "$120-150", "35-40", "-15 to -25%"], w3)
trow(["4", "Full proxy activation", "20-30%", "$130-160", "35-45", "-20 to -30%"], w3)
trow(["5", "Ground operations", "15-20%", "$140-170", "40-50", "-25 to -35%"], w3)
trow(["6", "Nuclear threshold", "10-15%", "$150-200", "45-60", "-30 to -40%"], w3)
trow(["7", "Gulf state war", "5-10%", "$180-250", "50+", "-35 to -50%"], w3)
trow(["8", "Great power", "3-5%", "$200+", "60+", "-40 to -60%"], w3)
trow(["9", "Total unrestricted", "1-3%", "$200-300", "Halt", "Market halt"], w3)
pdf.ln(5)

sec("KEY INSIGHT:")
txt(
    "Most likely path: Level 2 -> stalemate -> negotiation. "
    "But the Kharg Island ultimatum created a direct pathway to Level 3. "
    "The market is pricing Level 2 (oil $100, VIX 25). "
    "Any move to Level 3 reprices everything."
)
pdf.ln(2)

sec("TRADING IMPLICATION:")
txt("Level 3 probability within March 20 expiry: ~15-20%")
txt("Level 3 probability within April 17 expiry: ~25-30%")
txt("Level 4+ probability within any 2026 expiry: ~10-15%")
pdf.ln(3)

sec("SOURCES:")
pdf.set_font("Helvetica", "", 7)
for s in [
    "RAND Corporation - War in Iran Q&A with Experts",
    "Hudson Institute - Operation Epic Fury (Iran Capabilities)",
    "JINSA - Iran Missile Firepower Has Almost Run Out",
    "Atlantic Council - 20 Questions on the Iran War",
    "Arms Control Association - Iran Nuclear Program Status",
    "Scientific American - Iran Nuclear Assessment",
    "Bloomberg - Iran Drones/Missiles vs US Military",
    "Foreign Policy - Iran Proxies Out for Themselves",
    "Al Jazeera Centre for Studies - Strategic Escalation",
    "FactCheck.org - Nuclear Capabilities Assessment",
    "NBC, CNN, CNBC, NPR - Live war reporting",
]:
    pdf.cell(0, 3.5, f"  - {s}", new_x="LMARGIN", new_y="NEXT")

# Save
path = "/home/wayne/website/mu_advisor/analysis/iran_escalation_ladder_2026.pdf"
pdf.output(path)
print(f"PDF saved: {path}")

# Send via Telegram
url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
with open(path, "rb") as f:
    resp = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "caption": (
                "IRAN WAR ESCALATION LADDER\n"
                "From Current State to Total Unrestricted War\n"
                "March 13, 2026\n\n"
                "9 levels with probabilities, oil impact, MU implications.\n\n"
                "#WAR #ANALYSIS"
            ),
        },
        files={"document": ("Iran_Escalation_Ladder_2026.pdf", f, "application/pdf")},
    )

result = resp.json()
print(f"Telegram: {resp.status_code}, OK: {result.get('ok')}")
if not result.get("ok"):
    print(f"Error: {result.get('description')}")
