"""
TechPulse Bot with Gemini AI Analysis
- /news    → fetch latest articles
- /analyze → Gemini AI gives personalized briefing
- /help    → show commands
"""

import asyncio
import feedparser
import hashlib
import json
import logging
import os
import re
import httpx
from pathlib import Path

from telegram import Bot, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY")
SEEN_FILE          = Path("seen_articles.json")
BUFFER_FILE        = Path("articles_buffer.json")
MODE               = os.getenv("MODE", "once")

logging.basicConfig(format="%(asctime)s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

FEEDS = [
    {"url": "https://cointelegraph.com/rss",                                 "cat": "🔗 Blockchain & Crypto"},
    {"url": "https://coindesk.com/arc/outboundfeeds/rss/",                   "cat": "🔗 Blockchain & Crypto"},
    {"url": "https://decrypt.co/feed",                                       "cat": "🔗 Blockchain & Crypto"},
    {"url": "https://venturebeat.com/category/ai/feed/",                     "cat": "🤖 AI & Machine Learning"},
    {"url": "https://www.artificialintelligence-news.com/feed/",             "cat": "🤖 AI & Machine Learning"},
    {"url": "https://techcrunch.com/category/artificial-intelligence/feed/", "cat": "🤖 AI & Machine Learning"},
    {"url": "https://www.finextra.com/rss/headlines.aspx",                   "cat": "💳 Fintech"},
    {"url": "https://techcrunch.com/category/fintech/feed/",                 "cat": "💳 Fintech"},
    {"url": "https://thefintechtimes.com/feed/",                             "cat": "💳 Fintech"},
    {"url": "https://quantocracy.com/feed/",                                 "cat": "📈 Trading & Investing"},
    {"url": "https://alpaca.markets/blog/feed/",                             "cat": "📈 Trading & Investing"},
    {"url": "https://www.reddit.com/r/algotrading/.rss",                     "cat": "📈 Trading & Investing"},
]

KEYWORDS = {
    "🔗 Blockchain & Crypto":   ["blockchain","crypto","bitcoin","ethereum","defi","web3","token","smart contract","stablecoin","nft","layer 2"],
    "🤖 AI & Machine Learning": ["ai","artificial intelligence","machine learning","llm","gpt","generative ai","neural network","openai","anthropic","model","deep learning","agent","automation"],
    "💳 Fintech":               ["fintech","neobank","payment","lending","insurtech","open banking","bnpl","digital bank","remittance","credit"],
    "📈 Trading & Investing":   ["trading","quant","algo","backtesting","broker","portfolio","market data","systematic","strategy","signal"],
}

def load_seen() -> set:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()

def save_seen(seen: set):
    SEEN_FILE.write_text(json.dumps(list(seen)))

def load_buffer() -> list:
    if BUFFER_FILE.exists():
        return json.loads(BUFFER_FILE.read_text())
    return []

def save_buffer(buf: list):
    BUFFER_FILE.write_text(json.dumps(buf[-60:]))

def article_id(entry) -> str:
    key = getattr(entry, "link", "") or getattr(entry, "title", "")
    return hashlib.md5(key.encode()).hexdigest()

def is_relevant(title: str, summary: str, category: str) -> bool:
    text = (title + " " + summary).lower()
    return any(kw in text for kw in KEYWORDS.get(category, []))

def clean_html(raw: str) -> str:
    return re.sub(r"<[^>]+>", "", raw or "").strip()[:200]

def format_message(entry, category: str) -> str:
    title   = getattr(entry, "title", "No title")
    link    = getattr(entry, "link",  "")
    summary = clean_html(getattr(entry, "summary", ""))
    lines   = [f"*{category}*", f"📌 {title}"]
    if summary:
        lines.append(f"_{summary}_")
    if link:
        lines.append(f"[Read →]({link})")
    return "\n".join(lines)

async def fetch_and_send(bot: Bot) -> int:
    seen   = load_seen()
    buffer = load_buffer()
    sent   = 0

    for feed_cfg in FEEDS:
        try:
            entries = feedparser.parse(feed_cfg["url"]).entries[:5]
        except Exception as e:
            log.warning(f"Failed: {feed_cfg['url']} — {e}")
            continue

        for entry in entries:
            aid = article_id(entry)
            if aid in seen:
                continue
            title   = getattr(entry, "title",   "")
            summary = clean_html(getattr(entry, "summary", ""))
            if not is_relevant(title, summary, feed_cfg["cat"]):
                seen.add(aid)
                continue
            try:
                await bot.send_message(
                    chat_id=TELEGRAM_CHAT_ID,
                    text=format_message(entry, feed_cfg["cat"]),
                    parse_mode=ParseMode.MARKDOWN,
                    disable_web_page_preview=True,
                )
                buffer.append({
                    "title":    title,
                    "summary":  summary,
                    "category": feed_cfg["cat"],
                })
                sent += 1
                await asyncio.sleep(1)
            except Exception as e:
                log.error(f"Send failed: {e}")
            seen.add(aid)

    save_seen(seen)
    save_buffer(buffer)
    return sent

async def get_ai_analysis(articles: list) -> str:
    if not GEMINI_API_KEY:
        return "GEMINI_API_KEY not set."

    article_text = ""
    for i, a in enumerate(articles[-40:], 1):
        article_text += f"{i}. [{a['category']}] {a['title']}\n"
        if a.get("summary"):
            article_text += f"   {a['summary']}\n\n"

    prompt = f"""You are a personal intelligence analyst for Arjun, a finance professional and trader in Kerala, India. He tracks: Blockchain/Crypto, AI & ML, Fintech, and Trading tech.

Latest news articles:
{article_text}

Write a sharp personalized briefing for Arjun. Address him directly. Use this structure:

🔥 WHAT'S HAPPENING TODAY
(2-3 sentences on the biggest things right now)

📈 OPPORTUNITIES FOR ARJUN
(Specific opportunities — investments, tools, trends to get ahead of)

⚠️ RISKS & THREATS
(What he should watch out for)

🏃 KEY PLAYERS MOVING
(Companies or people making big moves)

💡 ARJUN'S EDGE
(One smart contrarian insight only a sharp person in this space would notice)

Be direct, specific, no fluff. Like a smart analyst friend texting him."""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            url,
            json={"contents": [{"parts": [{"text": prompt}]}]},
        )
        data = response.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]

async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 Fetching latest news...")
    sent = await fetch_and_send(context.bot)
    await update.message.reply_text(f"✅ Done — {sent} new articles sent.")

async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buffer = load_buffer()
    if not buffer:
        await update.message.reply_text("No articles yet. Run /news first.")
        return
    await update.message.reply_text(f"🧠 Analyzing {len(buffer)} articles — give me a moment...")
    analysis = await get_ai_analysis(buffer)
    if len(analysis) > 4000:
        for i in range(0, len(analysis), 4000):
            await update.message.reply_text(analysis[i:i+4000])
    else:
        await update.message.reply_text(analysis)

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📡 *TechPulse Bot Commands*\n\n"
        "/news — fetch latest articles now\n"
        "/analyze — AI analysis of all recent news\n"
        "/help — show this menu",
        parse_mode=ParseMode.MARKDOWN,
    )

async def run_once():
    bot  = Bot(token=TELEGRAM_BOT_TOKEN)
    sent = await fetch_and_send(bot)
    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=f"✅ Daily digest done — {sent} new articles sent.\nType /analyze for AI briefing.",
    )

def run_listener():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("news",    cmd_news))
    app.add_handler(CommandHandler("analyze", cmd_analyze))
    app.add_handler(CommandHandler("help",    cmd_help))
    log.info("Bot ready — /news and /analyze active.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    if MODE == "listen":
        run_listener()
    else:
        asyncio.run(run_once())
