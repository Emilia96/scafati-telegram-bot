
import os
import sqlite3
import logging
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")

URL = "https://acsespa.it/comunicati-2/"
DB = BASE / "acse.db"
DRY_RUN = os.getenv("ACSE_DRY_RUN", "true").lower() != "false"
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL = os.getenv("TELEGRAM_CHANNEL_ID")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)


def leggi_comunicati():
    response = requests.get(URL, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    articoli = soup.select(
        "#avia-magazine-1 article.av-magazine-entry"
    )

    if not articoli:
        raise RuntimeError("Nessun comunicato trovato: verificare HTML")

    comunicati = {}

    for articolo in articoli:
        link = articolo.select_one(".av-magazine-title a[href]")
        data = articolo.select_one("time.av-magazine-time")

        if not link or not data:
            continue

        titolo = link.get_text(" ", strip=True)
        indirizzo = urljoin(URL, link["href"])
        host = urlparse(indirizzo).hostname

        if host not in ("acsespa.it", "www.acsespa.it"):
            continue

        if not titolo:
            continue

        comunicati[indirizzo] = {
            "titolo": titolo,
            "data": data.get_text(" ", strip=True),
            "url": indirizzo
        }

    if not comunicati:
        raise RuntimeError("Nessun comunicato valido trovato")

    return list(comunicati.values())


def inizializza_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS comunicati (
            url TEXT PRIMARY KEY,
            titolo TEXT NOT NULL,
            data TEXT,
            stato TEXT NOT NULL
        )
    """)


def invia_telegram(item):
    if not TOKEN or not CHANNEL:
        raise RuntimeError("Credenziali Telegram mancanti")

    # Invia soltanto titolo, data e collegamento ufficiale.
    messaggio = (
        "📢 NUOVO COMUNICATO ACSE SCAFATI\n\n"
        f"📌 {item['titolo'][:500]}\n"
        f"📅 {item['data']}\n\n"
        f"🔗 {item['url']}\n\n"
        "Fonte: ACSE S.p.A.\n"
        "Canale informativo non ufficiale."
    )

    response = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": CHANNEL, "text": messaggio},
        timeout=30
    )
    response.raise_for_status()

    if not response.json().get("ok"):
        raise RuntimeError("Telegram non ha confermato l'invio")


def controlla():
    comunicati = leggi_comunicati()
    logging.info("Comunicati disponibili: %d", len(comunicati))

    with sqlite3.connect(DB) as conn:
        inizializza_db(conn)

        totale = conn.execute(
            "SELECT COUNT(*) FROM comunicati"
        ).fetchone()[0]

        if totale == 0:
            for item in comunicati:
                conn.execute(
                    "INSERT INTO comunicati VALUES (?, ?, ?, 'storico')",
                    (item["url"], item["titolo"], item["data"])
                )
            logging.info("Storico inizializzato senza invii")
            return

        for item in comunicati:
            conn.execute(
                """INSERT OR IGNORE INTO comunicati
                   VALUES (?, ?, ?, 'da_inviare')""",
                (item["url"], item["titolo"], item["data"])
            )

        conn.commit()

        pendenti = conn.execute("""
            SELECT url, titolo, data FROM comunicati
            WHERE stato = 'da_inviare'
            ORDER BY rowid
        """).fetchall()

        logging.info("Nuovi comunicati: %d", len(pendenti))

        if DRY_RUN:
            logging.info("Modalita simulazione: nessun invio")
            return

        # Per prudenza richiede un'approvazione esplicita
        # prima di pubblicare nuovi titoli.
        logging.warning(
            "Invio automatico disabilitato fino alla "
            "verifica dei contenuti pubblicabili"
        )


if __name__ == "__main__":
    controlla()
    logging.info("Controllo ACSE terminato")
