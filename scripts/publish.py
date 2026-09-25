#!/usr/bin/env python3
import json
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
FIELDS="id journal title titleOriginal titleEn authors institutions abstract abstractOriginal abstractEn keywords keywordsOriginal keywordsEn metadataEnglishStatus year language languageCode type doi url pdf openAccess oaiIdentifier source provenance corpusStatus corpusReason harvestedAt".split()
def load(p,d):
    try:return json.loads(p.read_text(encoding="utf-8"))
    except Exception:return d
def save(p,v):
    p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
def main():
    src=load(ROOT/"harvest/normalized/catalog.json",{"articles":[]})
    rows=src.get("articles",[])
    rows=[x for x in rows if x.get("corpusStatus")=="included"]
    rows.sort(key=lambda a:(a.get("year") or 0,a.get("id") or ""),reverse=True)
    out=ROOT/"dist/data"
    chunks=[]
    for i in range(0,len(rows),500):
        name=f"records-{i//500:03d}.json";chunks.append(name)
        save(out/name,[{k:a.get(k) for k in FIELDS} for a in rows[i:i+500]])
    for p in out.glob("records-*.json"):
        if p.name not in chunks:p.unlink()
    save(out/"catalog.json",{"demo":False,"partial":False,"updated":datetime.now(timezone.utc).isoformat(),"chunks":chunks,"count":len(rows)})
    journals=load(ROOT/"config/journals.json",[])
    save(out/"journals.json",journals)
    print(f"published={len(rows)}")
if __name__=="__main__":main()
