
#!/usr/bin/env python3
"""Raccoglie novita' normative da feed RSS/Atom/RDF ufficiali.
Propone le voci candidate senza modificare news.json e senza usare AI."""

import html
import json
import os
import re
import sys
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin


HERE = os.path.dirname(os.path.abspath(__file__))
UA = "Mozilla/5.0 (compatible; eco-nomy-news/1.2)"
TIMEOUT = 25


def fetch(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": (
                "application/rss+xml, application/atom+xml, "
                "application/rdf+xml, application/xml, "
                "text/xml, text/html;q=0.8"
            ),
        },
    )

    with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
        raw = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
        content_type = response.headers.get("Content-Type", "")
        final_url = response.geturl()

    return raw, charset, content_type, final_url


class _FeedLinks(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "link":
            return

        attrs = dict(attrs)
        rel = (attrs.get("rel") or "").lower().split()
        feed_type = (
            (attrs.get("type") or "")
            .lower()
            .split(";")[0]
            .strip()
        )
        href = attrs.get("href")

        if (
            "alternate" in rel
            and feed_type in (
                "application/rss+xml",
                "application/atom+xml",
                "application/rdf+xml",
                "application/xml",
                "text/xml",
            )
            and href
        ):
            self.links.append(urljoin(self.base, href))


def discover_feeds(site):
    raw, charset, _, final_url = fetch(site)

    if looks_like_xml_feed(raw):
        return [final_url]

    text = raw.decode(charset, errors="replace")
    parser = _FeedLinks(final_url)
    parser.feed(text)

    return list(dict.fromkeys(parser.links))[:5]


def looks_like_xml_feed(raw):
    try:
        root = ET.fromstring(normalize_xml_encoding(raw))
    except (ET.ParseError, ValueError):
        return False

    root_name = tag_name(root)
    return root_name in ("rss", "feed", "rdf")


def normalize_xml_encoding(raw):
    """Restituisce bytes e corregge dichiarazioni UTF-16 incoerenti."""
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    elif isinstance(raw, bytearray):
        raw = bytes(raw)
    elif not isinstance(raw, bytes):
        raise TypeError(
            f"Contenuto XML non supportato: {type(raw).__name__}"
        )

    if not raw:
        return raw

    # Conserva documenti con BOM o struttura UTF-16/UTF-32 reale.
    if raw.startswith((
        b"\xff\xfe", b"\xfe\xff",
        b"\x00\x00\xfe\xff", b"\xff\xfe\x00\x00",
    )):
        return raw

    if (
        raw.startswith(b"\x00<")
        or raw.startswith(b"<\x00")
        or raw.startswith(b"\x00\x00<\x00")
        or raw.startswith(b"<\x00\x00\x00")
    ):
        return raw

    declaration = re.match(
        rb"""(\s*<\?xml\b[^?]*?\bencoding\s*=\s*)(['"])([^'"]+)(['"])""",
        raw[:300],
        flags=re.IGNORECASE,
    )
    if not declaration:
        return raw

    declared = declaration.group(3).lower().replace(b"_", b"-")
    if declared in (b"utf-16", b"utf-16le", b"utf-16be"):
        raw = re.sub(
            rb"""(\bencoding\s*=\s*)(['"])[^'"]+(['"])""",
            rb"\1\2UTF-8\3",
            raw,
            count=1,
            flags=re.IGNORECASE,
        )

    return raw

def parse_date(value):
    if not value:
        return None

    value = value.strip()

    try:
        date = parsedate_to_datetime(value)
    except Exception:
        try:
            date = datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )
        except Exception:
            return None

    if date is None:
        return None

    if date.tzinfo is None:
        return date.replace(tzinfo=timezone.utc)

    return date.astimezone(timezone.utc)


def tag_name(element):
    return element.tag.split("}")[-1].lower()


def parse_feed(raw, base_url=""):
    raw = normalize_xml_encoding(raw)

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        preview = raw[:240].decode("utf-8", errors="replace")
        raise ValueError(
            f"XML non valido ({exc}); risposta iniziale: {preview!r}"
        ) from exc

    entries = []

    for element in root.iter():
        if tag_name(element) not in ("item", "entry"):
            continue

        item = {
            "titolo": "",
            "link": "",
            "data": None,
            "testo": "",
        }

        # In RDF il collegamento puo' trovarsi nell'attributo rdf:about.
        for key, value in element.attrib.items():
            if key.split("}")[-1].lower() == "about" and value:
                item["link"] = urljoin(base_url, value)
                break

        for child in element:
            name = tag_name(child)
            value = "".join(child.itertext()).strip()

            if name == "title" and not item["titolo"]:
                item["titolo"] = value

            elif name == "link":
                href = child.get("href")
                rel = (child.get("rel") or "alternate").lower()
                link_value = (href or value).strip()

                if link_value and (
                    rel == "alternate" or not item["link"]
                ):
                    item["link"] = urljoin(base_url, link_value)

            elif name in (
                "pubdate",
                "published",
                "updated",
                "date",
                "created",
                "issued",
            ) and not item["data"]:
                item["data"] = parse_date(value)

            elif name in (
                "description",
                "summary",
                "encoded",
                "content",
            ) and not item["testo"]:
                item["testo"] = re.sub(
                    r"\s+",
                    " ",
                    re.sub(
                        r"<[^>]+>",
                        " ",
                        html.unescape(value),
                    ),
                ).strip()

        if item["titolo"]:
            entries.append(item)

    return entries


def safe_url(url):
    if not url:
        return ""

    url = url.strip()

    if url.startswith(("http://", "https://")):
        return url

    return ""


def collect(config, now=None):
    now = now or datetime.now(timezone.utc)

    limite = now - timedelta(
        hours=config.get("finestra_ore", 30)
    )

    keywords = config.get("parole_chiave", [])
    regex = re.compile(
        r"\b(?:" + "|".join(keywords) + ")",
        re.IGNORECASE,
    )

    candidates = []
    problems = []
    notes = []

    for source in config.get("fonti", []):
        name = source.get("nome", "Fonte senza nome")
        urls = []

        try:
            if source.get("url"):
                urls = [source["url"]]

            elif source.get("sito"):
                urls = discover_feeds(source["sito"])

                if urls:
                    notes.append(
                        f"{name}: feed individuato -> "
                        f"{', '.join(urls)}. "
                        "Inseriscilo come 'url' nel news-config.json."
                    )
                else:
                    problems.append(
                        f"{name}: nessun feed RSS/Atom trovato "
                        "nei link dichiarati della pagina."
                    )
                    continue

            else:
                problems.append(
                    f"{name}: manca sia 'url' sia 'sito' nel config."
                )
                continue

        except urllib.error.HTTPError as exc:
            problems.append(
                f"{name}: errore HTTP {exc.code} durante "
                "il recupero della pagina."
            )
            continue

        except Exception as exc:
            problems.append(
                f"{name}: impossibile individuare il feed "
                f"({type(exc).__name__}: {exc})."
            )
            continue

        for url in urls:
            try:
                raw, charset, content_type, final_url = fetch(url)
                items = parse_feed(raw, final_url)

            except urllib.error.HTTPError as exc:
                problems.append(
                    f"{name}: errore HTTP {exc.code} sul feed {url}"
                )
                continue

            except Exception as exc:
                import traceback
                traceback_text = traceback.format_exc()
                print(traceback_text, file=sys.stderr)
                problems.append(
                    f"{name}: feed non leggibile "
                    f"({type(exc).__name__}: {exc}) [{url}]"
                    f"\\nDettaglio tecnico: {traceback_text.strip()}"
                )
                continue

            if not items:
                problems.append(
                    f"{name}: XML valido ma nessuna voce trovata "
                    f"[{url}; Content-Type: "
                    f"{content_type or 'sconosciuto'}]"
                )
                continue

            for item in items:
                date = item["data"]

                if not date or date < limite:
                    continue

                searchable = item["titolo"] + " " + item["testo"]

                if regex.search(searchable):
                    candidates.append({
                        "fonte": name,
                        "tipo": source.get("tipo", "Notizia"),
                        "titolo": item["titolo"][:200],
                        "url": safe_url(
                            urljoin(final_url, item["link"])
                        ),
                        "data": date,
                    })

    seen = set()
    unique = []

    for candidate in sorted(
        candidates,
        key=lambda entry: entry["data"],
        reverse=True,
    ):
        key = candidate["url"] or candidate["titolo"]

        if key not in seen:
            seen.add(key)
            unique.append(candidate)

    return (
        unique[:config.get("max_voci", 40)],
        problems,
        notes,
    )


def json_pronto(candidates):
    return json.dumps(
        [
            {
                "data": candidate["data"].strftime("%Y-%m-%d"),
                "tipo": candidate["tipo"],
                "titolo": candidate["titolo"],
                "fonte": candidate["fonte"],
                "url": candidate["url"],
            }
            for candidate in candidates
        ],
        ensure_ascii=False,
        indent=2,
    )


def messaggio_telegram(candidates, problems, limite=3800):
    lines = [
        f"<b>Norma-Aggiorna</b>: "
        f"{len(candidates)} novita' candidate (da verificare)"
    ]

    for candidate in candidates[:10]:
        title = html.escape(candidate["titolo"][:140])
        url = candidate["url"]

        if url:
            title = (
                f'<a href="{html.escape(url, quote=True)}">'
                f"{title}</a>"
            )

        lines.append(
            f"- {title} "
            f"<i>({html.escape(candidate['fonte'])}, "
            f"{candidate['data'].strftime('%d/%m')})</i>"
        )

    if len(candidates) > 10:
        lines.append(
            f"... e altre {len(candidates) - 10}: "
            "vedi il riepilogo dell'esecuzione su GitHub Actions."
        )

    if problems:
        lines.append("<b>Fonti con problemi</b>:")
        lines.extend(
            f"- {html.escape(problem)}"
            for problem in problems
        )

    # Evita di tagliare nel mezzo un tag HTML quando possibile.
    message = "\n".join(lines)
    if len(message) > limite:
        message = message[:limite - 20] + "\n… messaggio ridotto"

    return message


def invia_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("Telegram non configurato: salto la notifica.")
        return

    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(
            request, timeout=TIMEOUT
        ) as response:
            response.read()

        print("Notifica Telegram inviata.")

    except Exception as exc:
        print(
            "::warning::Notifica Telegram non riuscita "
            f"({type(exc).__name__}: {exc})"
        )


def main():
    config_path = os.path.join(HERE, "news-config.json")

    with open(config_path, encoding="utf-8") as file:
        config = json.load(file)

    candidates, problems, notes = collect(config)

    lines = [
        f"## Norma-Aggiorna: {len(candidates)} novita' candidate",
        "",
    ]

    for candidate in candidates:
        lines.append(
            f"- **{candidate['data'].strftime('%d/%m/%Y')}** "
            f"[{candidate['fonte']}] {candidate['titolo']} - "
            f"{candidate['url'] or 'senza link'}"
        )

    if candidates:
        lines.extend([
            "",
            "Voci candidate per news.json "
            "(da verificare sulla fonte ufficiale):",
            "",
            "```json",
            json_pronto(candidates),
            "```",
        ])

    if notes:
        lines.extend(["", "### Note", ""])
        lines.extend(f"- {note}" for note in notes)

    if problems:
        lines.extend(["", "### Fonti con problemi", ""])
        lines.extend(f"- {problem}" for problem in problems)

    report = "\n".join(lines)
    print(report)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as file:
            file.write(report + "\n")

    for problem in problems:
        print(f"::warning::{problem}")

    if candidates or problems:
        invia_telegram(
            messaggio_telegram(candidates, problems)
        )

    if len(problems) >= len(config.get("fonti", [])):
        sys.exit("Nessuna fonte ha funzionato.")


if __name__ == "__main__":
    main()
