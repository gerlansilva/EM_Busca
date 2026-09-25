#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def load(p,d):
    try:return json.loads(p.read_text(encoding="utf-8"))
    except Exception:return d

def save(p,v):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

def classify(a,rules):
    title=str(a.get("titleOriginal") or a.get("title") or "").strip()
    year=a.get("year")
    authors=a.get("authors") or []
    abstract=str(a.get("abstractOriginal") or a.get("abstract") or "").strip()
    keywords=a.get("keywordsOriginal") or a.get("keywords") or []
    dtype=str(a.get("type") or "").strip().lower()

    if not title:
        return "needs_metadata","missing_title"
    if not year:
        return "needs_metadata","missing_year"

    low=title.casefold()
    for pat in rules.get("exclude_title_patterns",[]):
        if pat.casefold() in low:
            return "excluded_document_type",pat

    if not authors:
        return "needs_metadata","missing_authors"

    if not abstract and not keywords:
        return "needs_metadata","missing_abstract_and_keywords"

    allowed=rules.get("include",{}).get("document_types",[])
    if dtype and dtype not in allowed and "article" not in dtype:
        return "needs_metadata","unknown_document_type"

    return "included","meets_minimum_metadata"

def dedupe(rows):
    out={}
    for a in rows:
        key=(a.get("doi") or a.get("oaiIdentifier") or a.get("id") or "").strip().casefold()
        if not key:
            continue
        old=out.get(key,{})
        # Prefer record with more non-empty fields.
        score=lambda x: sum(bool(x.get(k)) for k in ("title","authors","abstract","keywords","year","doi","url"))
        out[key]=a if score(a)>=score(old) else old
    return list(out.values())

def main():
    rules=load(ROOT/"config/curation_rules.json",{})
    sources=[]

    for p in sorted((ROOT/"harvest/normalized").glob("*.json")):
        if p.name=="catalog.json":
            continue
        x=load(p,{})
        rows=x.get("articles",[]) if isinstance(x,dict) else x if isinstance(x,list) else []
        sources.extend(rows)

    sources=dedupe(sources)
    included=[];quarantine=[];excluded=[]

    for a in sources:
        if a.get("deleted"):
            continue
        status,reason=classify(a,rules)
        a=dict(a)
        a["corpusStatus"]=status
        a["corpusReason"]=reason

        if status=="included":
            included.append(a)
        elif status=="excluded_document_type":
            excluded.append(a)
        else:
            quarantine.append(a)

    save(ROOT/"harvest/normalized/catalog.json",{"articles":included})
    save(ROOT/"harvest/quarantine/needs_metadata.json",{"articles":quarantine})
    save(ROOT/"harvest/quarantine/excluded_document_type.json",{"articles":excluded})

    print(f"source_records={len(sources)} included={len(included)} quarantine={len(quarantine)} excluded={len(excluded)}")

if __name__=="__main__":
    main()
