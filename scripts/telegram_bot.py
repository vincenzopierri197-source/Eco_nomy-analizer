#!/usr/bin/env python3
"""Bot Telegram "a richiesta" per GitHub Actions.

Ad ogni esecuzione legge i messaggi arrivati al bot, esegue i comandi e risponde.
Nessuna dipendenza esterna (solo libreria standard).

Comandi:
  /issue titolo | descrizione   crea una issue
  /nota testo                   aggiunge una riga a NOTE.md
  /issues                       issue aperte
  /ultimi                       ultimi 5 commit
  /pubblica                     avvia la pubblicazione su GitHub Pages
  /manutenzione on|off          attiva/disattiva la pagina di manutenzione
  /manutenzione                 mostra lo stato attuale
  file o foto                   caricati in uploads/
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

TG_TOKEN = os.environ["TG_TOKEN"]
OWNER = int(os.environ["TG_CHAT"])
GH_TOKEN = os.environ["GITHUB_TOKEN"]
REPO = os.environ["GITHUB_REPOSITORY"]
BRANCH = os.environ.get("BRANCH", "main")
PUBLISH_WORKFLOW = os.environ.get("PUBLISH_WORKFLOW", "Pubblica eco-nomy su GitHub Pages")
MAINT_WORKFLOW = os.environ.get("MAINT_WORKFLOW", "Gestione manutenzione ECO-NOMY")
MAX_UPLOAD = 20 * 1024 * 1024  # limite dei bot Telegram

HELP = (
    "Comandi:\n"
    "/issue titolo | descrizione\n"
    "/nota testo\n"
    "/issues\n"
    "/ultimi\n"
    "/pubblica\n"
    "/manutenzione on|off\n"
    "Oppure mandami un file o una foto: li carico in uploads/."
)


def http(method, url, headers=None, data=None, raw=False):
    """Ritorna (status, corpo). Il corpo è JSON se possibile, altrimenti bytes."""
    body = None
    hdrs = dict(headers or {})
    if data is not None:
        body = json.dumps(data).encode()
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            status, content = r.status, r.read()
    except urllib.error.HTTPError as e:
        status, content = e.code, e.read()
    if raw:
        return status, content
    try:
        return status, (json.loads(content) if content else {})
    except ValueError:
        return status, content


def tg(method, **params):
    _, res = http("POST", f"https://api.telegram.org/bot{TG_TOKEN}/{method}", data=params)
    return res if isinstance(res, dict) else {"ok": False, "description": "risposta non valida"}


def gh(method, path, data=None, params=None):
    url = f"https://api.github.com/repos/{REPO}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {
        "Authorization": f"Bearer {GH_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    return http(method, url, headers=headers, data=data)


def reply(text):
    tg("sendMessage", chat_id=OWNER, text=text[:4000], disable_web_page_preview=True)


def err(status, body):
    detail = body.get("message") if isinstance(body, dict) else ""
    return f"❌ Errore {status}: {detail}"


def put_file(path, content, message, append=False):
    """Crea o aggiorna un file nel repo con un commit."""
    url_path = "/contents/" + urllib.parse.quote(path)
    st, cur = gh("GET", url_path, params={"ref": BRANCH})
    sha, old = None, b""
    if st == 200 and isinstance(cur, dict):
        sha = cur["sha"]
        if append:
            if cur.get("encoding") != "base64":
                return 400, {"message": "file esistente troppo grande per aggiungere righe"}
            old = base64.b64decode(cur["content"])
    body = {
        "message": message,
        "branch": BRANCH,
        "content": base64.b64encode(old + content if append else content).decode(),
    }
    if sha:
        body["sha"] = sha
    return gh("PUT", url_path, data=body)


def cmd_issue(arg, msg):
    if not arg:
        return reply("Uso: /issue titolo | descrizione")
    title, _, body = arg.partition("|")
    st, res = gh("POST", "/issues", data={"title": title.strip(), "body": body.strip()})
    reply(f"✅ Issue creata: {res['html_url']}" if st == 201 else err(st, res))


def cmd_nota(arg, msg):
    if not arg:
        return reply("Uso: /nota testo")
    line = f"- {datetime.now():%Y-%m-%d %H:%M} — {arg}\n"
    st, res = put_file("NOTE.md", line.encode(), "Nuova nota da Telegram", append=True)
    reply("✅ Nota salvata in NOTE.md" if st in (200, 201) else err(st, res))


def cmd_issues(arg, msg):
    st, res = gh("GET", "/issues", params={"state": "open", "per_page": 10})
    items = [i for i in res if "pull_request" not in i] if st == 200 else []
    if st != 200:
        return reply(err(st, res))
    reply("\n".join(f"#{i['number']} {i['title']}" for i in items) or "Nessuna issue aperta 🎉")


def cmd_ultimi(arg, msg):
    st, res = gh("GET", "/commits", params={"per_page": 5, "sha": BRANCH})
    if st != 200:
        return reply(err(st, res))
    reply("\n".join(f"• {c['commit']['message'].splitlines()[0]}" for c in res))


def find_workflow(name):
    """Ritorna (workflow, errore). Cerca il workflow per nome."""
    st, res = gh("GET", "/actions/workflows")
    if st != 200:
        return None, err(st, res)
    wf = next((w for w in res.get("workflows", []) if w["name"] == name), None)
    if not wf:
        return None, f"❌ Workflow «{name}» non trovato."
    return wf, None


def dispatch(wf, inputs=None):
    data = {"ref": BRANCH}
    if inputs:
        data["inputs"] = inputs
    return gh("POST", f"/actions/workflows/{wf['id']}/dispatches", data=data)


def cmd_pubblica(arg, msg):
    wf, problem = find_workflow(PUBLISH_WORKFLOW)
    if problem:
        return reply(problem)
    st, res = dispatch(wf)
    reply("🚀 Pubblicazione avviata." if st == 204 else err(st, res))


def maintenance_active():
    """True/False in base al file .maintenance, None se non si riesce a leggere."""
    st, _ = gh("GET", "/contents/.maintenance", params={"ref": BRANCH})
    return True if st == 200 else False if st == 404 else None


def cmd_manutenzione(arg, msg):
    arg = arg.strip().lower()
    active = maintenance_active()
    if not arg:
        if active is None:
            return reply("❌ Non riesco a leggere lo stato della manutenzione.")
        return reply("🛠️ Manutenzione ATTIVA" if active else "✅ Manutenzione DISATTIVA (sito normale)")
    wanted = {"on": "ATTIVA", "attiva": "ATTIVA", "off": "DISATTIVA", "disattiva": "DISATTIVA"}.get(arg)
    if not wanted:
        return reply("Uso: /manutenzione on | off\n(senza parametro mostra lo stato)")
    if active is not None and active == (wanted == "ATTIVA"):
        stato = "attiva" if active else "disattiva"
        return reply(f"Manutenzione già {stato}, nessuna modifica. Per ripubblicare il sito usa /pubblica.")
    wf, problem = find_workflow(MAINT_WORKFLOW)
    if problem:
        return reply(problem)
    st, res = dispatch(wf, {"modalita": wanted})
    if st == 204:
        reply(f"🛠️ Richiesta inviata: manutenzione {wanted}. Ti avviso quando la pubblicazione è finita.")
    else:
        reply(err(st, res))


def upload(msg):
    if "document" in msg:
        d = msg["document"]
        file_id, size = d["file_id"], d.get("file_size", 0)
        name = d.get("file_name") or f"file_{msg['message_id']}"
    else:
        p = msg["photo"][-1]
        file_id, size = p["file_id"], p.get("file_size", 0)
        name = f"foto_{datetime.now():%Y%m%d_%H%M%S}.jpg"
    if size > MAX_UPLOAD:
        return reply("❌ File troppo grande (massimo 20 MB).")
    name = name.replace("/", "_").replace("\\", "_").lstrip(".") or "file"
    info = tg("getFile", file_id=file_id)
    if not info.get("ok"):
        return reply(f"❌ Telegram: {info.get('description')}")
    st, data = http("GET", f"https://api.telegram.org/file/bot{TG_TOKEN}/{info['result']['file_path']}", raw=True)
    if st != 200:
        return reply(f"❌ Download da Telegram fallito ({st}).")
    st, res = put_file(f"uploads/{name}", data, f"Upload da Telegram: {name}")
    reply(f"✅ Caricato: {res['content']['html_url']}" if st in (200, 201) else err(st, res))


COMMANDS = {
    "/issue": cmd_issue, "/nota": cmd_nota, "/issues": cmd_issues,
    "/ultimi": cmd_ultimi, "/pubblica": cmd_pubblica, "/manutenzione": cmd_manutenzione,
}


def handle(msg):
    if "document" in msg or "photo" in msg:
        return upload(msg)
    parts = (msg.get("text") or "").strip().split(None, 1)
    if not parts or not parts[0].startswith("/"):
        return reply(HELP)
    cmd = parts[0].split("@")[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    fn = COMMANDS.get(cmd)
    return fn(arg, msg) if fn else reply(HELP)


def main():
    res = tg("getUpdates", timeout=0, allowed_updates=["message"])
    if not res.get("ok"):
        print("Errore Telegram:", res.get("description"))
        sys.exit(1)
    updates = res.get("result", [])
    if not updates:
        print("Nessun messaggio nuovo.")
        return
    # Conferma subito i messaggi: se qualcosa va storto non vengono rieseguiti in loop.
    tg("getUpdates", offset=max(u["update_id"] for u in updates) + 1, timeout=0)
    done = 0
    for u in updates:
        msg = u.get("message")
        if not msg or msg.get("chat", {}).get("id") != OWNER:
            continue  # ignora chiunque non sia tu
        try:
            handle(msg)
        except Exception as e:  # noqa: BLE001
            reply(f"❌ Errore imprevisto: {type(e).__name__}")
            print("Errore:", type(e).__name__)
        done += 1
    print(f"Messaggi elaborati: {done}")


if __name__ == "__main__":
    main()
