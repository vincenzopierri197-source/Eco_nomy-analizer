#!/usr/bin/env python3
"""Invia via Telegram il bollettino normativo, il testo completo e l'audio."""
import json
import mimetypes
import os
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
REPORT = Path(os.environ["REPORT_PATH"])
AUDIO_TEXT = Path(os.environ["AUDIO_TEXT_PATH"])
AUDIO_MP3 = Path(os.environ["AUDIO_MP3_PATH"])


def send(method, fields, files=None):
    if not TOKEN or not CHAT_ID:
        raise RuntimeError("Mancano TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID nei secrets GitHub.")
    boundary = "----EcoNomy" + secrets.token_hex(16)
    chunks = []
    for name, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            str(value).encode("utf-8"),
            b"\r\n",
        ])
    for name, file_path, filename in (files or []):
        path = Path(file_path)
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode(),
            f"Content-Type: {mime}\r\n\r\n".encode(),
            path.read_bytes(),
            b"\r\n",
        ])
    chunks.append(f"--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{TOKEN}/{method}",
        data=b"".join(chunks),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram {method}: HTTP {exc.code} — {details[:500]}") from exc
    if not result.get("ok"):
        raise RuntimeError(f"Telegram {method}: {result.get('description', 'errore sconosciuto')}")
    print(f"Telegram {method}: OK")


def main():
    report = REPORT.read_text(encoding="utf-8")
    transcript = AUDIO_TEXT.read_text(encoding="utf-8").strip()

    # Messaggio breve con i collegamenti ufficiali, utile per scegliere cosa approfondire.
    summary = (
        "🗞️ BOLLETTINO NORMATIVO — 26 OTTOBRE 2026\n\n"
        "Principali temi del periodo 19–26 ottobre:\n"
        "• D.L. 193/2026: autotrasporto, agricoltura e pesca (in vigore; conversione da seguire).\n"
        "• D.L. 190/2026: sicurezza pubblica e criminalità minorile (in vigore; conversione da seguire).\n"
        "• D.Lgs. 186/2026: recupero e confisca dei beni (in vigore dal 31 ottobre).\n"
        "• D.Lgs. 188/2026: prove elettroniche transfrontaliere (dal 4 novembre).\n"
        "• D.Lgs. 191/2026: servizi di sicurezza gestiti (dal 6 novembre).\n"
        "• D.Lgs. 189/2026: azioni a voto plurimo e mercati dei capitali (in vigore).\n"
        "• UE: Regolamento 2026/2269 sugli scambi con l’Armenia (in vigore).\n"
        "• UE: COM(2026) 590 sugli appalti pubblici (proposta, non legge definitiva).\n\n"
        "Fonti ufficiali e impatto pratico nel documento allegato. "
        "L’audio contiene la lettura completa del bollettino. "
        "Sintesi informativa, non consulenza legale."
    )
    send("sendMessage", {"chat_id": CHAT_ID, "text": summary, "disable_web_page_preview": "true"})
    send(
        "sendDocument",
        {"chat_id": CHAT_ID, "caption": "Bollettino completo con stati, impatti pratici e link alle fonti ufficiali."},
        [("document", REPORT, REPORT.name)],
    )
    send(
        "sendDocument",
        {"chat_id": CHAT_ID, "caption": "Testo integrale usato per la lettura audio."},
        [("document", AUDIO_TEXT, AUDIO_TEXT.name)],
    )
    send(
        "sendAudio",
        {
            "chat_id": CHAT_ID,
            "title": "Bollettino normativo — 26 ottobre 2026",
            "performer": "Norma-Aggiorna",
            "caption": "Audio integrale del bollettino normativo italiano ed europeo.",
        },
        [("audio", AUDIO_MP3, "bollettino-normativo-2026-10-26.mp3")],
    )
    print("Invio Telegram completato: riepilogo, bollettino, trascrizione e audio.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"::error::{type(exc).__name__}: {exc}")
        sys.exit(1)
