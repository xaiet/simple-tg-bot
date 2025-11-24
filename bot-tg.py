import os
import random
import logging
import requests
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

# -----------------------------
# Config
# -----------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
COINGECKO_BASE = "https://api.coingecko.com/api/v3"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("simple-mcap-bot")

# -----------------------------
# Helpers
# -----------------------------
def fmt_usd(x):
    if x is None:
        return "?"
    if x >= 1_000_000_000:
        return f"${x/1_000_000_000:.2f}B"
    if x >= 1_000_000:
        return f"${x/1_000_000:.2f}M"
    if x >= 1_000:
        return f"${x/1_000:.2f}k"
    return f"${x:.2f}"

def safe_pct(x):
    return f"{x:.2f}%" if x is not None else "?"

def coingecko_get(path, params=None):
    url = f"{COINGECKO_BASE}{path}"
    r = requests.get(url, params=params or {}, timeout=30)
    if r.status_code == 429:
        logger.warning("Rate limit 429, esperant 10s...")
        import time; time.sleep(10)
        r = requests.get(url, params=params or {}, timeout=30)
    r.raise_for_status()
    return r.json()

# -----------------------------
# Coin fetch
# -----------------------------
def fetch_coin_by_rank(rank: int):
    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": 1,
        "page": rank,
        "price_change_percentage": "24h",
    }
    data = coingecko_get("/coins/markets", params)
    if not data:
        raise RuntimeError(f"No coin found at rank #{rank}")
    return data[0]

def fetch_coin_details(coin_id: str):
    params = {"localization": "false", "tickers": "false", "market_data": "true"}
    return coingecko_get(f"/coins/{coin_id}", params)

def build_message(coin, rank):
    lines = [
        f"• **Nom:** {coin['name']} ({coin['symbol'].upper()})",
        f"• **Rànquing MC:** #{rank}",
        f"• **Preu:** {fmt_usd(coin.get('current_price'))}",
        f"• **Market Cap:** {fmt_usd(coin.get('market_cap'))}",
        f"• **Vol 24h:** {fmt_usd(coin.get('total_volume'))}",
        f"• **Canvi 24h:** {safe_pct(coin.get('price_change_percentage_24h'))}",
    ]
    msg = f"**🎲 Coin #{rank} per MC (1–500)**\n" + "\n".join(lines)
    return msg

def pick_coin_text_by_rank(rank: int):
    coin = fetch_coin_by_rank(rank)
    return build_message(coin, rank)

def pick_random_coin_text(max_rank: int = 500):
    rank = random.randint(1, max_rank)
    return pick_coin_text_by_rank(rank)

# -----------------------------
# Handlers
# -----------------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot actiu! Envia /now o /now <rànquing> per veure una coin aleatòria.")

async def now_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rank = None
    if context.args:
        try:
            rank = int(context.args[0])
        except ValueError:
            pass
    try:
        msg = pick_coin_text_by_rank(rank) if rank else pick_random_coin_text()
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.exception("Error in /now")
        await update.message.reply_text(f"❌ Error: {e}")

async def id_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    c = update.effective_chat
    await update.message.reply_text(f"Chat ID: {c.id}\nType: {c.type}")

# -----------------------------
# Main (Webhook)
# -----------------------------
def main():
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit("Falta TELEGRAM_BOT_TOKEN")
    public_base = os.getenv("WEBHOOK_BASE") or os.getenv("RENDER_EXTERNAL_URL")
    if not public_base:
        raise SystemExit("Falta WEBHOOK_BASE (o RENDER_EXTERNAL_URL)")

    port = int(os.getenv("PORT", "10000"))
    url_path = f"webhook/{TELEGRAM_BOT_TOKEN}"
    webhook_url = f"{public_base.rstrip('/')}/{url_path}"

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("now", now_cmd))
    app.add_handler(CommandHandler("id", id_cmd))

    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=url_path,
        webhook_url=webhook_url,
        drop_pending_updates=True,
    )

if __name__ == "__main__":
    main()
