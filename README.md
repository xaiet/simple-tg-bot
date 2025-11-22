# README (Deploy a Render)
# simple-tg-bot 
1. Puja `random-mcap-telegram-bot.py`, `requirements.txt`, `render.yaml`, `.env.example` a GitHub.
2. A Render → New → Web Service.
3. Configura:
   - Build: `pip install -r requirements.txt`
   - Start: `python random-mcap-telegram-bot.py`
   - Env vars: `TELEGRAM_BOT_TOKEN`, `ALLOWED_CHAT_ID`.
4. El bot es mantindrà viu gràcies al petit servidor HTTP intern.
5. Envia `/start` o `/now` per provar-lo. A les 09:00 (Europe/Madrid) enviarà el missatge diari automàticament.
