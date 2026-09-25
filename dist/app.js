const $=s=>document.querySelector(s);
const state={journals:[],articles:[],filtered:[]};
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const arr=v=>Array.isArray(v)?v:(v?[v]:[]);
function norm(s){return String(s??"").normalize("NFD").replace(/[\u0300-\u036f]/g,"").toLowerCase()}
function searchText(a){return norm([a.title,a.titleOriginal,a.titleEn,...arr(a.authors),a.abstract,a.abstractOriginal,a.abstractEn,...arr(a.keywords),...arr(a.keywordsOriginal),...arr(a.keywordsEn)].join(" "))}
function compile(q){
  q=q.trim(); if(!q)return ()=>true;
  const tokens=q.match(/"[^"]+"|\(|\)|\bAND\b|\bOR\b|\bNOT\b|[^\s()]+/gi)||[];
  let p=0;
  const prim=()=>{const t=tokens[p++];if(!t)return ()=>true;if(t==="("){const f=or();p++;return f}if(/^NOT$/i.test(t)){const f=prim();return x=>!f(x)}const v=norm(t.replace(/^"|"$/g,""));return x=>x.includes(v)};
  const and=()=>{let f=prim();while(p<tokens.length&&!/^OR$/i.test(tokens[p])&&tokens[p]!==")"){if(/^AND$/i.test(tokens[p]))p++;const g=prim(),old=f;f=x=>old(x)&&g(x)}return f};
  const or=()=>{let f=and();while(/^OR$/i.test(tokens[p]||"")){p++;const g=and(),old=f;f=x=>old(x)||g(x)}return f};
  return or();
}
function langLabel(v){return ({pt:"Portuguese",en:"English",es:"Spanish",de:"German",fr:"French"})[v]||v||""}
function externalLinks(a){
  const links=[];
  if(a.url) links.push(`<a href="${esc(a.url)}" target="_blank" rel="noopener">Article</a>`);
  if(a.doi){
    const d=`https://doi.org/${a.doi}`;
    if(!a.url||a.url!==d) links.push(`<a href="${esc(d)}" target="_blank" rel="noopener">DOI</a>`);
  }
  if(a.pdf) links.push(`<a href="${esc(a.pdf)}" target="_blank" rel="noopener">PDF</a>`);
  return links.length?`<div class="record-links">${links.join(" · ")}</div>`:"";
}
function render(){
  $("#count").textContent=`${state.filtered.length.toLocaleString()} results`;
  $("#results").innerHTML=state.filtered.map(a=>{
    const j=state.journals.find(x=>x.id===a.journal);
    const title=a.titleOriginal||a.title||a.titleEn||"Untitled";
    const abstract=a.abstractOriginal||a.abstract||"";
    const keywords=arr(a.keywordsOriginal||a.keywords);
    return `<article class="card"><h2>${esc(title)}</h2>
    <div class="meta">${esc(j?.name||a.journal||"")} · ${esc(a.year||"n.d.")} · ${esc(langLabel(a.languageCode||a.language))}</div>
    <p>${esc(arr(a.authors).join("; "))}</p>
    ${abstract?`<p class="abstract">${esc(abstract)}</p>`:""}
    <div class="chips">${keywords.slice(0,8).map(k=>`<span class="chip">${esc(k)}</span>`).join("")}</div>
    ${externalLinks(a)}</article>`;
  }).join("")||'<div class="card">No curated records yet.</div>';
}
function apply(){
  const pred=compile($("#query").value),j=$("#journal").value,l=$("#language").value;
  const y1=Number($("#yearFrom").value||0),y2=Number($("#yearTo").value||9999);
  state.filtered=state.articles.filter(a=>pred(searchText(a))&&(!j||a.journal===j)&&(!l||(a.languageCode||a.language)===l)&&(!y1||a.year>=y1)&&(!y2||a.year<=y2));
  render();
}
async function init(){
  const [m,j]=await Promise.all([fetch("./data/catalog.json").then(r=>r.json()),fetch("./data/journals.json").then(r=>r.json())]);
  state.journals=j; const batches=await Promise.all((m.chunks||[]).map(x=>fetch("./data/"+x).then(r=>r.json())));
  state.articles=batches.flat(); state.filtered=[...state.articles];
  $("#summary").textContent=`${state.articles.length.toLocaleString()} curated articles · ${state.journals.length} registered journals`;
  $("#journal").innerHTML='<option value="">All journals</option>'+state.journals.map(x=>`<option value="${esc(x.id)}">${esc(x.name)}</option>`).join("");
  const langs=[...new Set(state.articles.map(a=>a.languageCode||a.language).filter(Boolean))].sort();
  $("#language").innerHTML='<option value="">All languages</option>'+langs.map(x=>`<option value="${esc(x)}">${esc(langLabel(x))}</option>`).join("");
  render();
}
$("#searchForm").onsubmit=e=>{e.preventDefault();apply()};
["journal","language","yearFrom","yearTo"].forEach(id=>$("#"+id).onchange=apply);
$("#clear").onclick=()=>{["query","journal","language","yearFrom","yearTo"].forEach(id=>$("#"+id).value="");apply()};
init();
