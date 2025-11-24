# random-mcap-telegram-bot.py — Webhook mode (Render Free)
# Optimized + robust: single-coin fetch by MC rank, retry/backoff on 429, and 10min cache

import os
import time
import logging
import random

import pytz
import requests
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

# -----------------------------
# Config
# -----------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TIMEZONE = pytz.timezone("Europe/Madrid")

ALLOWED_CHAT_ID = os.getenv("ALLOWED_CHAT_ID")
if ALLOWED_CHAT_ID:
    try:
        ALLOWED_CHAT_ID = int(ALLOWED_CHAT_ID)
    except ValueError:
        ALLOWED_CHAT_ID = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("random-mcap-bot")

COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY")  # opcional (API Pro)
# Usa automàticament el domini Pro si hi ha API key
COINGECKO_BASE = os.getenv("COINGECKO_BASE") or (
    "https://pro-api.coingecko.com/api/v3" if COINGECKO_API_KEY else "https://api.coingecko.com/api/v3"
)

# -----------------------------
# Cache (10 minuts)
# -----------------------------
CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", "600"))
_cache_coin_by_rank = {}   # {rank: {"data": coin, "ts": epoch}}
_cache_coin_details = {}   # {coin_id: {"data": details, "ts": epoch}}

# -----------------------------
# Helpers
# -----------------------------
def fmt_usd(x):
    try:
        if x is None: return "?"
        if x >= 1_000_000_000: return f"${x/1_000_000_000:.2f}B"
        if x >= 1_000_000:     return f"${x/1_000_000:.2f}M"
        if x >= 1_000:         return f"${x/1_000:.2f}k"
        return f"${x:.2f}"
    except Exception:
        return "?"

def safe_pct(x):
    return f"{x:.2f}%" if x is not None else "?"


def _headers():
    h = {"Accept": "application/json", "User-Agent": "random-mcap-telegram-bot/1.1"}
    if COINGECKO_API_KEY:
        h["x-cg-pro-api-key"] = COINGECKO_API_KEY
    return h


def coingecko_get(path, params=None, retries=3):
    """GET amb backoff exponencial i suport de Retry-After.
    Lança RuntimeError si persisteix l'error 429 o altres HTTP errors.
    """
    url = f"{COINGECKO_BASE}{path}"
    params = params or {}
    for attempt in range(retries):
        r = requests.get(url, params=params, timeout=30, headers=_headers())
        # Rate limit explícit
        if r.status_code == 429:
            # Respecta Retry-After si hi és
            retry_after = r.headers.get("Retry-After")
            if retry_after:
                try:
                    wait = max(1, int(retry_after))
                except ValueError:
                    wait = 2 ** attempt
            else:
                wait = 2 ** attempt
            logger.warning(f"429 Too Many Requests a {path}. Esperant {wait}s i reintentant (attempt {attempt+1}/{retries})...")
            time.sleep(wait)
            continue
        # Altres errors HTTP
        try:
            r.raise_for_status()
        except requests.HTTPError as e:
            # Reintents suaus per 5xx
            if 500 <= r.status_code < 600 and attempt < retries - 1:
                wait = 2 ** attempt
                logger.warning(f"{r.status_code} a {path}. Esperant {wait}s...")
                time.sleep(wait)
                continue
            raise RuntimeError(f"CoinGecko error {r.status_code}: {r.text}") from e
        return r.json()
    raise RuntimeError("CoinGecko rate limit persistent (429)")


# ---- API d'alt nivell ----

def _cache_get(cache, key):
    item = cache.get(key)
    if item and (time.time() - item["ts"] < CACHE_TTL):
        return item["data"]
    return None


def _cache_put(cache, key, data):
    cache[key] = {"data": data, "ts": time.time()}


# Fetch exactament una coin pel seu rànquing de MC (per_page=1 & page=rank)
# Usa cache i reintenta si CoinGecko retorna buid (p. ex. rank fora d'abast temporalment)

def fetch_coin_by_rank(rank: int):
    if rank < 1:
        raise ValueError("Rank must be >= 1")

    cached = _cache_get(_cache_coin_by_rank, rank)
    if cached:
        return cached

    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": 1,
        "page": rank,
        "price_change_percentage": "24h",
        "sparkline": "false",
        "locale": "en",
    }
    data = coingecko_get("/coins/markets", params)
    if not data:
        # Pot passar si el rànquing supera el nombre de monedes disponibles
        raise RuntimeError(f"No coin found at MC rank #{rank}")

    coin = data[0]
    _cache_put(_cache_coin_by_rank, rank, coin)
    return coin


def fetch_coin_details(coin_id: str):
    cached = _cache_get(_cache_coin_details, coin_id)
    if cached:
        return cached

    params = {"localization": "false", "tickers": "false", "market_data": "true"}
    details = coingecko_get(f"/coins/{coin_id}", params)
    _cache_put(_cache_coin_details, coin_id, details)
    return details


def analyze_tokenomics(md):
    circ, total, maxs = md.get("circulating_supply"), md.get("total_supply"), md.get("max_supply")
    mcap = (md.get("market_cap") or {}).get("usd")
    fdv = (md.get("fully_diluted_valuation") or {}).get("usd")
    vol24 = (md.get("total_volume") or {}).get("usd")
    chg24 = (md.get("price_change_percentage_24h") or 0)
    circ_ratio = circ/(maxs or total) if (circ and (maxs or total)) else None
    vol_mcap = vol24/mcap if (vol24 and mcap) else None
    mcap_fdv = mcap/fdv if (mcap and fdv and fdv != 0) else None

    score, notes = 5, []
    if circ_ratio is not None:
        if circ_ratio >= 0.8: score += 2; notes.append("Alta circulació (>=80%)")
        elif circ_ratio >= 0.5: score += 1; notes.append("Circulació mitjana (50–80%)")
        else: notes.append("Baixa circulació (<50%)")
    if mcap_fdv is not None:
        if mcap_fdv >= 0.7: score += 2; notes.append("MC/FDV alt (>=0.7)")
        elif mcap_fdv >= 0.4: score += 1; notes.append("MC/FDV moderat (0.4–0.7)")
        else: notes.append("MC/FDV baix (<0.4)")
    if vol_mcap is not None:
        if vol_mcap >= 0.2: score += 2; notes.append("Liquiditat alta (Vol/MC>=0.2)")
        elif vol_mcap >= 0.05: score += 1; notes.append("Liquiditat correcta (0.05–0.2)")
        else: notes.append("Liquiditat baixa (<0.05)")
    if abs(chg24) >= 30: score -= 2; notes.append("Volatilitat molt alta")
    elif abs(chg24) >= 15: score -= 1; notes.append("Volatilitat alta")

    score = max(1, min(10, score))
    return {"score": score, "notes": notes, "circ_ratio": circ_ratio, "mcap_fdv": mcap_fdv, "vol_mcap": vol_mcap}


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
    msg = f"**🎲 Coin aleatòria #{rank} per MC (1–500)**" + "".join(lines)
    msg += "**🔎 Anàlisi de tokenomics:**" + "".join([f"- {n}" for n in ana['notes']])
    msg += f"**Puntuació:** {ana['score']}/10"
    return msg


# -----------------------------
# Core selection
# -----------------------------

def pick_coin_text_by_rank(rank: int):
    coin = fetch_coin_by_rank(rank)
    details = fetch_coin_details(coin['id'])
    return build_message(coin, details, rank)


def pick_random_coin_text(max_rank: int = 500):
    # Intenta fins a 5 cops si algun rank torna buit o dóna rate-limit durador
    last_err = None
    for _ in range(5):
        rank = random.randint(1, max_rank)
        try:
            return pick_coin_text_by_rank(rank)
        except Exception as e:
            last_err = e
            logger.warning(f"Error recuperant rank aleatori: {e}")
            time.sleep(0.5)
    raise last_err if last_err else RuntimeError("No s'ha pogut obtenir cap coin")


# -----------------------------
# Handlers
# -----------------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot actiu! Envia /now per obtenir l'anàlisi. Pots fer /now 123 per un rànquing concret.")


async def now_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Optional: /now <rank>
    rank = None
    if context.args:
        try:
            rank = int(context.args[0])
        except ValueError:
            rank = None
    try:
        if rank is not None:
            if not (1 <= rank <= 500):
                await update.message.reply_text("Indica un rànquing entre 1 i 500, ex: /now 73")
                return
            msg = pick_coin_text_by_rank(rank)
        else:
            msg = pick_random_coin_text(500)
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.exception("Error in /now")
        await update.message.reply_text(f"S'ha produït un error: {e}")


async def id_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    c = update.effective_chat
    await update.message.reply_text(f"Chat ID: {c.id}Type: {c.type}")


# -----------------------------
# Main (Webhook)
# -----------------------------

def main():
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit("Falta TELEGRAM_BOT_TOKEN")

    # URL pública (Render)
    public_base = os.getenv("WEBHOOK_BASE") or os.getenv("RENDER_EXTERNAL_URL")
    if not public_base:
        raise SystemExit("Falta WEBHOOK_BASE (o RENDER_EXTERNAL_URL) amb la URL pública del servei")

    # Port i camí del webhook
    port = int(os.getenv("PORT", "10000"))
    url_path = f"webhook/{TELEGRAM_BOT_TOKEN}"        # endpoint privat
    webhook_url = f"{public_base.rstrip('/')}/{url_path}"

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("now", now_cmd))
    app.add_handler(CommandHandler("id", id_cmd))

    # Executa el servidor web i registra el webhook
    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        url_path=url_path,
        webhook_url=webhook_url,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()