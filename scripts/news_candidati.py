#!/usr/bin/env python3
"""Raccoglie novita' normative da feed RSS/Atom ufficiali e PROPONE le voci candidate.
Non modifica news.json e non usa l'AI: filtra solo per parole chiave sui titoli."""
import html, json, os, re, sys, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "Mozilla/5.0 (compatible; eco-nomy-news/1.0)"
TIMEOUT = 25


def fetch(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, text/html;q=0.8"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read(), (r.headers.get_content_charset() or "utf-8")


class _FeedLinks(HTMLParser):
    def __init__(self, base):
        super().__init__()
        self.base, self.links = base, []

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        a = dict(attrs)
        if (a.get("rel") or "").lower() == "alternate" and (a.get("type") or "").lower() in (
                "application/rss+xml", "application/atom+xml") and a.get("href"):
            self.links.append(urljoin(self.base, a["href"]))


def discover_feeds(site):
    body, cs = fetch(site)
    p = _FeedLinks(site)
    p.feed(body.decode(cs, errors="replace"))
    return list(dict.fromkeys(p.links))[:3]


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        d = parsedate_to_datetime(s)
    except Exception:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    if d is None:
        return None
    return d.astimezone(timezone.utc) if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _t(el):
    return el.tag.split("}")[-1].lower()


def parse_feed(raw):
    root = ET.fromstring(raw)
    out = []
    for it in root.iter():
        if _t(it) not in ("item", "entry"):
            continue
        f = {"titolo": "", "link": "", "data": None, "testo": ""}
        for c in it:
            n = _t(c)
            if n == "title":
                f["titolo"] = (c.text or "").strip()
            elif n == "link":
                f["link"] = (c.get("href") or c.text or "").strip() or f["link"]
            elif n in ("pubdate", "published", "updated", "date") and not f["data"]:
                f["data"] = parse_date(c.text)
            elif n in ("description", "summary") and not f["testo"]:
                f["testo"] = re.sub(r"<[^>]+>", " ", html.unescape(c.text or ""))
        if f["titolo"]:
            out.append(f)
    return out


def safe_url(u):
    return u if re.match(r"^https?://", u or "") else ""


def collect(cfg, now=None):
    now = now or datetime.now(timezone.utc)
    limite = now - timedelta(hours=cfg.get("finestra_ore", 30))
    kw = re.compile(r"\b(?:" + "|".join(cfg["parole_chiave"]) + ")", re.I)
    cand, problemi, note = [], [], []
    for fonte in cfg["fonti"]:
        nome, urls = fonte["nome"], []
        try:
            if fonte.get("url"):
                urls = [fonte["url"]]
            else:
                urls = discover_feeds(fonte["sito"])
                if urls:
                    note.append(f"{nome}: feed trovato automaticamente -> {', '.join(urls)} (scrivilo in news-config.json come \"url\")")
                else:
                    problemi.append(f"{nome}: nessun feed trovato sul sito; cerca l'icona RSS e inserisci l'indirizzo in news-config.json")
                    continue
        except Exception as e:
            problemi.append(f"{nome}: sito non raggiungibile ({type(e).__name__})")
            continue
        ok = False
        for u in urls:
            try:
                raw, _ = fetch(u)
                items = parse_feed(raw)
                ok = True
            except Exception as e:
                problemi.append(f"{nome}: feed non leggibile ({type(e).__name__}) {u}")
                continue
            for it in items:
                if not it["data"] or it["data"] < limite:
                    continue
                if kw.search(it["titolo"] + " " + it["testo"]):
                    cand.append({"fonte": nome, "tipo": fonte.get("tipo", "Notizia"),
                                 "titolo": it["titolo"][:200], "url": safe_url(it["link"]), "data": it["data"]})
        if not ok and urls:
            pass
    visti, unici = set(), []
    for c in sorted(cand, key=lambda x: x["data"], reverse=True):
        k = c["url"] or c["titolo"]
        if k not in visti:
            visti.add(k)
            unici.append(c)
    return unici[: cfg.get("max_voci", 40)], problemi, note


def json_pronto(cands):
    return json.dumps([{"data": c["data"].strftime("%Y-%m-%d"), "tipo": c["tipo"], "titolo": c["titolo"],
                        "fonte": c["fonte"], "url": c["url"]} for c in cands], ensure_ascii=False, indent=2)


def messaggio_telegram(cands, problemi, limite=3800):
    righe = [f"<b>Norma-Aggiorna</b>: {len(cands)} novita' candidate (da verificare)"]
    for c in cands[:10]:
        t = html.escape(c["titolo"][:140])
        link = f'<a href="{html.escape(c["url"], quote=True)}">{t}</a>' if c["url"] else t
        righe.append(f"- {link} <i>({html.escape(c['fonte'])}, {c['data'].strftime('%d/%m')})</i>")
    if len(cands) > 10:
        righe.append(f"... e altre {len(cands) - 10}: vedi il riepilogo dell'esecuzione su GitHub (Actions).")
    if problemi:
        righe.append("<b>Fonti con problemi</b>:")
        righe += [f"- {html.escape(p)}" for p in problemi]
    return "\n".join(righe)[:limite]


def invia_telegram(testo):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        print("Telegram non configurato: salto la notifica.")
        return
    req = urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage",
                                 data=json.dumps({"chat_id": chat, "text": testo, "parse_mode": "HTML",
                                                  "disable_web_page_preview": True}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=TIMEOUT).read()
        print("Notifica Telegram inviata.")
    except Exception as e:
        print(f"::warning::Notifica Telegram non riuscita ({type(e).__name__})")


def main():
    cfg = json.load(open(os.path.join(HERE, "news-config.json"), encoding="utf-8"))
    cands, problemi, note = collect(cfg)
    md = [f"## Norma-Aggiorna: {len(cands)} novita' candidate\n"]
    for c in cands:
        md.append(f"- **{c['data'].strftime('%d/%m/%Y')}** [{c['fonte']}] {c['titolo']} - {c['url'] or 'senza link'}")
    if cands:
        md += ["\nVoci pronte da incollare in `news.json` (dopo verifica sulla fonte ufficiale):\n", "```json", json_pronto(cands), "```"]
    if note:
        md += ["\n### Note\n"] + [f"- {n}" for n in note]
    if problemi:
        md += ["\n### Fonti con problemi\n"] + [f"- {p}" for p in problemi]
    testo = "\n".join(md)
    print(testo)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(testo + "\n")
    for p in problemi:
        print(f"::warning::{p}")
    if cands or problemi:
        invia_telegram(messaggio_telegram(cands, problemi))
    if len(problemi) >= len(cfg["fonti"]):
        sys.exit("Nessuna fonte ha funzionato.")


if __name__ == "__main__":
    main()
