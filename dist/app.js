const $=s=>document.querySelector(s);
const $$=s=>[...document.querySelectorAll(s)];
const state={
  journals:[],articles:[],filtered:[],
  selectedJournals:new Set(),
  view:"list",visible:30,queryActive:false
};

const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const arr=v=>Array.isArray(v)?v:(v?[v]:[]);
const norm=s=>String(s??"").normalize("NFD").replace(/[\u0300-\u036f]/g,"").toLowerCase().trim();
const uniq=a=>[...new Set(a.filter(Boolean))];

function fmtDate(iso){
  if(!iso)return "—";
  const d=new Date(iso);
  if(Number.isNaN(d.getTime()))return "—";
  return new Intl.DateTimeFormat("pt-BR").format(d);
}

function langLabel(v){
  return ({pt:"Português",en:"Inglês",es:"Espanhol",de:"Alemão",fr:"Francês",it:"Italiano"})[v]||v||"";
}

function searchText(a){
  return norm([
    a.title,a.titleOriginal,a.titleEn,
    ...arr(a.authors),
    a.abstract,a.abstractOriginal,a.abstractEn,
    ...arr(a.keywords),...arr(a.keywordsOriginal),...arr(a.keywordsEn),
    a.doi
  ].join(" "));
}

function tokenize(q){
  return q.match(/"[^"]+"|\(|\)|\bAND\b|\bOR\b|\bNOT\b|[^\s()]+/gi)||[];
}

function compile(q){
  q=q.trim();
  if(!q)return ()=>true;
  const tokens=tokenize(q);
  let p=0;
  const prim=()=>{
    const t=tokens[p++];
    if(!t)return ()=>true;
    if(t==="("){const f=or();if(tokens[p]===")")p++;return f}
    if(/^NOT$/i.test(t)){const f=prim();return x=>!f(x)}
    const v=norm(t.replace(/^"|"$/g,""));
    return x=>x.includes(v);
  };
  const and=()=>{
    let f=prim();
    while(p<tokens.length&&!/^OR$/i.test(tokens[p])&&tokens[p]!==")"){
      if(/^AND$/i.test(tokens[p]))p++;
      const g=prim(),old=f;
      f=x=>old(x)&&g(x);
    }
    return f;
  };
  const or=()=>{
    let f=and();
    while(/^OR$/i.test(tokens[p]||"")){
      p++;
      const g=and(),old=f;
      f=x=>old(x)||g(x);
    }
    return f;
  };
  return or();
}

function journalCounts(rows){
  const m=new Map();
  rows.forEach(a=>m.set(a.journal,(m.get(a.journal)||0)+1));
  return m;
}

function renderJournalFilters(baseRows=state.articles){
  const counts=journalCounts(baseRows);
  const withArticles=state.journals
    .map(j=>({...j,count:counts.get(j.id)||0}))
    .filter(j=>j.count>0 || state.selectedJournals.has(j.id))
    .sort((a,b)=>b.count-a.count || a.name.localeCompare(b.name));

  $("#journalsPanel").innerHTML=withArticles.map(j=>`
    <label class="journal-option">
      <input type="checkbox" value="${esc(j.id)}" ${state.selectedJournals.has(j.id)?"checked":""}>
      <span class="journal-dot"></span>
      <span class="journal-name">${esc(j.name)}</span>
      <span class="journal-count">${j.count.toLocaleString("pt-BR")}</span>
    </label>`).join("");

  $$("#journalsPanel input").forEach(el=>{
    el.onchange=()=>{
      if(el.checked)state.selectedJournals.add(el.value);
      else state.selectedJournals.delete(el.value);
      state.visible=30;
      apply(false);
    };
  });
}

function sortRows(rows){
  const mode=$("#sort").value;
  const copy=[...rows];
  if(mode==="title-asc")copy.sort((a,b)=>(a.titleOriginal||a.title||"").localeCompare(b.titleOriginal||b.title||""));
  else if(mode==="title-desc")copy.sort((a,b)=>(b.titleOriginal||b.title||"").localeCompare(a.titleOriginal||a.title||""));
  else if(mode==="year-desc")copy.sort((a,b)=>(b.year||0)-(a.year||0));
  else if(mode==="year-asc")copy.sort((a,b)=>(a.year||0)-(b.year||0));
  else if(state.queryActive){
    const q=norm($("#query").value);
    const terms=q.replace(/\b(and|or|not)\b/gi," ").replace(/["()]/g," ").split(/\s+/).filter(Boolean);
    copy.sort((a,b)=>score(b,terms)-score(a,terms));
  }
  return copy;
}

function score(a,terms){
  if(!terms.length)return 0;
  const title=norm(a.titleOriginal||a.title||"");
  const kw=norm(arr(a.keywordsOriginal||a.keywords).join(" "));
  const abs=norm(a.abstractOriginal||a.abstract||"");
  const auth=norm(arr(a.authors).join(" "));
  return terms.reduce((s,t)=>s+(title.includes(t)?8:0)+(kw.includes(t)?5:0)+(auth.includes(t)?4:0)+(abs.includes(t)?2:0),0);
}

function articleUrl(a){
  if(a.url)return a.url;
  if(a.doi)return `https://doi.org/${a.doi}`;
  return "";
}

function card(a,i){
  const j=state.journals.find(x=>x.id===a.journal);
  const title=a.titleOriginal||a.title||a.titleEn||"Sem título";
  const abstract=a.abstractOriginal||a.abstract||"";
  const keywords=uniq(arr(a.keywordsOriginal||a.keywords)).slice(0,6);
  const url=articleUrl(a);
  const doiUrl=a.doi?`https://doi.org/${a.doi}`:"";
  const links=[];
  if(url)links.push(`<a class="primary" href="${esc(url)}" target="_blank" rel="noopener">▣ Ler artigo ↗</a>`);
  if(a.pdf)links.push(`<a href="${esc(a.pdf)}" target="_blank" rel="noopener">PDF ↗</a>`);
  if(doiUrl)links.push(`<a href="${esc(doiUrl)}" target="_blank" rel="noopener">〃 DOI</a>`);

  return `<article class="card" data-index="${i}">
    <div class="card-top">
      <span class="journal-badge">${esc(j?.name||a.journal||"Periódico")}</span>
      <span class="year">▣ ${esc(a.year||"s.d.")}</span>
    </div>
    <h3>${url?`<a href="${esc(url)}" target="_blank" rel="noopener">${esc(title)}</a>`:esc(title)}</h3>
    <div class="authors">♧ ${esc(arr(a.authors).join("; ")||"Autoria não informada")}</div>
    ${abstract?`<p class="abstract">${esc(abstract)}</p><button class="toggle-abstract" type="button">Ler resumo completo⌄</button>`:""}
    ${keywords.length?`<div class="keywords">${keywords.map(k=>`<span class="keyword">${esc(k)}</span>`).join("")}</div>`:""}
    <div class="card-footer">
      <div class="secondary-meta">${esc(langLabel(a.languageCode||a.language))}${a.doi?` · ${esc(a.doi)}`:""}</div>
      <div class="record-actions">${links.join("")}</div>
    </div>
  </article>`;
}

function render(){
  const sorted=sortRows(state.filtered);
  const visible=sorted.slice(0,state.visible);

  $("#count").textContent=`${state.filtered.length.toLocaleString("pt-BR")} resultados`;
  $("#results").className=state.view==="grid"?"results-list grid":"results-list";
  $("#results").innerHTML=visible.map((a,i)=>card(a,i)).join("") || `<div class="card">Nenhum registro encontrado para os filtros atuais.</div>`;

  $$(".toggle-abstract").forEach(btn=>{
    btn.onclick=()=>{
      const c=btn.closest(".card");
      const expanded=c.classList.toggle("expanded");
      btn.textContent=expanded?"Recolher resumo⌃":"Ler resumo completo⌄";
    };
  });

  $("#loadMore").hidden=state.visible>=sorted.length;
}

function apply(updateJournals=true){
  const q=$("#query").value.trim();
  const pred=compile(q);
  const l=$("#language").value;
  const y1=Number($("#yearFrom").value||0);
  const y2=Number($("#yearTo").value||9999);
  state.queryActive=Boolean(q);

  state.filtered=state.articles.filter(a=>
    pred(searchText(a)) &&
    (!state.selectedJournals.size||state.selectedJournals.has(a.journal)) &&
    (!l||(a.languageCode||a.language)===l) &&
    (!y1||(a.year||0)>=y1) &&
    (!y2||(a.year||0)<=y2)
  );

  if(updateJournals){
    const withoutJournalFilter=state.articles.filter(a=>
      pred(searchText(a)) &&
      (!l||(a.languageCode||a.language)===l) &&
      (!y1||(a.year||0)>=y1) &&
      (!y2||(a.year||0)<=y2)
    );
    renderJournalFilters(withoutJournalFilter);
  }
  render();
}

async function init(){
  const [m,j]=await Promise.all([
    fetch("./data/catalog.json").then(r=>r.json()),
    fetch("./data/journals.json").then(r=>r.json())
  ]);

  state.journals=j;
  const batches=await Promise.all((m.chunks||[]).map(x=>fetch("./data/"+x).then(r=>r.json())));
  state.articles=batches.flat();
  state.filtered=[...state.articles];

  $("#articleTotal").textContent=state.articles.length.toLocaleString("pt-BR");
  $("#journalTotal").textContent=state.journals.length.toLocaleString("pt-BR");
  $("#updatedDate").textContent=fmtDate(m.updated);

  const langs=uniq(state.articles.map(a=>a.languageCode||a.language)).sort();
  $("#language").innerHTML='<option value="">Todos os idiomas</option>'+langs.map(x=>`<option value="${esc(x)}">${esc(langLabel(x))}</option>`).join("");

  renderJournalFilters();
  render();
}

$("#searchForm").onsubmit=e=>{e.preventDefault();state.visible=30;apply()};
$("#query").oninput=()=>{if(!$("#query").value.trim()){state.visible=30;apply()}};
["language","yearFrom","yearTo"].forEach(id=>$("#"+id).onchange=()=>{state.visible=30;apply()});
$("#sort").onchange=render;

$("#clear").onclick=()=>{
  $("#query").value="";
  $("#language").value="";
  $("#yearFrom").value="";
  $("#yearTo").value="";
  $("#sort").value="relevance";
  state.selectedJournals.clear();
  state.visible=30;
  apply();
};

$("#gridView").onclick=()=>{
  state.view="grid";
  $("#gridView").classList.add("active");
  $("#listView").classList.remove("active");
  render();
};
$("#listView").onclick=()=>{
  state.view="list";
  $("#listView").classList.add("active");
  $("#gridView").classList.remove("active");
  render();
};

$("#loadMore").onclick=()=>{state.visible+=30;render()};

document.addEventListener("click",e=>{
  const btn=e.target.closest("[data-toggle]");
  if(!btn)return;
  const el=$("#"+btn.dataset.toggle);
  if(el)el.hidden=!el.hidden;
});

init();
