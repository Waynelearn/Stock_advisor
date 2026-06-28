"""Article summarizer using DeepSeek API - extracts, summarizes, and rates MU sentiment."""

import requests
from bs4 import BeautifulSoup

from .config import DEEPSEEK_API_KEY, DEEPSEEK_MODEL_FAST
from .llm import ask

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"


def fetch_article(url: str, max_chars: int = 3000) -> dict:
    """Fetch article and extract body text + image.

    Returns: {"text": str|None, "image": str|None}
    """
    result = {"text": None, "image": None}
    if not url:
        return result
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
        }
        resp = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # Extract og:image (most reliable article image)
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            result["image"] = og_image["content"]
        else:
            # Try twitter:image
            tw_image = soup.find("meta", attrs={"name": "twitter:image"})
            if tw_image and tw_image.get("content"):
                result["image"] = tw_image["content"]

        # Remove script, style, nav, footer, header
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "iframe"]):
            tag.decompose()

        # Extract text from paragraphs
        paragraphs = soup.find_all("p")
        text = " ".join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 30)

        if not text:
            article = soup.find("article") or soup.find("main") or soup.find("body")
            if article:
                text = article.get_text(separator=" ", strip=True)

        result["text"] = text[:max_chars] if text else None
    except Exception:
        pass
    return result


def fetch_article_text(url: str, max_chars: int = 3000) -> str | None:
    """Backward compat wrapper."""
    return fetch_article(url, max_chars)["text"]


def summarize_article(title: str, text: str | None) -> dict:
    """Send article to DeepSeek for summary + MU sentiment.

    Returns: {"summary": str, "sentiment": "BULLISH"|"NEUTRAL"|"BEARISH", "relevance": "HIGH"|"MED"|"LOW"}
    """
    content = f"Headline: {title}"
    if text:
        content += f"\n\nArticle text:\n{text}"

    prompt = (
        "You are a semiconductor equity analyst focused on Micron Technology (MU). "
        "Analyze this news article and respond with EXACTLY this JSON format, nothing else:\n"
        '{"summary": "<2-3 sentence summary focusing on what matters for MU. If the article mentions a specific upcoming event/date, include it in the summary.>", '
        '"sentiment": "<BULLISH or NEUTRAL or BEARISH for MU stock>", '
        '"relevance": "<HIGH or MED or LOW relevance to MU>", '
        '"catalyst": "<If article mentions a specific upcoming event with a date (earnings, product launch, conference, policy decision, report release), return {\"month\": int, \"day\": int, \"event\": \"short description\"}. Otherwise null>"}\n\n'
        "Rules:\n"
        "- BULLISH: positive for MU revenue, HBM demand, DRAM/NAND pricing, AI spend, dovish Fed\n"
        "- BEARISH: negative for MU (demand weakness, pricing pressure, hawkish Fed, tariffs, geopolitical risk)\n"
        "- NEUTRAL: not directly impactful or mixed signals\n"
        "- HIGH relevance: directly about MU, HBM, memory pricing, or major macro (FOMC, CPI)\n"
        "- MED: about peers (NVDA, AMD, AVGO) or semiconductor sector broadly\n"
        "- LOW: tangentially related\n"
        "- catalyst: only if article explicitly mentions a FUTURE dated event relevant to semis/MU/markets. Must have month+day.\n\n"
        f"Article:\n{content}"
    )

    try:
        result_text = ask(prompt, tier="fast", temperature=0.1, max_tokens=3000,
                          label="summarizer.article")

        # Parse JSON from response
        import json
        # Handle markdown code blocks
        if result_text.startswith("```"):
            result_text = result_text.split("```")[1]
            if result_text.startswith("json"):
                result_text = result_text[4:]
            result_text = result_text.strip()

        parsed = json.loads(result_text)

        result = {
            "summary": parsed.get("summary", "No summary available"),
            "sentiment": parsed.get("sentiment", "NEUTRAL").upper(),
            "relevance": parsed.get("relevance", "LOW").upper(),
        }

        # Extract discovered catalyst if present
        catalyst = parsed.get("catalyst")
        if catalyst and isinstance(catalyst, dict) and "month" in catalyst and "day" in catalyst:
            try:
                from .catalyst_fetcher import add_discovered_catalyst
                add_discovered_catalyst(
                    int(catalyst["month"]),
                    int(catalyst["day"]),
                    str(catalyst.get("event", "Discovered event")),
                )
            except Exception:
                pass

        return result
    except Exception as e:
        return {
            "summary": title,
            "sentiment": "NEUTRAL",
            "relevance": "LOW",
        }


def batch_sentiment(headlines: list[dict]) -> list[dict]:
    """Analyze multiple headlines in a single API call for efficiency.

    Input: [{"title": str, "source": str, "link": str}, ...]
    Returns same list with added "summary", "sentiment", "relevance" fields.
    """
    if not headlines:
        return headlines

    titles_text = "\n".join(f"{i+1}. {h['title']}" for i, h in enumerate(headlines))

    prompt = (
        "You are a semiconductor equity analyst focused on Micron Technology (MU). "
        "Analyze these news headlines and respond with a JSON array. "
        "Each element must have: summary (1 sentence, MU-focused), sentiment (BULLISH/NEUTRAL/BEARISH for MU), relevance (HIGH/MED/LOW to MU).\n"
        "Respond with ONLY the JSON array, no other text.\n\n"
        f"Headlines:\n{titles_text}"
    )

    try:
        result_text = ask(prompt, tier="fast", temperature=0.1, max_tokens=3000,
                          label="summarizer.batch")

        import json
        if result_text.startswith("```"):
            result_text = result_text.split("```")[1]
            if result_text.startswith("json"):
                result_text = result_text[4:]
            result_text = result_text.strip()

        parsed = json.loads(result_text)

        for i, h in enumerate(headlines):
            if i < len(parsed):
                h["summary"] = parsed[i].get("summary", h["title"])
                h["sentiment"] = parsed[i].get("sentiment", "NEUTRAL").upper()
                h["relevance"] = parsed[i].get("relevance", "LOW").upper()
            else:
                h["summary"] = h["title"]
                h["sentiment"] = "NEUTRAL"
                h["relevance"] = "LOW"

    except Exception:
        for h in headlines:
            h["summary"] = h["title"]
            h["sentiment"] = "NEUTRAL"
            h["relevance"] = "LOW"

    return headlines
