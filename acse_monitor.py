
import os
import sqlite3
import logging
from pathlib import Path
from xml.etree import ElementTree as ET

import requests
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")

FEED_URL = "https://acsespa.it/feed/"
DB = BASE / "acse.db"

DRY_RUN = os.getenv("ACSE_DRY_RUN", "true").lower() != "false"
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL = os.getenv("TELEGRAM_CHANNEL_ID")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)


def leggi_comunicati():
    
import subprocess

result = subprocess.run(
    ["curl", "--fail", "--silent", "--show-error",
     "--location", "--max-time", "30", FEED_URL],
    capture_output=True,
    check=True
)

root = ET.fromstring(result.stdout)
    comunicati = {}

    for item in root.findall("./channel/item"):
        titolo = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        data = (item.findtext("pubDate") or "").strip()

        if not titolo or not url:
            continue

        if not url.startswith(("https://acsespa.it/",
                               "https://www.acsespa.it/")):
            continue

        comunicati[url] = {
            "titolo": titolo,
            "data": data,
            "url": url
        }

    if not comunicati:
        raise RuntimeError("Nessun comunicato trovato nel feed RSS")

    return list(comunicati.values())


def controlla():
    comunicati = leggi_comunicati()
    logging.info("Comunicati disponibili: %d", len(comunicati))

    with sqlite3.connect(DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS comunicati (
                url TEXT PRIMARY KEY,
                titolo TEXT NOT NULL,
                data TEXT,
                stato TEXT NOT NULL
            )
        """)

        totale = conn.execute(
            "SELECT COUNT(*) FROM comunicati"
        ).fetchone()[0]

        if totale == 0:
            for item in comunicati:
                conn.execute(
                    "INSERT OR IGNORE INTO comunicati VALUES (?, ?, ?, 'storico')",
                    (item["url"], item["titolo"], item["data"])
                )
            logging.info("Prima esecuzione: storico memorizzato")
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

        if not TOKEN or not CHANNEL:
            raise RuntimeError("Credenziali Telegram mancanti")

        # Per sicurezza non pubblica automaticamente:
        # i contenuti devono prima essere verificati.
        logging.warning(
            "Invio automatico non ancora abilitato"
        )


if __name__ == "__main__":
    controlla()
    logging.info("Controllo ACSE terminato")
