#!/usr/bin/env python3
"""Bot Telegram "a richiesta" per GitHub Actions.

Due modalità:
  - webhook: un Cloudflare Worker passa il messaggio nella variabile TG_UPDATE
  - polling: senza TG_UPDATE legge i messaggi in coda con getUpdates
Nessuna dipendenza esterna (solo libreria standard).

Comandi:
  /issue titolo | descrizione   crea una issue
  /nota testo                   aggiunge una riga a NOTE.md
  /issues                       issue aperte
  /ultimi                       ultimi 5 commit
  /pubblica                     avvia la pubblicazione su GitHub Pages
  /manutenzione on|off          attiva/disattiva la pagina di manutenzione
  /manutenzione                 mostra lo stato attuale
  /news_on                      attiva la raccolta normativa automatica
  /news_off                     disattiva la raccolta normativa automatica
  /news_status                  mostra lo stato della raccolta normativa
  /news tipo | titolo | fonte | link   aggiunge una notizia a Norma-Aggiorna
  /news                         elenca le notizie; /news elimina N le rimuove
  file o foto                   caricati in uploads/
"""
import base64
import json
import os
import re
import sys
import time
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
NEWS_FILE = "news.json"
NEWS_MAX = 50

HELP = (
    "Comandi:\n"
    "/issue titolo | descrizione\n"
    "/nota testo\n"
    "/issues\n"
    "/ultimi\n"
    "/pubblica\n"
    "/manutenzione on|off\n"
    "/news_on · /news_off · /news_status\n"
    "/news tipo | titolo | fonte | link\n"
    "/news (elenco) · /news elimina N\n"
    "Oppure mandami un file o una foto: li carico in uploads/."
)


def now():
    """Data e ora italiane (il runner di GitHub lavora in UTC)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Rome"))
    except Exception:  # noqa: BLE001
        return datetime.now()


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
    """Crea o aggiorna un file nel repo con un commit (riprova se c'è un conflitto)."""
    url_path = "/contents/" + urllib.parse.quote(path)
    for attempt in range(3):
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
        st, res = gh("PUT", url_path, data=body)
        if st not in (409, 422) or attempt == 2:
            return st, res
        time.sleep(2)


def cmd_issue(arg, msg):
    if not arg:
        return reply("Uso: /issue titolo | descrizione")
    title, _, body = arg.partition("|")
    st, res = gh("POST", "/issues", data={"title": title.strip(), "body": body.strip()})
    reply(f"✅ Issue creata: {res['html_url']}" if st == 201 else err(st, res))


def cmd_nota(arg, msg):
    if not arg:
        return reply("Uso: /nota testo")
    line = f"- {now():%Y-%m-%d %H:%M} — {arg}\n"
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


def news_collection_status():
    """Legge lo stato persistente della raccolta normativa da scripts/news-config.json."""
    st, res = gh("GET", "/contents/scripts/news-config.json", params={"ref": BRANCH})
    if st != 200 or not isinstance(res, dict):
        return None, err(st, res)
    try:
        config = json.loads(base64.b64decode(res["content"]).decode("utf-8"))
    except (ValueError, KeyError):
        return None, "❌ Il file scripts/news-config.json non contiene JSON valido."
    return config.get("ricezione_attiva", True) is True, None


def cmd_news_status(arg, msg):
    active, problem = news_collection_status()
    if problem:
        return reply(problem)
    reply(
        "🟢 Raccolta automatica delle novità normative ATTIVA."
        if active else
        "🔴 Raccolta automatica delle novità normative DISATTIVATA. Le notizie già pubblicate restano disponibili."
    )


def cmd_news_toggle(arg, msg):
    command = (msg.get("text") or "").strip().split(None, 1)[0].split("@")[0].lower()
    mode = "attiva" if command == "/news_on" else "disattiva"
    wanted = mode == "attiva"
    active, problem = news_collection_status()
    if problem:
        return reply(problem)
    if active is wanted:
        return reply(
            "🟢 La raccolta normativa è già attiva."
            if wanted else
            "🔴 La raccolta normativa è già disattivata. Le notizie già pubblicate non sono state cancellate."
        )
    wf, problem = find_workflow("Raccogli novita normative (candidate)")
    if problem:
        return reply(problem)
    st, res = dispatch(wf, {"modalita_ricezione": mode})
    if st == 204:
        reply(
            f"⏳ Richiesta ricevuta: raccolta normativa {mode}. "
            "GitHub aggiornerà l'impostazione e avvierà il workflow."
        )
    else:
        reply(err(st, res))


def read_news():
    """Ritorna (status, dati, sha). Se il file non esiste ancora, parte da un elenco vuoto."""
    st, cur = gh("GET", "/contents/" + NEWS_FILE, params={"ref": BRANCH})
    if st == 404:
        return 404, {"aggiornato": "", "voci": []}, None
    if st != 200 or not isinstance(cur, dict):
        return st, cur, None
    try:
        data = json.loads(base64.b64decode(cur["content"]).decode("utf-8"))
    except (ValueError, KeyError):
        return 422, {"message": "news.json non è un JSON valido: non lo tocco"}, None
    if not isinstance(data, dict) or not isinstance(data.get("voci"), list):
        return 422, {"message": "news.json non ha l'elenco \"voci\": non lo tocco"}, None
    return 200, data, cur["sha"]


def news_order(voci):
    """Indici ordinati dalla notizia più recente (stesso ordine mostrato sul sito)."""
    return sorted(range(len(voci)), key=lambda i: str(voci[i].get("data", "")), reverse=True)


def edit_news(mutate, message):
    """Legge news.json, applica mutate(dati) -> (ok, testo) e salva. Riprova se c'è un conflitto."""
    for attempt in range(3):
        st, data, sha = read_news()
        if st not in (200, 404):
            return False, err(st, data)
        ok, text = mutate(data)
        if not ok:
            return False, text
        body = {
            "message": message,
            "branch": BRANCH,
            "content": base64.b64encode((json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()).decode(),
        }
        if sha:
            body["sha"] = sha
        st, res = gh("PUT", "/contents/" + NEWS_FILE, data=body)
        if st in (200, 201):
            return True, text
        if st not in (409, 422) or attempt == 2:
            return False, err(st, res)
        time.sleep(2)


def publish_after():
    """I commit fatti con il token del workflow non avviano la pubblicazione: la avvio io."""
    wf, problem = find_workflow(PUBLISH_WORKFLOW)
    if not problem:
        st, _ = dispatch(wf)
        if st == 204:
            return reply("🚀 Pubblicazione avviata: tra circa un minuto la modifica è sul sito.")
    reply("⚠️ Modifica salvata, ma non sono riuscito ad avviare la pubblicazione: usa /pubblica.")


def news_list():
    st, data, _ = read_news()
    if st not in (200, 404):
        return reply(err(st, data))
    voci = data["voci"]
    if not voci:
        return reply("Nessuna notizia in news.json. Aggiungine una con:\n/news tipo | titolo | fonte | link")
    lines = []
    for n, i in enumerate(news_order(voci)[:8], 1):
        v = voci[i]
        titolo = v.get("titolo", "")
        data_n = v.get("data", "")
        url = (v.get("url") or "").strip()
        lines.append(f"{n}) [{v.get('tipo', '')}] {titolo} ({data_n})")
        lines.append(f"🔗 {url}" if re.match(r"https?://", url, re.I) else "🔗 Link non disponibile")
    extra = f"\n… e altre {len(voci) - 8}" if len(voci) > 8 else ""
    reply("Notizie sul sito (con link):\n" + "\n".join(lines) + extra)


def news_add(arg):
    parts = [p.strip() for p in arg.split("|")]
    if len(parts) > 4:
        return reply("Troppi campi. Uso: /news tipo | titolo | fonte | link")
    if len(parts) == 1:
        tipo, titolo, fonte, url = "Notizia", parts[0], "", ""
    else:
        tipo, titolo, fonte, url = (parts + ["", ""])[:4]
    if not titolo:
        return reply("Manca il titolo. Uso: /news tipo | titolo | fonte | link")
    if url and not re.match(r"https?://", url, re.I):
        return reply("❌ Il link deve iniziare con http:// o https://")
    today = now().strftime("%Y-%m-%d")
    entry = {"data": today, "tipo": tipo[:30] or "Notizia", "titolo": titolo[:300], "fonte": fonte[:80], "url": url}

    def mutate(data):
        data["voci"].insert(0, entry)
        data["voci"] = [data["voci"][i] for i in news_order(data["voci"])][:NEWS_MAX]
        data["aggiornato"] = today
        return True, f"✅ Notizia aggiunta ({len(data['voci'])} in elenco): {entry['titolo']}"

    ok, text = edit_news(mutate, "Nuova notizia da Telegram")
    reply(text)
    if ok:
        publish_after()


def news_delete(arg):
    try:
        n = int(arg.strip())
    except ValueError:
        return reply("Uso: /news elimina NUMERO (il numero lo vedi con /news)")

    def mutate(data):
        voci = data["voci"]
        order = news_order(voci)
        if not 1 <= n <= len(order):
            return False, f"❌ Numero non valido: in elenco ci sono {len(order)} notizie."
        removed = voci.pop(order[n - 1])
        return True, f"🗑️ Eliminata: {removed.get('titolo', '')}"

    ok, text = edit_news(mutate, "Notizia rimossa da Telegram")
    reply(text)
    if ok:
        publish_after()


def cmd_news(arg, msg):
    words = arg.split(None, 1)
    if not words or words[0].lower() in ("lista", "elenco"):
        return news_list()
    if words[0].lower() in ("elimina", "rimuovi", "del"):
        return news_delete(words[1] if len(words) > 1 else "")
    return news_add(arg)


def upload(msg):
    if "document" in msg:
        d = msg["document"]
        file_id, size = d["file_id"], d.get("file_size", 0)
        name = d.get("file_name") or f"file_{msg['message_id']}"
    else:
        p = msg["photo"][-1]
        file_id, size = p["file_id"], p.get("file_size", 0)
        name = f"foto_{now():%Y%m%d_%H%M%S}.jpg"
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
    "/news": cmd_news, "/news_on": cmd_news_toggle,
    "/news_off": cmd_news_toggle, "/news_status": cmd_news_status,
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


def process(updates):
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


def main():
    raw = os.environ.get("TG_UPDATE", "").strip()
    if raw:  # modalità webhook: il Worker passa il messaggio già pronto
        try:
            update = json.loads(raw)
        except ValueError:
            print("Messaggio non valido.")
            sys.exit(1)
        return process([update])

    # modalità polling: legge i messaggi in coda
    res = tg("getUpdates", timeout=0, allowed_updates=["message"])
    if not res.get("ok"):
        if res.get("error_code") == 409:
            print("Webhook attivo: nessun messaggio da leggere qui.")
            return
        print("Errore Telegram:", res.get("description"))
        sys.exit(1)
    updates = res.get("result", [])
    if not updates:
        print("Nessun messaggio nuovo.")
        return
    # Conferma subito i messaggi: se qualcosa va storto non vengono rieseguiti in loop.
    tg("getUpdates", offset=max(u["update_id"] for u in updates) + 1, timeout=0)
    process(updates)


if __name__ == "__main__":
    main()
