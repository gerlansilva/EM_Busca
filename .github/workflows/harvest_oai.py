#!/usr/bin/env python3
"""
BuscaEM — coletor OAI-PMH v3

Mudanças principais:
- mantém a lógica multilíngue da v2;
- tenta xml.etree primeiro;
- se o XML vier malformado, usa lxml em modo recover=True;
- salva a página problemática para diagnóstico;
- aumenta timeout para fontes OAI lentas;
- mantém checkpoint a cada página concluída.
"""
from __future__ import annotations

import argparse, hashlib, html, json, re, ssl, time
import urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

try:
    from lxml import etree as LET
except Exception:
    LET = None

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT/"harvest"/"SOURCE_AUDIT.json"
JOURNALS = ROOT/"config"/"journals.json"
OUTDIR = ROOT/"harvest"/"normalized"
STATEDIR = ROOT/"harvest"/"state"
RAWDIR = ROOT/"harvest"/"raw"/"oai"

USER_AGENT = "BuscaEM/3.0 metadata-harvester (https://github.com/gerlansilva/EM_Busca)"
TIMEOUT = 90
SLEEP = 0.35
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

def now():
    return datetime.now(timezone.utc).isoformat()

def load(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def save(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix+".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    tmp.replace(path)

def clean_text(v):
    if v is None:
        return ""
    s = html.unescape(str(v))
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def unique(seq):
    out=[]; seen=set()
    for x in seq:
        x=clean_text(x)
        if not x: continue
        key=re.sub(r"\s+"," ",x).strip().casefold()
        if key not in seen:
            seen.add(key); out.append(x)
    return out

def normalize_doi(v):
    s=clean_text(v)
    s=re.sub(r"^https?://(dx\.)?doi\.org/","",s,flags=re.I)
    s=re.sub(r"^doi:\s*","",s,flags=re.I)
    m=re.search(r"10\.\d{4,9}/\S+",s,flags=re.I)
    return m.group(0).rstrip(".,;)").lower() if m else ""

def canon_lang(v):
    s=clean_text(v).lower().replace("_","-")
    mp={
      "por":"pt","pt":"pt","pt-br":"pt","portuguese":"pt","português":"pt",
      "eng":"en","en":"en","en-us":"en","en-gb":"en","english":"en",
      "spa":"es","es":"es","es-es":"es","spanish":"es","español":"es",
      "deu":"de","ger":"de","de":"de","german":"de","deutsch":"de",
      "fra":"fr","fre":"fr","fr":"fr","french":"fr"
    }
    return mp.get(s, s[:2] if len(s)>=2 else s)

def detect_lang(text):
    s=" "+clean_text(text).casefold()+" "
    if not s.strip(): return ""
    scores={"pt":0,"en":0,"es":0}
    for w in (" de "," do "," da "," dos "," das "," e "," em "," para "," com "," uma "," um "," ensino "," formação "," professores "," educação "," matemática "):
        scores["pt"] += s.count(w)
    for w in (" the "," of "," and "," in "," for "," with "," a "," an "," teaching "," teacher "," education "," mathematics "):
        scores["en"] += s.count(w)
    for w in (" de "," del "," la "," las "," los "," y "," en "," para "," con "," una "," un "," enseñanza "," profesores "," educación "," matemática "):
        scores["es"] += s.count(w)
    if any(c in s for c in "ãõç"): scores["pt"] += 3
    if "ción" in s or "ñ" in s: scores["es"] += 3
    best=max(scores,key=scores.get)
    return best if scores[best]>0 else ""

def values(parent, local):
    out=[]
    for el in parent.iter():
        tag = el.tag if isinstance(el.tag, str) else ""
        if tag.split("}")[-1]==local and el.text:
            t=clean_text(el.text)
            if t:
                out.append({"text":t,"lang":canon_lang(el.attrib.get(XML_LANG,"") or el.attrib.get("lang",""))})
    return out

def text_list(vals):
    return unique(v["text"] for v in vals)

def choose_one(vals, target_lang):
    if not vals: return ""
    for v in vals:
        if v["lang"]==target_lang:
            return v["text"]
    detected=[v for v in vals if detect_lang(v["text"])==target_lang]
    if detected:
        return max((v["text"] for v in detected), key=len)
    return vals[0]["text"]

def choose_translation(vals, lang, original):
    for v in vals:
        if v["lang"]==lang and v["text"]!=original:
            return v["text"]
    detected=[v["text"] for v in vals if detect_lang(v["text"])==lang and v["text"]!=original]
    return max(detected,key=len) if detected else ""

def choose_keywords(vals, target_lang):
    if not vals: return []
    explicit=[v["text"] for v in vals if v["lang"]==target_lang]
    if explicit:
        return unique(explicit)
    detected=[v["text"] for v in vals if detect_lang(v["text"])==target_lang]
    if detected:
        return unique(detected)
    return unique(v["text"] for v in vals)

def year_from_values(vals):
    for v in vals:
        m=re.search(r"\b(18|19|20|21)\d{2}\b", clean_text(v))
        if m:return int(m.group(0))
    return None

def stable_id(journal_id,doi,oai_identifier,title,year):
    if doi:return "doi:"+doi
    if oai_identifier:return "oai:"+oai_identifier
    raw=f"{journal_id}|{title.casefold()}|{year or ''}"
    return "local:"+hashlib.sha1(raw.encode()).hexdigest()

def request(url):
    req=urllib.request.Request(url,headers={"User-Agent":USER_AGENT,"Accept":"application/xml,text/xml,*/*"})
    ctx=ssl.create_default_context()
    last=None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req,timeout=TIMEOUT,context=ctx) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last=e
            if e.code in (429,500,502,503,504) and attempt<4:
                time.sleep(2**attempt); continue
            raise
        except Exception as e:
            last=e
            if attempt<4:
                time.sleep(2**attempt); continue
            raise last

def q(base,params):
    return base+("&" if "?" in base else "?")+urllib.parse.urlencode(params)

def lname(tag):
    if not isinstance(tag,str): return ""
    return tag.split("}")[-1]

def first_text(parent, local):
    for el in parent.iter():
        if lname(el.tag)==local and el.text:
            return clean_text(el.text)
    return ""

def article_id_from_oai(identifier):
    m=re.search(r"article/(\d+)", identifier or "")
    return m.group(1) if m else ""

def build_ojs_article_url(journal_cfg, identifier):
    aid=article_id_from_oai(identifier)
    if not aid:return ""
    u=journal_cfg.get("url") or ""
    if not u:return ""
    p=urllib.parse.urlsplit(u)
    path=p.path.rstrip("/")
    path=re.sub(r"/(?:pt_BR|en_US|en|es_ES|es)$","",path)
    return urllib.parse.urlunsplit((p.scheme,p.netloc,path+f"/article/view/{aid}","",""))

def parse_dc_record(record,journal):
    header=next((x for x in record if lname(x.tag)=="header"),None)
    metadata=next((x for x in record if lname(x.tag)=="metadata"),None)
    if header is None:return None

    status=header.attrib.get("status","")
    identifier=first_text(header,"identifier")
    datestamp=first_text(header,"datestamp")

    if status=="deleted":
        return {"id":"oai:"+identifier if identifier else "","journal":journal["id"],"oaiIdentifier":identifier,
                "deleted":True,"source":"oai-pmh","harvestedAt":now()}

    if metadata is None:return None

    titles=values(metadata,"title")
    creators=unique(v["text"] for v in values(metadata,"creator"))
    subjects=values(metadata,"subject")
    descriptions=values(metadata,"description")
    dates=text_list(values(metadata,"date"))
    langs=text_list(values(metadata,"language"))
    types=text_list(values(metadata,"type"))
    ids=text_list(values(metadata,"identifier"))
    relations=text_list(values(metadata,"relation"))
    sources=text_list(values(metadata,"source"))
    publishers=text_list(values(metadata,"publisher"))

    lang=canon_lang(langs[0]) if langs else ""
    if not lang:
        probe=max([v["text"] for v in descriptions],key=len,default="") or (titles[0]["text"] if titles else "")
        lang=detect_lang(probe)

    title_original=choose_one(titles,lang) or (titles[0]["text"] if titles else "")
    title_en=choose_translation(titles,"en",title_original) if lang!="en" else title_original
    abstract_original=choose_one(descriptions,lang)
    abstract_en=choose_translation(descriptions,"en",abstract_original) if lang!="en" else abstract_original
    keywords_original=choose_keywords(subjects,lang)
    keywords_en=choose_keywords(subjects,"en") if lang!="en" else keywords_original

    if lang!="en":
        orig_cf={x.casefold() for x in keywords_original}
        keywords_en=[x for x in keywords_en if x.casefold() not in orig_cf]

    doi=""; url=""; pdf=""
    for v in ids+relations:
        d=normalize_doi(v)
        if d and not doi: doi=d
        if re.match(r"^https?://",v,re.I):
            if v.lower().endswith(".pdf") and not pdf: pdf=v
            elif "doi.org/" not in v.lower() and not url: url=v

    if not url:
        url=build_ojs_article_url(journal.get("config",{}),identifier)
    if not url and doi:
        url="https://doi.org/"+doi

    year=year_from_values(dates)
    dtype=" ".join(types).lower()
    normalized_type="article" if ("article" in dtype or not dtype) else clean_text(types[0]).lower()
    rid=stable_id(journal["id"],doi,identifier,title_original,year)

    return {
      "id":rid,"journal":journal["id"],"journalName":journal.get("name",""),
      "title":title_original,"titleOriginal":title_original,"titleEn":title_en if title_en!=title_original else "",
      "authors":creators,"institutions":[],
      "abstract":abstract_original,"abstractOriginal":abstract_original,
      "abstractEn":abstract_en if abstract_en!=abstract_original else "",
      "keywords":keywords_original,"keywordsOriginal":keywords_original,"keywordsEn":keywords_en,
      "metadataEnglishStatus":"source_provided" if (title_en or abstract_en or keywords_en) and lang!="en" else "source_only",
      "year":year,"language":lang,"languageCode":lang,"type":normalized_type,
      "doi":doi,"url":url,"pdf":pdf,"openAccess":None,"oaiIdentifier":identifier,
      "source":"oai-pmh",
      "provenance":{"baseUrl":journal["oaiBase"],"metadataPrefix":"oai_dc","datestamp":datestamp,
                    "allTitles":[v["text"] for v in titles],
                    "allDescriptions":[v["text"] for v in descriptions],
                    "allSubjects":[v["text"] for v in subjects],
                    "sourceValues":{"dates":dates,"types":types,"sources":sources,"publishers":publishers}},
      "harvestedAt":now()
    }

def parse_root(xml_bytes, journal_id, page_no):
    try:
        return ET.fromstring(xml_bytes), False
    except ET.ParseError as e:
        bad = RAWDIR/journal_id/f"parse-error-page-{page_no}.xml"
        bad.parent.mkdir(parents=True,exist_ok=True)
        bad.write_bytes(xml_bytes)
        print(f"  XML malformed on page {page_no}: {e}")
        if LET is None:
            raise RuntimeError("Malformed XML and lxml is unavailable for recovery") from e
        parser=LET.XMLParser(recover=True, huge_tree=True, resolve_entities=False, no_network=True)
        root=LET.fromstring(xml_bytes, parser=parser)
        if root is None:
            raise RuntimeError("lxml recovery could not build an XML tree") from e
        print(f"  recovered malformed XML with lxml ({len(parser.error_log)} parser warnings)")
        return root, True

def parse_page(xml_bytes,journal,page_no):
    root,recovered=parse_root(xml_bytes,journal["id"],page_no)
    err=next((x for x in root.iter() if lname(x.tag)=="error"),None)
    if err is not None:
        code=err.attrib.get("code",""); msg=clean_text(err.text or "")
        if code=="noRecordsMatch":return [],"",recovered
        raise RuntimeError(f"OAI error {code}: {msg}")

    rows=[]
    skipped=0
    for el in root.iter():
        if lname(el.tag)=="record":
            try:
                row=parse_dc_record(el,journal)
                if row:rows.append(row)
            except Exception as e:
                skipped+=1
                print(f"  warning: skipped one malformed record: {type(e).__name__}: {e}")

    token_el=next((x for x in root.iter() if lname(x.tag)=="resumptionToken"),None)
    token=clean_text(token_el.text) if token_el is not None and token_el.text else ""
    if skipped:
        print(f"  warning: skipped {skipped} record(s) on page {page_no}")
    return rows,token,recovered

def verified_oai_rows():
    audit=load(AUDIT,{})
    config={j["id"]:j for j in load(JOURNALS,[])}
    out=[]
    for row in audit.get("journals",[]):
        verified=[x for x in row.get("oai",[]) if x.get("verified")]
        if not verified:continue
        cfg=config.get(row["id"],{})
        out.append({"id":row["id"],"name":row.get("name") or cfg.get("name",""),
                    "oaiBase":verified[0]["baseUrl"],"config":cfg})
    return out

def quality(a):
    return sum(bool(a.get(k)) for k in ("titleOriginal","authors","abstractOriginal","keywordsOriginal","year","doi","url"))

def merge_records(existing,incoming):
    byid={x.get("id"):x for x in existing if x.get("id")}
    for x in incoming:
        if not x.get("id"):continue
        if x.get("deleted"):byid.pop(x["id"],None)
        else:
            old=byid.get(x["id"],{})
            byid[x["id"]]={**old,**x} if quality(x)>=quality(old) else old
    return list(byid.values())

def harvest_one(journal,limit=None,reset=False):
    jid=journal["id"]; out_file=OUTDIR/f"{jid}.json"; state_file=STATEDIR/f"{jid}.json"
    if reset and state_file.exists():
        state_file.unlink()

    existing=load(out_file,{"articles":[]}).get("articles",[])
    state={} if reset else load(state_file,{})
    token=state.get("resumptionToken","")
    pages=int(state.get("pages",0)); seen=0
    recovered_pages=int(state.get("recoveredPages",0))

    print(f"\n[{jid}] {journal['name']}")
    while True:
        url=q(journal["oaiBase"],{"verb":"ListRecords","resumptionToken":token}) if token else q(journal["oaiBase"],{"verb":"ListRecords","metadataPrefix":"oai_dc"})
        raw=request(url); pages+=1
        raw_path=RAWDIR/jid/"last-page.xml"
        raw_path.parent.mkdir(parents=True,exist_ok=True)
        raw_path.write_bytes(raw)

        rows,next_token,recovered=parse_page(raw,journal,pages)
        if recovered:
            recovered_pages+=1

        if limit is not None:
            rows=rows[:max(0,limit-seen)]

        existing=merge_records(existing,rows)
        seen+=len(rows)

        save(out_file,{
          "journal":jid,"journalName":journal["name"],"source":"oai-pmh",
          "sourceUrl":journal["oaiBase"],"updatedAt":now(),"articles":existing
        })
        save(state_file,{
          "journal":jid,"resumptionToken":next_token,"pages":pages,
          "recordsSeenThisRun":seen,"recoveredPages":recovered_pages,
          "updatedAt":now(),"complete":not bool(next_token)
        })
        print(f"  page {pages}: +{len(rows)} | file={len(existing)}")

        if limit is not None and seen>=limit:
            print("  stopped by limit; checkpoint saved"); break
        if not next_token:
            print("  complete"); break
        token=next_token
        time.sleep(SLEEP)

    return len(existing)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--journal",default="all")
    ap.add_argument("--limit",type=int,default=None)
    ap.add_argument("--reset",action="store_true")
    args=ap.parse_args()

    rows=verified_oai_rows()
    if args.journal!="all":
        rows=[x for x in rows if x["id"]==args.journal]
        if not rows:raise SystemExit(f"No verified OAI source for: {args.journal}")

    total=0
    errors=0
    for j in rows:
        try:
            total+=harvest_one(j,args.limit,args.reset)
        except Exception as e:
            errors+=1
            print(f"ERROR [{j['id']}]: {type(e).__name__}: {e}")

    print(f"\nFinished. Records across selected files: {total}")
    if errors and args.journal!="all":
        raise SystemExit(2)

if __name__=="__main__":
    main()
