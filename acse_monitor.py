
import os
import sqlite3
import logging
import subprocess
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import requests
from dotenv import load_dotenv


# CONFIGURAZIONE
BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")

FEED_URL = "https://acsespa.it/feed/"
DB = BASE / "acse.db"

DRY_RUN = os.getenv(
    "ACSE_DRY_RUN", "true"
).lower() != "false"

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL = os.getenv("TELEGRAM_CHANNEL_ID")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)


# FILTRI PRUDENZIALI
PAROLE_ESCLUSE = (
    "graduatoria",
    "candidati",
    "sorteggio",
    "esiti prova",
    "elenco ammessi",
    "matrimonio",
    "atti giudiziari",
    "dati personali",
    "verbale selezione"
)


def titolo_da_verificare(titolo):
    return any(
        parola in titolo.lower()
        for parola in PAROLE_ESCLUSE
    )


# LETTURA RSS
def leggi_comunicati():
    result = subprocess.run(
        [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--location",
            "--max-time", "30",
            FEED_URL
        ],
        capture_output=True,
        check=True,
        timeout=35
    )

    root = ET.fromstring(result.stdout)
    comunicati = {}

    for item in root.findall("./channel/item"):
        titolo = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        data = (item.findtext("pubDate") or "").strip()

        if not titolo or not url:
            continue

        parsed = urlparse(url)

        if (
            parsed.scheme != "https"
            or parsed.hostname not in (
                "acsespa.it",
                "www.acsespa.it"
            )
        ):
            continue

        comunicati[url] = {
            "titolo": titolo,
            "data": data,
            "url": url
        }

    if not comunicati:
        raise RuntimeError(
            "Nessun comunicato trovato nel feed RSS"
        )

    return list(comunicati.values())


# DATABASE
def inizializza_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS comunicati (
            url TEXT PRIMARY KEY,
            titolo TEXT NOT NULL,
            data TEXT,
            stato TEXT NOT NULL
        )
    """)


# INVIO TELEGRAM
def invia_telegram(url, titolo, data):
    if not TOKEN or not CHANNEL:
        raise RuntimeError(
            "Credenziali Telegram mancanti"
        )

    messaggio = (
        "📢 NUOVO COMUNICATO ACSE SCAFATI\n\n"
        f"📌 {titolo[:500]}\n"
        f"📅 {data}\n\n"
        f"🔗 {url}\n\n"
        "Fonte: ACSE S.p.A.\n"
        "Canale informativo non ufficiale."
    )

    response = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={
            "chat_id": CHANNEL,
            "text": messaggio,
            "disable_web_page_preview": True
        },
        timeout=30
    )

    response.raise_for_status()

    if not response.json().get("ok"):
        raise RuntimeError(
            "Telegram non ha confermato l'invio"
        )


# CONTROLLO PRINCIPALE
def controlla():
    comunicati = leggi_comunicati()

    logging.info(
        "Comunicati disponibili: %d",
        len(comunicati)
    )

    with sqlite3.connect(DB) as conn:
        inizializza_db(conn)

        totale = conn.execute(
            "SELECT COUNT(*) FROM comunicati"
        ).fetchone()[0]

        # Prima esecuzione: memorizza lo storico
        if totale == 0:
            for item in comunicati:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO comunicati
                    VALUES (?, ?, ?, 'storico')
                    """,
                    (
                        item["url"],
                        item["titolo"],
                        item["data"]
                    )
                )

            conn.commit()
            logging.info(
                "Prima esecuzione: storico memorizzato"
            )
            return

        # Registra nuovi comunicati
        for item in comunicati:
            conn.execute(
                """
                INSERT OR IGNORE INTO comunicati
                VALUES (?, ?, ?, 'da_inviare')
                """,
                (
                    item["url"],
                    item["titolo"],
                    item["data"]
                )
            )

        conn.commit()

        pendenti = conn.execute("""
            SELECT url, titolo, data
            FROM comunicati
            WHERE stato = 'da_inviare'
            ORDER BY rowid
        """).fetchall()

        logging.info(
            "Nuovi comunicati da elaborare: %d",
            len(pendenti)
        )

        if DRY_RUN:
            logging.info(
                "SIMULAZIONE: nessun messaggio inviato"
            )
            return

        if not TOKEN or not CHANNEL:
            raise RuntimeError(
                "Credenziali Telegram mancanti"
            )

        # Invio dei nuovi comunicati
        for url, titolo, data in pendenti:

            if titolo_da_verificare(titolo):
                logging.warning(
                    "Comunicazione da verificare: %s",
                    titolo
                )

                conn.execute(
                    """
                    UPDATE comunicati
                    SET stato = 'da_verificare'
                    WHERE url = ?
                    """,
                    (url,)
                )
                conn.commit()
                continue

            try:
                invia_telegram(url, titolo, data)

                conn.execute(
                    """
                    UPDATE comunicati
                    SET stato = 'inviato'
                    WHERE url = ?
                    """,
                    (url,)
                )
                conn.commit()

                logging.info(
                    "Comunicazione inviata: %s",
                    titolo
                )

            except (
                requests.RequestException,
                RuntimeError
            ) as errore:
                logging.error(
                    "Errore invio %s: %s",
                    titolo,
                    errore
                )

    logging.info(
        "Controllo ACSE completato"
    )


if __name__ == "__main__":
    controlla()
    logging.info("Controllo ACSE terminato")
