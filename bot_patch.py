"""
PATCH for bot.py — add these changes to integrate the analyst.

1. Add articles_cache.json writing to fetch_and_send()
2. Add /analyze command handler
3. Wire up the command in run_listener()
"""

# ── STEP 1: At the top of bot.py, add these imports ──────────────────────────
# (add alongside existing imports)

from analyst import analyse_and_notify   # <-- add this import

ARTICLES_CACHE = Path("articles_cache.json")  # <-- add this constant


# ── STEP 2: Replace your format_message + send block inside fetch_and_send()
# to also collect articles into a cache for the analyst.
# 
# Add this list at the TOP of fetch_and_send(), before the for loop:
#
#   collected = []
#
# Then inside the loop, after you confirm an article is relevant and BEFORE
# sending to Telegram, add:
#
#   collected.append({
#       "category": feed_cfg["cat"],
#       "title": title,
#       "summary": summary,
#       "link": getattr(entry, "link", ""),
#   })
#
# Then at the END of fetch_and_send(), before save_seen(seen), add:
#
#   if collected:
#       existing = []
#       if ARTICLES_CACHE.exists():
#           existing = json.loads(ARTICLES_CACHE.read_text())
#       existing.extend(collected)
#       ARTICLES_CACHE.write_text(json.dumps(existing[-200:], ensure_ascii=False))


# ── STEP 3: Add this command handler function ─────────────────────────────────

async def cmd_analyze(update, context):
    await update.message.reply_text("🧠 Running analysis... this takes ~30 seconds.")
    try:
        analysis = await analyse_and_notify()
        if "error" in analysis:
            await update.message.reply_text(f"❌ {analysis['error']}")
            return
        n = analysis.get("article_count", 0)
        overview = analysis.get("overview", "")
        await update.message.reply_text(
            f"✅ Analysis complete — {n} articles processed.\n\n{overview}\n\n"
            f"Open your dashboard to see the full report.",
            parse_mode=None,
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Analysis failed: {e}")


# ── STEP 4: In run_listener(), add this line alongside the /news handler:
#
#   app.add_handler(CommandHandler("analyze", cmd_analyze))
