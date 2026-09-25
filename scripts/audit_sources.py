#!/usr/bin/env python3
"""
BuscaEM — auditor de fontes

Não coleta artigos.

Verifica, para cada periódico cadastrado em config/journals.json:
- disponibilidade da página oficial;
- endpoints OAI-PMH candidatos;
- formatos OAI-PMH disponíveis;
- páginas SciELO candidatas;
- ISSN no Crossref;
- fonte preferencial sugerida para a futura coleta.

Saídas:
- harvest/SOURCE_AUDIT.json
- dist/data/source-audit.json

Importante:
"verified" significa apenas que a fonte respondeu de forma válida nesta auditoria.
Não significa que o backfile histórico da revista esteja completo.
"""

from __future__ import annotations

import concurrent.futures
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
JOURNALS_FILE = ROOT / "config" / "journals.json"
AUDIT_FILE = ROOT / "harvest" / "SOURCE_AUDIT.json"
PUBLIC_AUDIT_FILE = ROOT / "dist" / "data" / "source-audit.json"

USER_AGENT = (
    "BuscaEM/1.0 source-audit "
    "(academic metadata project; https://github.com/gerlansilva/EM_Busca)"
)

TIMEOUT = 20
MAX_WORKERS = 6


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def request(url: str, *, accept: str = "*/*", timeout: int = TIMEOUT):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": accept,
        },
    )
    context = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=context) as response:
        return {
            "status": getattr(response, "status", 200),
            "url": response.geturl(),
            "content_type": response.headers.get("Content-Type", ""),
            "body": response.read(),
        }


def safe_request(url: str, *, accept: str = "*/*"):
    try:
        r = request(url, accept=accept)
        return {
            "ok": 200 <= r["status"] < 400,
            "status": r["status"],
            "finalUrl": r["url"],
            "contentType": r["content_type"],
            "body": r["body"],
            "error": None,
        }
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "status": exc.code,
            "finalUrl": getattr(exc, "url", url),
            "contentType": "",
            "body": b"",
            "error": f"HTTP {exc.code}",
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": None,
            "finalUrl": url,
            "contentType": "",
            "body": b"",
            "error": f"{type(exc).__name__}: {exc}",
        }


def candidate_oai_urls(journal: dict):
    candidates = []

    # 1) explicit candidates from registry
    for source in journal.get("sourceCandidates", []):
        if source.get("type") == "oai-pmh" and source.get("url"):
            candidates.append(source["url"])

    # 2) derive common OJS patterns from journal URL
    url = journal.get("url") or ""
    if url:
        p = urllib.parse.urlsplit(url)
        if p.netloc:
            base = f"{p.scheme or 'https'}://{p.netloc}"
            path = p.path.rstrip("/")

            if path:
                candidates.append(base + path + "/oai")

            m = re.search(r"(/index\.php/[^/]+)", path)
            if m:
                candidates.append(base + m.group(1) + "/oai")

            # common repository-wide OAI endpoints
            candidates.extend([
                base + "/oai",
                base + "/index.php/index/oai",
            ])

    seen = set()
    out = []
    for candidate in candidates:
        candidate = candidate.rstrip("/")
        if candidate and candidate not in seen:
            seen.add(candidate)
            out.append(candidate)
    return out


def oai_url(base: str, verb: str):
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{urllib.parse.urlencode({'verb': verb})}"


def local_name(tag: str):
    return tag.split("}")[-1]


def first_text(root: ET.Element, name: str):
    for el in root.iter():
        if local_name(el.tag) == name and el.text:
            return el.text.strip()
    return ""


def audit_oai(base: str):
    identify = safe_request(
        oai_url(base, "Identify"),
        accept="application/xml,text/xml,*/*",
    )

    result = {
        "baseUrl": base,
        "verified": False,
        "identifyStatus": identify["status"],
        "repositoryName": "",
        "protocolVersion": "",
        "granularity": "",
        "deletedRecord": "",
        "metadataPrefixes": [],
        "error": identify["error"],
    }

    if not identify["ok"] or not identify["body"]:
        return result

    try:
        root = ET.fromstring(identify["body"])
    except Exception as exc:
        result["error"] = f"Invalid XML: {exc}"
        return result

    if local_name(root.tag) != "OAI-PMH":
        result["error"] = "Response is not an OAI-PMH document"
        return result

    error_node = next(
        (el for el in root.iter() if local_name(el.tag) == "error"),
        None,
    )
    if error_node is not None:
        result["error"] = (
            error_node.attrib.get("code", "oai-error")
            + ": "
            + (error_node.text or "").strip()
        )
        return result

    result["verified"] = True
    result["repositoryName"] = first_text(root, "repositoryName")
    result["protocolVersion"] = first_text(root, "protocolVersion")
    result["granularity"] = first_text(root, "granularity")
    result["deletedRecord"] = first_text(root, "deletedRecord")
    result["baseUrl"] = first_text(root, "baseURL") or base

    formats = safe_request(
        oai_url(result["baseUrl"], "ListMetadataFormats"),
        accept="application/xml,text/xml,*/*",
    )

    if formats["ok"] and formats["body"]:
        try:
            froot = ET.fromstring(formats["body"])
            prefixes = []
            for el in froot.iter():
                if local_name(el.tag) == "metadataPrefix" and el.text:
                    value = el.text.strip()
                    if value and value not in prefixes:
                        prefixes.append(value)
            result["metadataPrefixes"] = prefixes
        except Exception:
            pass

    return result


def audit_official_page(url: str):
    if not url:
        return {
            "verified": False,
            "status": None,
            "finalUrl": "",
            "contentType": "",
            "error": "No official URL registered",
        }

    r = safe_request(url, accept="text/html,application/xhtml+xml,*/*")
    return {
        "verified": r["ok"],
        "status": r["status"],
        "finalUrl": r["finalUrl"],
        "contentType": r["contentType"],
        "error": r["error"],
    }


def audit_scielo(journal: dict):
    candidates = []
    for source in journal.get("sourceCandidates", []):
        if source.get("type") == "scielo" and source.get("url"):
            candidates.append(source["url"])

    results = []
    for url in candidates:
        r = safe_request(url, accept="text/html,application/xhtml+xml,*/*")
        results.append({
            "url": url,
            "verified": r["ok"],
            "status": r["status"],
            "finalUrl": r["finalUrl"],
            "contentType": r["contentType"],
            "error": r["error"],
        })
    return results


def crossref_candidate_issns(journal: dict):
    values = []

    for value in journal.get("issns", []):
        if value and value not in values:
            values.append(value)

    for source in journal.get("sourceCandidates", []):
        if source.get("type") == "crossref":
            if source.get("issn") and source["issn"] not in values:
                values.append(source["issn"])
            for value in source.get("issns", []):
                if value and value not in values:
                    values.append(value)

    return values


def audit_crossref(journal: dict):
    results = []

    for issn in crossref_candidate_issns(journal):
        url = "https://api.crossref.org/journals/" + urllib.parse.quote(issn)
        r = safe_request(url, accept="application/json")

        row = {
            "issn": issn,
            "verified": False,
            "status": r["status"],
            "title": "",
            "publisher": "",
            "issns": [],
            "error": r["error"],
        }

        if r["ok"] and r["body"]:
            try:
                data = json.loads(r["body"].decode("utf-8"))
                message = data.get("message") or {}
                row["verified"] = True
                row["title"] = message.get("title") or ""
                row["publisher"] = message.get("publisher") or ""
                row["issns"] = message.get("ISSN") or []
            except Exception as exc:
                row["error"] = f"Invalid Crossref JSON: {exc}"

        results.append(row)

        # tiny pause so sequential ISSN aliases do not hammer the API
        time.sleep(0.08)

    return results


def preferred_source(row: dict):
    # For source authority in BuscaEM:
    # SciELO > OAI-PMH > Crossref > official HTML/manual collector.
    if any(x.get("verified") for x in row["scielo"]):
        return "scielo"

    if any(x.get("verified") for x in row["oai"]):
        return "oai-pmh"

    if any(x.get("verified") for x in row["crossref"]):
        return "crossref"

    if row["officialPage"].get("verified"):
        return "official-page"

    return None


def verification_status(row: dict):
    preferred = row["preferredSource"]

    if preferred in {"scielo", "oai-pmh", "crossref"}:
        return "verified"

    if preferred == "official-page":
        return "manual_review"

    return "unresolved"


def audit_journal(journal: dict):
    jid = journal["id"]

    official = audit_official_page(journal.get("url") or "")

    oai_results = []
    for base in candidate_oai_urls(journal):
        result = audit_oai(base)
        oai_results.append(result)
        if result["verified"]:
            # one verified OAI endpoint is enough; don't probe every guessed path
            break
        time.sleep(0.08)

    scielo_results = audit_scielo(journal)
    crossref_results = audit_crossref(journal)

    row = {
        "id": jid,
        "name": journal.get("name", ""),
        "country": journal.get("country", ""),
        "officialPage": official,
        "oai": oai_results,
        "scielo": scielo_results,
        "crossref": crossref_results,
        "preferredSource": None,
        "sourceVerification": None,
        "notes": [],
    }

    row["preferredSource"] = preferred_source(row)
    row["sourceVerification"] = verification_status(row)

    if not row["preferredSource"]:
        row["notes"].append("No structured source verified automatically.")

    if row["preferredSource"] == "official-page":
        row["notes"].append(
            "Official site is online, but a structured metadata source still needs manual review."
        )

    if any(x.get("verified") for x in oai_results):
        verified_oai = next(x for x in oai_results if x.get("verified"))
        prefixes = verified_oai.get("metadataPrefixes") or []
        if "oai_dc" not in prefixes and prefixes:
            row["notes"].append(
                "OAI-PMH verified; oai_dc was not listed among available metadata formats."
            )

    return row


def summary(rows: list[dict]):
    counts = {
        "journals": len(rows),
        "verified": 0,
        "manual_review": 0,
        "unresolved": 0,
        "preferred_scielo": 0,
        "preferred_oai_pmh": 0,
        "preferred_crossref": 0,
        "preferred_official_page": 0,
    }

    for row in rows:
        status = row.get("sourceVerification")
        if status in counts:
            counts[status] += 1

        source = row.get("preferredSource")
        key = {
            "scielo": "preferred_scielo",
            "oai-pmh": "preferred_oai_pmh",
            "crossref": "preferred_crossref",
            "official-page": "preferred_official_page",
        }.get(source)

        if key:
            counts[key] += 1

    return counts


def main():
    journals = load_json(JOURNALS_FILE, [])

    if not isinstance(journals, list):
        raise SystemExit("config/journals.json must contain a JSON array.")

    enabled = [j for j in journals if j.get("enabled", True)]

    print(f"Auditing {len(enabled)} registered journals...")
    rows = []

    with concurrent.futures.ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:
        futures = {
            executor.submit(audit_journal, journal): journal["id"]
            for journal in enabled
        }

        for future in concurrent.futures.as_completed(futures):
            jid = futures[future]
            try:
                row = future.result()
            except Exception as exc:
                row = {
                    "id": jid,
                    "name": next(
                        (j.get("name", "") for j in enabled if j["id"] == jid),
                        "",
                    ),
                    "country": "",
                    "officialPage": {},
                    "oai": [],
                    "scielo": [],
                    "crossref": [],
                    "preferredSource": None,
                    "sourceVerification": "unresolved",
                    "notes": [f"Audit exception: {type(exc).__name__}: {exc}"],
                }

            rows.append(row)
            print(
                f"{jid}: "
                f"{row.get('sourceVerification')} / "
                f"{row.get('preferredSource') or 'no-source'}"
            )

    order = {j["id"]: i for i, j in enumerate(enabled)}
    rows.sort(key=lambda row: order.get(row["id"], 999999))

    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "definition": (
            "Source verification confirms technical availability only. "
            "It does not certify complete historical coverage."
        ),
        "summary": summary(rows),
        "journals": rows,
    }

    save_json(AUDIT_FILE, payload)
    save_json(PUBLIC_AUDIT_FILE, payload)

    print()
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print(f"\nSaved: {AUDIT_FILE.relative_to(ROOT)}")
    print(f"Saved: {PUBLIC_AUDIT_FILE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
