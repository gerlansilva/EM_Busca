#!/usr/bin/env python3
from __future__ import annotations
import json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def load(p,d):
    try:return json.loads(p.read_text(encoding="utf-8"))
    except Exception:return d
def save(p,v):
    p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

def classify(a,rules):
    title=str(a.get("titleOriginal") or a.get("title") or "").strip()
    authors=a.get("authors") or []
    abstract=str(a.get("abstractOriginal") or a.get("abstract") or "").strip()
    keywords=a.get("keywordsOriginal") or a.get("keywords") or []
    dtype=str(a.get("type") or "").lower()
    if not title:return "needs_metadata","missing_title"
    low=title.casefold()
    for pat in rules["exclude_title_patterns"]:
        if pat.casefold() in low:return "excluded_document_type",pat
    if not authors:return "needs_metadata","missing_authors"
    if not abstract and not keywords:return "needs_metadata","missing_abstract_and_keywords"
    if dtype and dtype not in rules["include"]["document_types"] and "article" not in dtype:
        return "needs_metadata","unknown_document_type"
    return "included","meets_minimum_metadata"

def main():
    rules=load(ROOT/"config/curation_rules.json",{})
    sources=[]
    for p in (ROOT/"harvest/normalized").glob("*.json"):
        x=load(p,{})
        sources.extend(x.get("articles",[]) if isinstance(x,dict) else x if isinstance(x,list) else [])
    included=[];quarantine=[];excluded=[]
    for a in sources:
        status,reason=classify(a,rules);a=dict(a);a["corpusStatus"]=status;a["corpusReason"]=reason
        if status=="included":included.append(a)
        elif status=="excluded_document_type":excluded.append(a)
        else:quarantine.append(a)
    save(ROOT/"harvest/normalized/catalog.json",{"articles":included})
    save(ROOT/"harvest/quarantine/needs_metadata.json",{"articles":quarantine})
    save(ROOT/"harvest/quarantine/excluded_document_type.json",{"articles":excluded})
    print(f"included={len(included)} quarantine={len(quarantine)} excluded={len(excluded)}")
if __name__=="__main__":main()
