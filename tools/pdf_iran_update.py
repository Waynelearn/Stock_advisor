"""Generate Iran War Update PDF and send via Telegram."""
from fpdf import FPDF
import requests

import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from alerts.config import TELEGRAM_BOT_TOKEN as BOT_TOKEN, TELEGRAM_CHAT_ID as CHAT_ID


class PDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 14)
        self.cell(0, 10, "IRAN WAR SITUATION REPORT", new_x="LMARGIN", new_y="NEXT", align="C")
        self.set_font("Helvetica", "", 9)
        self.cell(0, 5, "Day 19 | March 18, 2026 | FOMC + MU Earnings Day", new_x="LMARGIN", new_y="NEXT", align="C")
        self.line(10, self.get_y() + 2, 200, self.get_y() + 2)
        self.ln(5)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.cell(0, 10, f"MU Advisor | Page {self.page_no()}", align="C")


pdf = PDF()
pdf.set_auto_page_break(auto=True, margin=20)
pdf.add_page()


def sec(t, size=10):
    pdf.set_font("Helvetica", "B", size)
    pdf.cell(0, 7, t, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)

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

def header_bar(t, r, g, b):
    pdf.set_fill_color(r, g, b)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(0, 7, f"  {t}", new_x="LMARGIN", new_y="NEXT", fill=True)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)

# ============ MARKET SNAPSHOT ============
header_bar("MARKET SNAPSHOT - March 18, 2026", 0, 50, 100)
w = [30, 30, 30, 100]
trow(["Ticker", "Price", "Change", "Note"], w, True)
trow(["MU", "$461.69", "+4.5%", "RIPPING on earnings day - up 29% from $358 low"], w)
trow(["NVDA", "$181.93", "-0.7%", "Flat after GTC - Rubin priced in"], w)
trow(["VIX", "21.60", "-3.4%", "Falling despite war escalation"], w)
trow(["WTI Oil", "$93.24", "-2.4%", "Dropping - Hormuz slowly reopening"], w)
trow(["Brent", "$102.09", "-1.3%", "Still above $100 but trending down"], w)
trow(["S&P Fut", "6,805", "+0.5%", "Risk-on despite war"], w)
trow(["Nasdaq", "25,167", "+0.6%", "Tech leading"], w)
pdf.ln(3)

# ============ MAJOR DEVELOPMENTS ============
header_bar("MAJOR DEVELOPMENTS (March 14-18)", 180, 0, 0)

sec("1. ALI LARIJANI KILLED BY ISRAEL", 9)
bul("Iran's Supreme National Security Council secretary - top security official")
bul("Killed alongside his son and protection team in targeted strike")
bul("Second-highest-profile kill after Ali Khamenei (Feb 28)")
bul("Iran's leadership being systematically decapitated")
pdf.ln(2)

sec("2. IRAN STRIKES UAE GAS FIELD", 9)
bul("Shah gas plant in Abu Dhabi set ablaze by Iranian drone strike")
bul("Supplies 20% of UAE gas and 5% of global granulated sulphur")
bul("Operations suspended - damage assessment ongoing")
bul("This is Level 3 escalation (oil infrastructure war) materializing")
bul("Brent topped $105 briefly on this news")
pdf.ln(2)

sec("3. ISRAEL GROUND OPS IN LEBANON", 9)
bul("IDF launched 'limited and targeted' ground operation in southern Lebanon")
bul("1 million+ displaced in Lebanon, 100,000+ in shelters")
bul("War spreading to second front - Hezbollah activation")
bul("Two Israeli civilians killed by Iranian missile barrage in Ramat Gan")
pdf.ln(2)

sec("4. 200 US TROOPS WOUNDED", 9)
bul("Pentagon confirms 200 US service members wounded, 10 seriously")
bul("13 US service members killed since Feb 28")
bul("Casualties mounting - political pressure building on Trump")
pdf.ln(2)

sec("5. TRUMP vs NATO - ALLIES REFUSE HORMUZ MISSION", 9)
bul("Trump demanded NATO + China help reopen Strait of Hormuz")
bul("ALL allies publicly rejected the call")
bul("Trump: 'Very foolish mistake' - threatened to leave NATO")
bul("Trump: US 'doesn't need' NATO allies for Iran war")
bul("Diplomatic isolation of US growing")
pdf.ln(2)

sec("6. BESSENT: US ALLOWING IRANIAN OIL THROUGH HORMUZ", 9)
bul("Treasury Sec Bessent: 'We let that happen to supply the rest of the world'")
bul("US not intercepting Iranian tankers - pragmatic oil policy")
bul("Contradicts Trump's 'maximum pressure' rhetoric")
bul("Signal: US prioritizing oil supply stability over war objectives")
pdf.ln(2)

sec("7. HORMUZ SLOWLY REOPENING", 9)
bul("Vessel transits nearly doubled - 8 ships detected Monday")
bul("Iran allowing more non-Western ships through")
bul("But: only 21 tankers total since Feb 28 (vs 100+/day pre-war)")
bul("Daily Gulf oil exports still down 60%")
bul("Oil tankers remain hesitant - insurance premiums prohibitive")
pdf.ln(3)

# ============ NEW PAGE - ESCALATION UPDATE ============
pdf.add_page()
header_bar("ESCALATION LADDER UPDATE", 255, 80, 0)

txt("Since last report (March 13), we've moved partially into Level 3:")
pdf.ln(1)

bul("Level 3 (Oil Infrastructure War): PARTIALLY TRIGGERED")
bul("  - Iran struck UAE Shah gas field (energy infrastructure attack)")
bul("  - BUT: Trump has NOT destroyed Kharg oil infrastructure in response")
bul("  - Bessent allowing Iranian oil through = de-escalation signal")
bul("  - Net: one-sided Level 3 - Iran attacking Gulf infra, US showing restraint")
pdf.ln(2)

bul("Level 4 (Proxy Activation): PARTIALLY TRIGGERED")
bul("  - Israel ground ops in Lebanon = Hezbollah front activated")
bul("  - Iranian missiles hitting Israel (2 dead in Ramat Gan)")
bul("  - Houthis status unclear")
bul("  - Iraqi militias still restrained")
pdf.ln(2)

bul("Level 5 (Ground Ops): NOT YET")
bul("  - Marines still en route (ETA March 23-27)")
bul("  - No US ground forces inside Iran")
bul("  - Israel ground ops limited to Lebanon, not Iran")
pdf.ln(3)

header_bar("REVISED PROBABILITIES", 0, 50, 100)
w3 = [60, 35, 35, 60]
trow(["Scenario", "Mar 13 est", "Mar 18 est", "Direction"], w3, True)
trow(["Stalemate -> negotiation", "Most likely", "Most likely", "Unchanged"], w3)
trow(["Level 3 (full oil war)", "30-40%", "25-30%", "Down (Bessent signal)"], w3)
trow(["Level 4 (proxy activation)", "20-30%", "30-35%", "UP (Lebanon ops)"], w3)
trow(["Level 5 (ground ops Iran)", "15-20%", "10-15%", "Down (NATO refused)"], w3)
trow(["Ceasefire within 2 weeks", "10-15%", "15-20%", "UP (Hormuz opening)"], w3)
pdf.ln(3)

header_bar("KEY PARADOX: WAR ESCALATING BUT MARKETS DE-RISKING", 0, 100, 0)
txt(
    "The war is objectively worse than March 13: UAE gas field hit, Lebanon ground ops, "
    "200 US wounded, Larijani killed, NATO allies refusing to help. "
    "Yet oil is DOWN (-2.4%), VIX is DOWN (-3.4%), and MU is UP (+4.5% to $462).\n\n"
    "Why? Three reasons:\n"
    "1. Hormuz slowly reopening - traffic doubling, Iran allowing more ships\n"
    "2. Bessent signaling US won't block Iranian oil - pragmatism over ideology\n"
    "3. Market pricing 'peak war' - worst case (full Hormuz closure) didn't happen\n\n"
    "The market has decided the war is a stalemate heading toward negotiation, "
    "not total escalation. Oil at $93 (down from $119 peak) confirms this."
)
pdf.ln(3)

header_bar("WHAT TO WATCH TONIGHT (March 18 SGT)", 130, 0, 0)
bul("2:00 AM SGT - FOMC rate decision + dot plot (92% hold)")
bul("2:30 AM SGT - Powell press conference (oil/inflation commentary key)")
bul("4:15 AM SGT - MU EARNINGS FQ2 (consensus $8.59 EPS, $18.96B rev)")
bul("MU at $462 - already priced in a beat. FQ3 guide is the swing factor.")
bul("If FQ3 guide > $21B rev + 70% GM: MU $480-500")
bul("If sell-the-news (guide meets, doesn't wow): MU fades to $440-450")
bul("If hawkish FOMC + weak guide: MU $420-430")
pdf.ln(3)

sec("SOURCES:", 8)
pdf.set_font("Helvetica", "", 7)
for s in [
    "Al Jazeera - Day 17-18 of US-Israel attacks on Iran",
    "CNN - Day 18: top Iranian leaders killed, Trump admin official quits",
    "NBC - Trump pressures NATO, Israel launches Lebanon ground ops",
    "CNBC - Iran targets UAE energy infrastructure, gas field ablaze",
    "CNBC - Bessent: US allowing Iranian oil tankers through Hormuz",
    "CBS - Israel kills Larijani, EU rejects Trump Hormuz call",
    "Bloomberg - UAE gas field struck, key oil hub halts",
    "NPR - Trump demands NATO help, allies not joining",
    "Al Jazeera - Iran allowing more ships through Hormuz",
    "ABC - 200 US troops wounded, 10 seriously",
    "Morningstar - Does Iran war change Fed outlook?",
    "Fortune - Peak war panic in 1-3 weeks, strategist predicts",
]:
    pdf.cell(0, 3.5, f"  - {s}", new_x="LMARGIN", new_y="NEXT")

# Save
path = "/home/wayne/website/mu_advisor/analysis/iran_sitrep_2026_03_18.pdf"
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
                "IRAN WAR SITREP - Day 19\n"
                "March 18, 2026 (FOMC + MU Earnings Day)\n\n"
                "Key: Larijani killed, UAE gas field hit, Lebanon ground ops,\n"
                "200 US wounded, NATO refuses Hormuz, Hormuz slowly reopening.\n\n"
                "MU $462 (+4.5%) | Oil $93 (-2.4%) | VIX 21.6 (-3.4%)\n\n"
                "#WAR #SITREP"
            ),
        },
        files={"document": ("Iran_SITREP_March18_2026.pdf", f, "application/pdf")},
    )

result = resp.json()
print(f"Telegram: {resp.status_code}, OK: {result.get('ok')}")
if not result.get("ok"):
    print(f"Error: {result.get('description')}")
