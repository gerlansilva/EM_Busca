#!/usr/bin/env python3
"""
BuscaEM — Coletor OAI-PMH eficiente e retomável

Coleta metadados das revistas cujo SOURCE_AUDIT.json possui OAI-PMH verificado.
Não traduz metadados. Preserva título/resumo/palavras-chave tal como expostos pela fonte.

Características:
- usa ListRecords + resumptionToken;
- retoma de checkpoint se a execução for interrompida;
- grava um arquivo normalizado por revista;
- deduplica por DOI / OAI identifier / chave estável;
- respeita intervalo entre requisições;
- pode coletar todas as revistas OAI ou apenas uma;
- não publica diretamente: depois roda curate.py -> publish.py -> validate.py.

Uso:
  python scripts/harvest_oai.py
  python scripts/harvest_oai.py --journal zetetike
  python scripts/harvest_oai.py --journal all --limit 500
  python scripts/harvest_oai.py --reset zetetike
"""

from __future__ import annotations
import argparse, hashlib, html, json, re, ssl, time
import urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "harvest" / "SOURCE_AUDIT.json"
JOURNALS = ROOT / "config" / "journals.json"
OUTDIR = ROOT / "harvest" / "normalized"
STATEDIR = ROOT / "harvest" / "state"
RAWDIR = ROOT / "harvest" / "raw" / "oai"

USER_AGENT = "BuscaEM/1.0 metadata-harvester (https://github.com/gerlansilva/EM_Busca)"
TIMEOUT = 45
SLEEP = 0.35

DC_NS = "http://purl.org/dc/elements/1.1/"

def now():
    return datetime.now(timezone.utc).isoformat()

def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)

def clean_text(v):
    if v is None:
        return ""
    s = html.unescape(str(v))
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def normalize_doi(v):
    s = clean_text(v)
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.I)
    s = re.sub(r"^doi:\s*", "", s, flags=re.I)
    m = re.search(r"10\.\d{4,9}/\S+", s, flags=re.I)
    return m.group(0).rstrip(".,;)").lower() if m else ""

def year_from_values(values):
    for v in values:
        m = re.search(r"\b(18|19|20|21)\d{2}\b", clean_text(v))
        if m:
            return int(m.group(0))
    return None

def language_code(values):
    if not values:
        return ""
    s = clean_text(values[0]).lower()
    mp = {
        "por":"pt","pt":"pt","pt-br":"pt","portuguese":"pt","português":"pt",
        "eng":"en","en":"en","english":"en",
        "spa":"es","es":"es","spanish":"es","español":"es",
        "deu":"de","ger":"de","de":"de","german":"de","deutsch":"de",
        "fra":"fr","fre":"fr","fr":"fr","french":"fr"
    }
    return mp.get(s, s[:2] if len(s) >= 2 else s)

def stable_id(journal_id, doi, oai_identifier, title, year):
    if doi:
        return "doi:" + doi
    if oai_identifier:
        return "oai:" + oai_identifier
    raw = f"{journal_id}|{title.casefold()}|{year or ''}"
    return "local:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()

def request(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/xml,text/xml,*/*"
    })
    ctx = ssl.create_default_context()
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 4:
                time.sleep(2 ** attempt)
                continue
            raise
        except Exception:
            if attempt < 4:
                time.sleep(2 ** attempt)
                continue
            raise

def q(base, params):
    sep = "&" if "?" in base else "?"
    return base + sep + urllib.parse.urlencode(params)

def lname(tag):
    return tag.split("}")[-1]

def texts_by_local(parent, local):
    out = []
    for el in parent.iter():
        if lname(el.tag) == local and el.text:
            t = clean_text(el.text)
            if t:
                out.append(t)
    return out

def first(iterable, default=""):
    for x in iterable:
        if x:
            return x
    return default

def parse_dc_record(record, journal):
    header = next((x for x in record if lname(x.tag) == "header"), None)
    metadata = next((x for x in record if lname(x.tag) == "metadata"), None)
    if header is None:
        return None

    status = header.attrib.get("status", "")
    identifier = first(texts_by_local(header, "identifier"))
    datestamp = first(texts_by_local(header, "datestamp"))

    if status == "deleted":
        return {
            "id": "oai:" + identifier if identifier else "",
            "journal": journal["id"],
            "oaiIdentifier": identifier,
            "deleted": True,
            "source": "oai-pmh",
            "provenance": {"baseUrl": journal["oaiBase"], "datestamp": datestamp},
            "harvestedAt": now(),
        }

    if metadata is None:
        return None

    titles = texts_by_local(metadata, "title")
    creators = texts_by_local(metadata, "creator")
    subjects = texts_by_local(metadata, "subject")
    descriptions = texts_by_local(metadata, "description")
    dates = texts_by_local(metadata, "date")
    langs = texts_by_local(metadata, "language")
    types = texts_by_local(metadata, "type")
    ids = texts_by_local(metadata, "identifier")
    sources = texts_by_local(metadata, "source")
    relations = texts_by_local(metadata, "relation")
    publishers = texts_by_local(metadata, "publisher")

    doi = ""
    url = ""
    pdf = ""
    for v in ids + relations:
        d = normalize_doi(v)
        if d and not doi:
            doi = d
        if re.match(r"^https?://", v, flags=re.I):
            if v.lower().endswith(".pdf") and not pdf:
                pdf = v
            elif not url:
                url = v

    title = first(titles)
    year = year_from_values(dates)

    # Dublin Core usually exposes abstract in dc:description; keep all if multiple.
    abstract = ""
    if descriptions:
        abstract = max(descriptions, key=len)

    lang = first(langs)
    dtype = first(types).lower()
    if "article" in dtype or not dtype:
        normalized_type = "article"
    else:
        normalized_type = dtype

    rid = stable_id(journal["id"], doi, identifier, title, year)

    return {
        "id": rid,
        "journal": journal["id"],
        "journalName": journal.get("name",""),
        "title": title,
        "titleOriginal": title,
        "titleEn": "",
        "authors": creators,
        "institutions": [],
        "abstract": abstract,
        "abstractOriginal": abstract,
        "abstractEn": "",
        "keywords": subjects,
        "keywordsOriginal": subjects,
        "keywordsEn": [],
        "metadataEnglishStatus": "source_only",
        "year": year,
        "language": lang,
        "languageCode": language_code(langs),
        "type": normalized_type,
        "doi": doi,
        "url": url,
        "pdf": pdf,
        "openAccess": None,
        "oaiIdentifier": identifier,
        "source": "oai-pmh",
        "provenance": {
            "baseUrl": journal["oaiBase"],
            "metadataPrefix": "oai_dc",
            "datestamp": datestamp,
            "sourceValues": {
                "dates": dates,
                "types": types,
                "sources": sources,
                "publishers": publishers
            }
        },
        "harvestedAt": now()
    }

def parse_page(xml_bytes, journal):
    root = ET.fromstring(xml_bytes)

    err = next((x for x in root.iter() if lname(x.tag) == "error"), None)
    if err is not None:
        code = err.attrib.get("code","")
        msg = clean_text(err.text or "")
        if code == "noRecordsMatch":
            return [], ""
        raise RuntimeError(f"OAI error {code}: {msg}")

    records = []
    for el in root.iter():
        if lname(el.tag) == "record":
            row = parse_dc_record(el, journal)
            if row:
                records.append(row)

    token = ""
    token_el = next((x for x in root.iter() if lname(x.tag) == "resumptionToken"), None)
    if token_el is not None and token_el.text:
        token = token_el.text.strip()

    return records, token

def verified_oai_rows():
    audit = load(AUDIT, {})
    config = {j["id"]: j for j in load(JOURNALS, [])}
    out = []
    for row in audit.get("journals", []):
        verified = [x for x in row.get("oai", []) if x.get("verified")]
        if not verified:
            continue
        cfg = config.get(row["id"], {})
        out.append({
            "id": row["id"],
            "name": row.get("name") or cfg.get("name",""),
            "oaiBase": verified[0]["baseUrl"],
            "metadataPrefixes": verified[0].get("metadataPrefixes",[])
        })
    return out

def merge_records(existing, incoming):
    byid = {x.get("id"): x for x in existing if x.get("id")}
    for x in incoming:
        if not x.get("id"):
            continue
        if x.get("deleted"):
            byid.pop(x["id"], None)
        else:
            old = byid.get(x["id"], {})
            # Incoming source-authoritative fields replace prior OAI values.
            byid[x["id"]] = {**old, **x}
    return list(byid.values())

def harvest_one(journal, limit=None, reset=False):
    jid = journal["id"]
    out_file = OUTDIR / f"{jid}.json"
    state_file = STATEDIR / f"{jid}.json"

    if reset:
        if state_file.exists():
            state_file.unlink()

    existing_payload = load(out_file, {"journal": jid, "articles": []})
    articles = existing_payload.get("articles", [])
    state = load(state_file, {})

    token = state.get("resumptionToken", "")
    total_new = 0
    page_no = int(state.get("pages", 0))

    print(f"\n[{jid}] {journal['name']}")
    print(f"  OAI: {journal['oaiBase']}")
    print(f"  existing: {len(articles)}")

    while True:
        if token:
            url = q(journal["oaiBase"], {"verb":"ListRecords","resumptionToken":token})
        else:
            url = q(journal["oaiBase"], {"verb":"ListRecords","metadataPrefix":"oai_dc"})

        xml_bytes = request(url)
        page_no += 1

        # Keep last raw response for diagnosis only, not every page forever.
        raw_path = RAWDIR / jid / "last-page.xml"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(xml_bytes)

        rows, next_token = parse_page(xml_bytes, journal)

        if limit is not None:
            remain = max(0, limit - total_new)
            rows = rows[:remain]

        articles = merge_records(articles, rows)
        total_new += len(rows)

        save(out_file, {
            "journal": jid,
            "journalName": journal["name"],
            "source": "oai-pmh",
            "sourceUrl": journal["oaiBase"],
            "updatedAt": now(),
            "articles": articles
        })

        save(state_file, {
            "journal": jid,
            "resumptionToken": next_token,
            "pages": page_no,
            "recordsSeenThisRun": total_new,
            "updatedAt": now(),
            "complete": not bool(next_token)
        })

        print(f"  page {page_no}: +{len(rows)} | total file={len(articles)}")

        if limit is not None and total_new >= limit:
            print(f"  stopped by limit={limit}; checkpoint saved")
            break
        if not next_token:
            print("  complete")
            break

        token = next_token
        time.sleep(SLEEP)

    return len(articles)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--journal", default="all", help="journal id or all")
    ap.add_argument("--limit", type=int, default=None, help="max records this run per journal")
    ap.add_argument("--reset", default="", help="journal id to reset checkpoint")
    args = ap.parse_args()

    rows = verified_oai_rows()
    if args.journal != "all":
        rows = [x for x in rows if x["id"] == args.journal]
        if not rows:
            raise SystemExit(f"No verified OAI source for journal: {args.journal}")

    if args.reset:
        rows_reset = [x for x in verified_oai_rows() if x["id"] == args.reset]
        if not rows_reset:
            raise SystemExit(f"Unknown/reset journal: {args.reset}")
        harvest_one(rows_reset[0], limit=args.limit, reset=True)
        return

    print(f"Verified OAI journals selected: {len(rows)}")
    total = 0
    for j in rows:
        try:
            total += harvest_one(j, limit=args.limit)
        except Exception as e:
            print(f"  ERROR [{j['id']}]: {type(e).__name__}: {e}")
            # Continue other journals; checkpoint from successful pages is preserved.

    print(f"\nFinished. Records across selected files: {total}")

if __name__ == "__main__":
    main()
