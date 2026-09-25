import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];d=ROOT/"dist/data"
m=json.loads((d/"catalog.json").read_text());n=0
for c in m.get("chunks",[]):
    rows=json.loads((d/c).read_text());n+=len(rows)
assert n==m.get("count"),(n,m.get("count"))
print(f"OK {n} records")
