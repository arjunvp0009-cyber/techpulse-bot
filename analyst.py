"""
TechPulse Analyst v2 — Trading Intelligence Edition
Upgraded: Trading pair bias + market impact mapping
"""

import asyncio
import json
import logging
import os
import re
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

from telegram import Bot

# ── CONFIG ────────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID")
OPENROUTER_API_KEY  = os.getenv("OPENROUTER_API_KEY", "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")

ARTICLES_CACHE = Path("articles_cache.json")
SEEN_FILE      = Path("seen_articles.json")
ANALYSIS_OUT   = Path("analysis.json")

logging.basicConfig(format="%(asctime)s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# ── UPGRADED SYSTEM PROMPT ────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a macro trading intelligence engine for Arjun, a systematic trader
in Kerala, India. He trades these 9 instruments: XAUUSD, EURUSD, GBPUSD, USDJPY, USDCHF,
AUDUSD, US30, NAS100, USOIL.

His strategies are structural/Wyckoff-based. He needs to know macro bias BEFORE NY session opens.

Your job is NOT to summarize news.
Your job is to extract actionable market bias and tell him what matters for his trades today.

Rules:
- Score each news 0-5 for market impact. Ignore anything below 3.
- Be decisive. No hedging. No fluff.
- Think like a fund manager, not a journalist.

Return ONLY valid JSON (no markdown, no preamble) matching this exact schema:
{
  "generated_at": "<ISO timestamp>",
  "article_count": <int>,
  "overview": "<2-3 sentence macro synthesis — what is the dominant theme today>",
  "market_trends": [
    {
      "title": "<short trend name>",
      "signal": "bullish|bearish|neutral|watch",
      "category": "<Blockchain & Crypto|AI & Machine Learning|Fintech|Trading & Investing>",
      "summary": "<2-3 sentences>",
      "evidence": ["<article title 1>"]
    }
  ],
  "opportunities": [
    {
      "title": "<opportunity>",
      "type": "investment|product|research",
      "thesis": "<specific thesis in 1-2 lines>",
      "timeframe": "immediate|3-6 months|6-12 months|long-term",
      "confidence": "high|medium|low",
      "evidence": ["<article title>"]
    }
  ],
  "risks": [
    {
      "title": "<risk name>",
      "severity": "critical|high|medium|low",
      "description": "<mechanism of harm in 1-2 lines>",
      "mitigation": "<concrete action>",
      "evidence": ["<article title>"]
    }
  ],
  "key_players": [
    {
      "name": "<company or person>",
      "move": "<what they did — 1 line>",
      "significance": "<why it matters — 1 line>"
    }
  ],
  "contrarian_take": "<one bold non-consensus observation>",
  "trading_bias": {
    "summary": "<1 sentence on today's dominant macro theme for traders>",
    "risk_sentiment": "risk-on|risk-off|neutral",
    "usd_bias": "bullish|bearish|neutral",
    "pairs": {
      "XAUUSD": { "bias": "bullish|bearish|neutral", "reason": "<max 5 words>" },
      "EURUSD": { "bias": "bullish|bearish|neutral", "reason": "<max 5 words>" },
      "GBPUSD": { "bias": "bullish|bearish|neutral", "reason": "<max 5 words>" },
      "USDJPY": { "bias": "bullish|bearish|neutral", "reason": "<max 5 words>" },
      "USDCHF": { "bias": "bullish|bearish|neutral", "reason": "<max 5 words>" },
      "AUDUSD": { "bias": "bullish|bearish|neutral", "reason": "<max 5 words>" },
      "US30":   { "bias": "bullish|bearish|neutral", "reason": "<max 5 words>" },
      "NAS100": { "bias": "bullish|bearish|neutral", "reason": "<max 5 words>" },
      "USOIL":  { "bias": "bullish|bearish|neutral", "reason": "<max 5 words>" }
    }
  }
}

Produce 3-5 items in market_trends, opportunities, risks, key_players."""


def build_prompt(articles: list) -> str:
    lines = [f"Analyze these {len(articles)} recent tech/finance news articles:\n"]
    for i, a in enumerate(articles, 1):
        lines.append(f"{i}. [{a.get('category', '')}] {a.get('title', '')}")
        if a.get("summary"):
            lines.append(f"   {a['summary'][:300]}")
        lines.append("")
    lines.append("\nGenerate the full trading intelligence briefing JSON now.")
    return "\n".join(lines)


def load_articles() -> list:
    if ARTICLES_CACHE.exists():
        data = json.loads(ARTICLES_CACHE.read_text())
        log.info(f"Loaded {len(data)} articles from articles_cache.json")
        return data[-60:]
    if SEEN_FILE.exists():
        log.info("articles_cache.json not found — rebuilding from RSS feeds...")
        return fetch_articles_directly()
    log.warning("No article data found. Run bot.py first.")
    return []


def fetch_articles_directly() -> list:
    import feedparser
    FEEDS = [
        {"url": "https://cointelegraph.com/rss",                                 "cat": "Blockchain & Crypto"},
        {"url": "https://venturebeat.com/category/ai/feed/",                     "cat": "AI & Machine Learning"},
        {"url": "https://techcrunch.com/category/artificial-intelligence/feed/", "cat": "AI & Machine Learning"},
        {"url": "https://www.finextra.com/rss/headlines.aspx",                   "cat": "Fintech"},
        {"url": "https://techcrunch.com/category/fintech/feed/",                 "cat": "Fintech"},
        {"url": "https://www.reddit.com/r/algotrading/.rss",                     "cat": "Trading & Investing"},
    ]
    articles = []
    for feed_cfg in FEEDS:
        try:
            entries = feedparser.parse(feed_cfg["url"]).entries[:8]
            for entry in entries:
                title   = getattr(entry, "title", "")
                summary = re.sub(r"<[^>]+>", "", getattr(entry, "summary", "") or "").strip()[:300]
                if title:
                    articles.append({"category": feed_cfg["cat"], "title": title, "summary": summary})
        except Exception as e:
            log.warning(f"Feed failed: {feed_cfg['url']} — {e}")
    log.info(f"Fetched {len(articles)} articles from feeds")
    return articles[:60]


def call_openrouter(prompt: str) -> str:
    models = [
        "openrouter/free",
        "deepseek/deepseek-r1:free",
        "qwen/qwen3-235b-a22b:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "google/gemma-3-12b-it:free",
        "allenai/olmo-3.1-32b-think:free",
    ]
    for model in models:
        log.info(f"Trying OpenRouter model: {model}")
        payload = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": prompt}
            ],
            "max_tokens": 4096,
            "temperature": 0.4,
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://techpulse-analyst.local",
                "X-Title": "TechPulse Analyst",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                text = data["choices"][0]["message"]["content"].strip()
                log.info(f"Success with {model}")
                return text
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")
            log.warning(f"{model} failed ({e.code}): {body[:200]}")
            continue
        except Exception as e:
            log.warning(f"{model} error: {e}")
            continue

    raise RuntimeError("All OpenRouter models failed. Check your API key.")


def clean_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except:
        pass
    fenced = re.findall(r"```(?:json)?\s*([\s\S]*?)```", raw)
    for block in fenced:
        try:
            return json.loads(block.strip())
        except:
            continue
    start = raw.find("{")
    end   = raw.rfind("}")
    if start != -1 and end != -1:
        try:
            return json.loads(raw[start:end+1])
        except:
            pass
    cleaned = re.sub(r",\s*([}\]])", r"", raw[start:end+1] if start != -1 else raw)
    return json.loads(cleaned)


def run_analysis(articles: list) -> dict:
    raw = call_openrouter(build_prompt(articles))
    analysis = clean_json(raw)
    analysis["generated_at"] = datetime.now(timezone.utc).isoformat()
    analysis["article_count"] = len(articles)
    return analysis


def save_analysis(analysis: dict):
    ANALYSIS_OUT.write_text(json.dumps(analysis, indent=2, ensure_ascii=False))
    log.info(f"Analysis saved → {ANALYSIS_OUT}")


# ── EMOJI MAPS ────────────────────────────────────────────────────────────────
SIGNAL_EMOJI = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪", "watch": "🟡"}
SEV_EMOJI    = {"critical": "🚨", "high": "🔴", "medium": "🟡", "low": "🟢"}
CONF_EMOJI   = {"high": "✅", "medium": "🔶", "low": "❌"}
BIAS_EMOJI   = {"bullish": "▲", "bearish": "▼", "neutral": "─"}


async def send_safe(bot, chat_id: str, text: str):
    while text:
        chunk = text[:4096]
        if len(text) > 4096:
            cut = chunk.rfind("\n")
            if cut > 2000:
                chunk = text[:cut]
        await bot.send_message(
            chat_id=chat_id,
            text=chunk,
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
        text = text[len(chunk):]
        if text:
            await asyncio.sleep(0.5)


async def notify_telegram(analysis: dict):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.info("No Telegram credentials — skipping notification.")
        return
    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    n   = analysis.get("article_count", 0)
    now = datetime.now().strftime("%d %b %Y")

    # ── HEADER + OVERVIEW ─────────────────────────────────────────────────────
    await send_safe(bot, TELEGRAM_CHAT_ID,
        f"🧠 *TRADE INTELLIGENCE — {now}*\n"
        f"_{n} articles analysed_\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"*📡 Macro Signal*\n"
        f"{analysis.get('overview', '')}"
    )
    await asyncio.sleep(1)

    # ── TODAY'S TRADING BIAS (THE NEW SECTION) ────────────────────────────────
    tb = analysis.get("trading_bias", {})
    if tb:
        pairs = tb.get("pairs", {})
        risk  = tb.get("risk_sentiment", "neutral").upper()
        usd   = tb.get("usd_bias", "neutral").upper()

        lines = [
            "💡 *TODAY'S TRADING BIAS*",
            "━━━━━━━━━━━━━━━━━━━━━━━━",
            f"Risk Sentiment : `{risk}`",
            f"USD Bias       : `{usd}`",
            "",
        ]

        for pair, data in pairs.items():
            bias   = data.get("bias", "neutral")
            reason = data.get("reason", "")
            emoji  = BIAS_EMOJI.get(bias, "─")
            lines.append(f"`{pair:<8}` {emoji}  _{reason}_")

        lines.append("")
        lines.append(f"_{tb.get('summary', '')}_")

        await send_safe(bot, TELEGRAM_CHAT_ID, "\n".join(lines))
        await asyncio.sleep(1)

    # ── MARKET TRENDS ─────────────────────────────────────────────────────────
    trends = analysis.get("market_trends", [])
    if trends:
        lines = ["*📈 MARKET TRENDS*\n"]
        for t in trends:
            sig = SIGNAL_EMOJI.get(t.get("signal", ""), "⚪")
            lines.append(f"{sig} *{t['title']}*")
            lines.append(f"{t.get('summary', '')}")
            lines.append("")
        await send_safe(bot, TELEGRAM_CHAT_ID, "\n".join(lines))
        await asyncio.sleep(1)

    # ── OPPORTUNITIES ─────────────────────────────────────────────────────────
    opps = analysis.get("opportunities", [])
    if opps:
        lines = ["*💡 OPPORTUNITIES*\n"]
        for o in opps:
            conf = CONF_EMOJI.get(o.get("confidence", ""), "❌")
            lines.append(f"{conf} *{o['title']}*")
            lines.append(f"_{o.get('type','').upper()} · {o.get('timeframe','')} · {o.get('confidence','').upper()} confidence_")
            lines.append(f"{o.get('thesis', '')}")
            lines.append("")
        await send_safe(bot, TELEGRAM_CHAT_ID, "\n".join(lines))
        await asyncio.sleep(1)

    # ── RISKS ─────────────────────────────────────────────────────────────────
    risks = analysis.get("risks", [])
    if risks:
        lines = ["*⚠️ RISK REGISTER*\n"]
        for r in risks:
            sev = SEV_EMOJI.get(r.get("severity", ""), "🟡")
            lines.append(f"{sev} *{r['title']}*")
            lines.append(f"{r.get('description', '')}")
            lines.append(f"_Mitigation: {r.get('mitigation', '')}_")
            lines.append("")
        await send_safe(bot, TELEGRAM_CHAT_ID, "\n".join(lines))
        await asyncio.sleep(1)

    # ── KEY PLAYERS ───────────────────────────────────────────────────────────
    players = analysis.get("key_players", [])
    if players:
        lines = ["*👥 KEY PLAYERS*\n"]
        for p in players:
            lines.append(f"• *{p['name']}*: {p.get('move', '')}")
            lines.append(f"  _{p.get('significance', '')}_")
            lines.append("")
        await send_safe(bot, TELEGRAM_CHAT_ID, "\n".join(lines))
        await asyncio.sleep(1)

    # ── CONTRARIAN TAKE ───────────────────────────────────────────────────────
    contrarian = analysis.get("contrarian_take", "")
    if contrarian:
        await send_safe(bot, TELEGRAM_CHAT_ID,
            f"*🔥 CONTRARIAN TAKE*\n\n_{contrarian}_"
        )

    log.info("Full Telegram report sent.")


async def analyse_and_notify() -> dict:
    articles = load_articles()
    if not articles:
        return {"error": "No articles found."}
    analysis = run_analysis(articles)
    save_analysis(analysis)
    await notify_telegram(analysis)
    return analysis


if __name__ == "__main__":
    asyncio.run(analyse_and_notify())
