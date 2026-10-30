
import os
import re
import sqlite3
import logging
import time
import requests

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from urllib.parse import urljoin

load_dotenv()

URL = "https://scafati.soluzionipa.it/openweb/albo/albo_pretorio.php"
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL = os.getenv("TELEGRAM_CHANNEL_ID")
DB = os.getenv("DB_PATH", "scafati.db")
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)


def inizializza_db():
    with sqlite3.connect(DB) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS atti (
                id TEXT PRIMARY KEY,
                numero TEXT,
                oggetto TEXT,
                tipo TEXT,
                data_pubblicazione TEXT,
                url TEXT,
                stato TEXT NOT NULL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS configurazione (
                chiave TEXT PRIMARY KEY,
                valore TEXT
            )
        """)


def leggi_atti():
    session = requests.Session()
    response = session.get(URL, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    atti = {}

    # Legge la prima pagina e segue i link di paginazione.
    pagine = [URL]
    visitate = set()

    while pagine:
        pagina = pagine.pop(0)

        if pagina in visitate:
            continue

        visitate.add(pagina)

        if pagina != URL:
            response = session.get(pagina, timeout=30)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

        tabella = soup.select_one("#tabella_albo")
        if tabella is None:
            raise RuntimeError("Tabella Albo Pretorio non trovata")

        for riga in tabella.select("tbody tr"):
            celle = riga.find_all("td")

            if len(celle) < 5:
                continue

            link = celle[0].find("a", href=True)
            if not link:
                continue

            url = urljoin(URL, link["href"])
            match = re.search(r"[?&]id=(\d+)", url)

            if not match:
                continue

            tipo = celle[2].get_text(" ", strip=True)
            oggetto = celle[1].get_text(" ", strip=True)

            # Esclusioni iniziali per privacy.
            if any(x in tipo.lower() for x in (
                "matrimonio", "atti giudiziari"
            )):
                continue

            if "PUBBLICAZIONE ANNULLATA" in oggetto.upper():
                continue

            atti[match.group(1)] = {
                "id": match.group(1),
                "numero": celle[0].get_text(" ", strip=True),
                "oggetto": oggetto,
                "tipo": tipo,
                "data": celle[3].get_text(" ", strip=True),
                "url": url
            }

        for link in soup.select("#tabella_albo_pageForm a[href]"):
            href = urljoin(URL, link["href"])
            if href not in visitate and href not in pagine:
                pagine.append(href)

        if len(visitate) > 100:
            raise RuntimeError("Troppe pagine: controllo interrotto")

    return sorted(atti.values(), key=lambda a: int(a["id"]))


def invia_telegram(atto):
    if DRY_RUN:
        logging.info("SIMULAZIONE Telegram: %s", atto["numero"])
        return False

    if not TOKEN or not CHANNEL:
        raise RuntimeError("Credenziali Telegram mancanti")

    messaggio = (
        "🏛️ NUOVO ATTO - ALBO PRETORIO SCAFATI\n\n"
        f"📌 Numero: {atto['numero']}\n"
        f"📄 Tipo: {atto['tipo']}\n"
        f"📝 Oggetto: {atto['oggetto'][:2000]}\n"
        f"📅 Pubblicazione: {atto['data']}\n\n"
        f"🔗 Documento: {atto['url']}\n\n"
        "Fonte: Comune di Scafati\n"
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

    return True


def controlla():
    atti = leggi_atti()
    logging.info("Atti disponibili: %s", len(atti))

    with sqlite3.connect(DB) as conn:
        inizializzato = conn.execute(
            "SELECT valore FROM configurazione WHERE chiave = 'bootstrap'"
        ).fetchone()

        if not inizializzato:
            for atto in atti:
                conn.execute("""
                    INSERT OR IGNORE INTO atti
                    VALUES (?, ?, ?, ?, ?, ?, 'storico')
                """, (
                    atto["id"], atto["numero"], atto["oggetto"],
                    atto["tipo"], atto["data"], atto["url"]
                ))

            conn.execute("""
                INSERT INTO configurazione VALUES ('bootstrap', 'ok')
            """)

            logging.info(
                "Prima esecuzione: %s atti memorizzati senza invio",
                len(atti)
            )
            return

        for atto in atti:
            conn.execute("""
                INSERT OR IGNORE INTO atti
                VALUES (?, ?, ?, ?, ?, ?, 'da_inviare')
            """, (
                atto["id"], atto["numero"], atto["oggetto"],
                atto["tipo"], atto["data"], atto["url"]
            ))

        conn.commit()

        pendenti = conn.execute("""
            SELECT id, numero, oggetto, tipo,
                   data_pubblicazione, url
            FROM atti
            WHERE stato = 'da_inviare'
            ORDER BY CAST(id AS INTEGER)
        """).fetchall()

        logging.info("Nuovi atti da inviare: %s", len(pendenti))

        for id_, numero, oggetto, tipo, data, url in pendenti:
            atto = {
                "id": id_, "numero": numero,
                "oggetto": oggetto, "tipo": tipo,
                "data": data, "url": url
            }

            try:
                if invia_telegram(atto):
                    conn.execute(
                        "UPDATE atti SET stato = 'inviato' WHERE id = ?",
                        (id_,)
                    )
                    conn.commit()
                    logging.info("Pubblicato: %s", numero)
                time.sleep(1)

            except Exception:
                logging.exception("Invio fallito: %s", numero)


if __name__ == "__main__":
    inizializza_db()
    controlla()
    logging.info("Controllo terminato")
