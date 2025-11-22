# random-mcap-telegram-bot.py (versió Render Web Service Free)

import os
import asyncio
import logging
import random
import argparse
from datetime import datetime

import pytz
import requests
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram import Update, Bot
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

# -----------------------------
# Config
# -----------------------------

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TIMEZONE = pytz.timezone("Europe/Madrid")
DAILY_HOUR = int(os.getenv("DAILY_HOUR", 9))
ALLOWED_CHAT_ID = os.getenv("ALLOWED_CHAT_ID")
if ALLOWED_CHAT_ID:
    try:
        ALLOWED_CHAT_ID = int(ALLOWED_CHAT_ID)
    except ValueError:
        ALLOWED_CHAT_ID = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("random-mcap-bot")
COINGECKO_BASE = "https://api.coingecko.com/api/v3"

# -----------------------------
# Keep-alive HTTP server (Render Free Web Service)
# -----------------------------

def start_keepalive_http_server():
    import threading, http.server, socketserver
    PORT = int(os.environ.get("PORT", 8080))
    class SilentHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, format, *args):
            return
    class SilentTCP(socketserver.TCPServer):
        allow_reuse_address = True
    httpd = SilentTCP(("", PORT), SilentHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    logger.info(f"Keep-alive HTTP server running on port {PORT}")

# -----------------------------
# Helpers
# -----------------------------

def fmt_usd(x):
    try:
        if x is None:
            return "?"
        if x >= 1_000_000_000:
            return f"${x/1_000_000_000:.2f}B"
        if x >= 1_000_000:
            return f"${x/1_000_000:.2f}M"
        if x >= 1_000:
            return f"${x/1_000:.2f}k"
        return f"${x:.2f}"
    except Exception:
        return "?"

def safe_pct(x):
    return f"{x:.2f}%" if x is not None else "?"

def coingecko_get(path, params=None):
    url = f"{COINGECKO_BASE}{path}"
    r = requests.get(url, params=params or {}, timeout=30)
    r.raise_for_status()
    return r.json()

def fetch_top_500_marketcap():
    params = {"vs_currency": "usd", "order": "market_cap_desc", "per_page": 250, "price_change_percentage": "24h"}
    data = coingecko_get("/coins/markets", {**params, "page": 1}) + coingecko_get("/coins/markets", {**params, "page": 2})
    return data[:500]

def fetch_coin_details(coin_id: str):
    params = {"localization": "false", "tickers": "false", "market_data": "true"}
    return coingecko_get(f"/coins/{coin_id}", params)

def analyze_tokenomics(md):
    circ, total, maxs = md.get("circulating_supply"), md.get("total_supply"), md.get("max_supply")
    mcap = (md.get("market_cap") or {}).get("usd")
    fdv = (md.get("fully_diluted_valuation") or {}).get("usd")
    vol24 = (md.get("total_volume") or {}).get("usd")
    chg24 = (md.get("price_change_percentage_24h") or 0)
    circ_ratio = circ/(maxs or total) if (circ and (maxs or total)) else None
    vol_mcap = vol24/mcap if (vol24 and mcap) else None
    mcap_fdv = mcap/fdv if (mcap and fdv and fdv!=0) else None

    score, notes = 5, []
    if circ_ratio is not None:
        if circ_ratio >= 0.8: score+=2; notes.append("Alta circulació (>=80%)")
        elif circ_ratio >= 0.5: score+=1; notes.append("Circulació mitjana (50–80%)")
        else: notes.append("Baixa circulació (<50%)")
    if mcap_fdv is not None:
        if mcap_fdv>=0.7: score+=2; notes.append("MC/FDV alt (>=0.7)")
        elif mcap_fdv>=0.4: score+=1; notes.append("MC/FDV moderat (0.4–0.7)")
        else: notes.append("MC/FDV baix (<0.4)")
    if vol_mcap is not None:
        if vol_mcap>=0.2: score+=2; notes.append("Liquiditat alta (Vol/MC>=0.2)")
        elif vol_mcap>=0.05: score+=1; notes.append("Liquiditat correcta (0.05–0.2)")
        else: notes.append("Liquiditat baixa (<0.05)")
    if abs(chg24)>=30: score-=2; notes.append("Volatilitat molt alta")
    elif abs(chg24)>=15: score-=1; notes.append("Volatilitat alta")

    score = max(1, min(10, score))
    return {"score":score,"notes":notes,"circ_ratio":circ_ratio,"mcap_fdv":mcap_fdv,"vol_mcap":vol_mcap}

def build_message(coin, details, rank):
    md = details.get("market_data") or {}
    ana = analyze_tokenomics(md)
    lines = [
        f"• **Nom:** {coin['name']} ({coin['symbol'].upper()})",
        f"• **Rànquing MC:** #{rank}",
        f"• **Preu:** {fmt_usd(coin.get('current_price'))}",
        f"• **Market Cap:** {fmt_usd(coin.get('market_cap'))}",
        f"• **FDV:** {fmt_usd(coin.get('fully_diluted_valuation'))}",
        f"• **Vol 24h:** {fmt_usd(coin.get('total_volume'))}",
        f"• **Canvi 24h:** {safe_pct(coin.get('price_change_percentage_24h'))}",
    ]
    msg = f"**🎲 Coin aleatòria #{rank} per MC (1–500)**\n" + "\n".join(lines)
    msg += "\n\n**🔎 Anàlisi de tokenomics:**\n" + "\n".join([f"- {n}" for n in ana['notes']])
    msg += f"\n\n**Puntuació:** {ana['score']}/10"
    return msg

def pick_random_coin_text():
    coins = fetch_top_500_marketcap()
    X = random.randint(1, len(coins))
    coin = coins[X-1]
    details = fetch_coin_details(coin['id'])
    return build_message(coin, details, X)

async def send_daily(app: Application):
    if not ALLOWED_CHAT_ID:
        logger.warning("No ALLOWED_CHAT_ID definit.")
        return
    msg = pick_random_coin_text()
    await app.bot.send_message(chat_id=ALLOWED_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot actiu! Usa /now per veure una coin aleatòria.")

async def now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = pick_random_coin_text()
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


async def chat_id_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    c = update.effective_chat
    await update.message.reply_text(f"Chat ID: {c.id}\nType: {c.type}")

def schedule_daily(app: Application):
    sched = AsyncIOScheduler(timezone=TIMEZONE)
    sched.add_job(lambda: asyncio.create_task(send_daily(app)), CronTrigger(hour=DAILY_HOUR, minute=0))
    sched.start()

def main():
    start_keepalive_http_server()
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("now", now))
    app.add_handler(CommandHandler("id", chat_id_cmd))
    schedule_daily(app)
    logger.info("Bot en marxa… (Web Service)")
    app.run_polling()

if __name__ == "__main__":
    main()
