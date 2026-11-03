
import os
import requests
from dotenv import load_dotenv

load_dotenv()

token = os.getenv("TELEGRAM_BOT_TOKEN")
canale = os.getenv("TELEGRAM_CHANNEL_ID")

if not token or not canale:
    raise SystemExit("Credenziali Telegram mancanti nel file .env")

risposta = requests.post(
    f"https://api.telegram.org/bot{token}/sendMessage",
    json={
        "chat_id": canale,
        "text": (
            "🏛️ SCAFATI INFORMA\n\n"
            "✅ Test dal Raspberry Pi riuscito!\n\n"
            "Il collegamento con Telegram funziona.\n"
            "Messaggio di prova - Canale non ufficiale."
        )
    },
    timeout=20
)

if risposta.ok and risposta.json().get("ok"):
    print("Messaggio inviato correttamente!")
else:
    print("Invio non riuscito:", risposta.status_code)
