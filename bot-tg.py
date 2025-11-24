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
CMC_API_KEY = os.getenv("COINMARKETCAP_API_KEY")
CMC_BASE = "https://pro-api.coinmarketcap.com/v1"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("cmc-bot")

HEADERS = {"Accept": "application/json", "X-CMC_PRO_API_KEY": CMC_API_KEY}

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

# -----------------------------
# Core logic
# -----------------------------
def fetch_coin_by_rank(rank: int):
    """Demana exactament la moneda #rank (1-500) per Market Cap."""
    url = f"{CMC_BASE}/cryptocurrency/listings/latest"
    params = {"start": rank, "limit": 1, "convert": "USD"}
    r = requests.get(url, params=params, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json().get("data", [])
    if not data:
        raise RuntimeError(f"No s'ha trobat la moneda #{rank}")
    return data[0]

def build_message(coin):
    q = coin["quote"]["USD"]
    lines = [
        f"• **Nom:** {coin['name']} ({coin['symbol']})",
        f"• **Rànquing MC:** #{coin['cmc_rank']}",
        f"• **Preu:** {fmt_usd(q.get('price'))}",
        f"• **Market Cap:** {fmt_usd(q.get('market_cap'))}",
        f"• **Vol 24h:** {fmt_usd(q.get('volume_24h'))}",
        f"• **Canvi 24h:** {safe_pct(q.get('percent_change_24h'))}",
    ]
    msg = f"**🎲 Coin #{coin['cmc_rank']} per MC (1–500)**\n" + "\n".join(lines)
    return msg

def pick_random_coin_text(max_rank: int = 500):
    rank = random.randint(1, max_rank)
    coin = fetch_coin_by_rank(rank)
    return build_message(coin)

# -----------------------------
# Handlers
# -----------------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot actiu ✅ Envia /now o /now <rànquing> per veure una coin per Market Cap.")

async def now_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rank = None
    if context.args:
        try:
            rank = int(context.args[0])
        except ValueError:
            pass
    try:
        if rank is None:
            msg = pick_random_coin_text()
        else:
            if not (1 <= rank <= 500):
                await update.message.reply_text("Introdueix un rànquing entre 1 i 500, ex: /now 73")
                return
            coin = fetch_coin_by_rank(rank)
            msg = build_message(coin)
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
