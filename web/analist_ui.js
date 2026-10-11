/* analist_ui.js — Coin Analist cevabını profesyonel karta çevirir (index.html + grafik.html ortak).
   Kullanım: anRender(cevapMetni, planJSON) → HTML string. Stil bu dosyadan enjekte edilir. */
(function(){
const CSS=`
.anr{font-size:13px;line-height:1.5;color:var(--txt,#e8eef3)}
.anr *{box-sizing:border-box}
.anr-head{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:12px 14px;border-radius:12px;
  background:linear-gradient(135deg,rgba(242,181,68,.10),rgba(106,168,255,.06));border:1px solid var(--line-2,rgba(148,170,190,.22))}
.anr-coin{font-size:22px;font-weight:800;letter-spacing:.3px}
.anr-coin small{font-size:12px;font-weight:500;color:var(--dim,#93a3b3);margin-left:4px}
.anr-px{font-family:var(--mono,ui-monospace,monospace);font-size:18px;font-weight:600}
.anr-ts{font-size:11.5px;color:var(--dim2,#5e6e7e);margin-left:auto}
.anr-badge{font-weight:800;padding:4px 12px;border-radius:999px;font-size:12.5px;letter-spacing:.4px}
.anr-badge.LONG{background:rgba(53,208,127,.18);color:#35d07f;border:1px solid rgba(53,208,127,.4)}
.anr-badge.SHORT{background:rgba(255,91,110,.18);color:#ff5b6e;border:1px solid rgba(255,91,110,.4)}
.anr-badge.BEKLE{background:rgba(242,181,68,.18);color:#f2b544;border:1px solid rgba(242,181,68,.4)}
.anr-sec{margin-top:12px;border:1px solid var(--line,rgba(148,170,190,.12));border-radius:12px;background:var(--surf,#111820);overflow:hidden}
.anr-sec>h4{margin:0;padding:9px 14px;font-size:12px;letter-spacing:.6px;text-transform:uppercase;color:var(--dim,#93a3b3);
  background:rgba(148,170,190,.06);border-bottom:1px solid var(--line,rgba(148,170,190,.12));display:flex;align-items:center;gap:8px}
.anr-sec>h4 .cnt{margin-left:auto;font-weight:500;text-transform:none;letter-spacing:0;color:var(--dim2,#5e6e7e)}
.anr-body{padding:10px 14px}
.anr-news{display:flex;flex-direction:column;gap:8px}
.anr-n{display:grid;grid-template-columns:auto 1fr;gap:10px;padding:8px 10px;border-radius:9px;background:rgba(148,170,190,.05);border-left:3px solid #5e6e7e}
.anr-n.pos{border-left-color:#35d07f}.anr-n.neg{border-left-color:#ff5b6e}
.anr-n .ic{font-size:16px;line-height:1.2}
.anr-n .meta{font-size:11px;color:var(--dim2,#5e6e7e);margin-bottom:2px}
.anr-n .meta b{color:var(--dim,#93a3b3);font-weight:600}
.anr-n .refl{display:inline-block;margin-top:4px;font-size:11px;padding:1px 7px;border-radius:5px;background:rgba(148,170,190,.1);color:var(--dim,#93a3b3)}
.anr-tbl{width:100%;border-collapse:separate;border-spacing:0;font-size:12px}
.anr-tbl th{font-weight:600;color:var(--dim2,#5e6e7e);text-align:left;padding:5px 8px;border-bottom:1px solid var(--line,rgba(148,170,190,.12));font-size:11px;text-transform:uppercase;letter-spacing:.4px}
.anr-tbl td{padding:6px 8px;border-bottom:1px solid rgba(148,170,190,.06);font-family:var(--mono,ui-monospace,monospace);font-size:12px}
.anr-tbl td:first-child{font-weight:700;font-family:inherit}
.anr-chip{display:inline-block;padding:1px 8px;border-radius:5px;font-size:11px;font-weight:700;font-family:inherit}
.anr-chip.up{background:rgba(53,208,127,.16);color:#35d07f}.anr-chip.dn{background:rgba(255,91,110,.16);color:#ff5b6e}
.anr-chip.flat{background:rgba(242,181,68,.16);color:#f2b544}.anr-chip.mix{background:rgba(148,170,190,.14);color:#93a3b3}
.anr-ul{margin:8px 0 0 0;padding:0;list-style:none}
.anr-ul li{position:relative;padding:3px 0 3px 16px}
.anr-ul li:before{content:"";position:absolute;left:2px;top:11px;width:6px;height:6px;border-radius:50%;background:var(--amb,#f2b544);opacity:.7}
.anr-dec{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.anr-dec .big{font-size:15px;font-weight:700}
.anr-conf{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;color:var(--dim,#93a3b3)}
.anr-conf i{display:inline-block;width:8px;height:8px;border-radius:2px;background:rgba(148,170,190,.2)}
.anr-conf i.on{background:#f2b544}
.anr-plan{display:grid;grid-template-columns:minmax(170px,230px) 1fr;gap:14px}
@media(max-width:640px){.anr-plan{grid-template-columns:1fr}}
.anr-ladder{position:relative;padding-left:18px;font-family:var(--mono,ui-monospace,monospace);font-size:12px}
.anr-ladder:before{content:"";position:absolute;left:5px;top:8px;bottom:8px;width:2px;background:rgba(148,170,190,.18);border-radius:2px}
.anr-lv{position:relative;display:flex;justify-content:space-between;gap:8px;padding:5px 8px;margin:3px 0;border-radius:7px;background:rgba(148,170,190,.05)}
.anr-lv:before{content:"";position:absolute;left:-16px;top:50%;width:8px;height:8px;border-radius:50%;transform:translateY(-50%);background:var(--c)}
.anr-lv .n{color:var(--dim,#93a3b3);font-family:inherit}
.anr-lv .v{font-weight:700}
.anr-lv .d{color:var(--dim2,#5e6e7e);font-size:11px}
.anr-lv.tp{--c:#35d07f}.anr-lv.en{--c:#f2b544}.anr-lv.sl{--c:#ff5b6e}.anr-lv.inv{--c:#b98cff}
.anr-lv.now{--c:#6aa8ff;background:rgba(106,168,255,.10);border:1px solid rgba(106,168,255,.3)}
.anr-rr{margin-top:8px;font-size:11.5px;color:var(--dim,#93a3b3)}
.anr-rr b{color:#35d07f}
.anr-kv{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;font-size:12.5px}
.anr-kv .k{color:var(--dim2,#5e6e7e);white-space:nowrap}
.anr-kv .k.y{color:#f2b544}.anr-kv .k.s{color:#ff5b6e}.anr-kv .k.t{color:#35d07f}.anr-kv .k.i{color:#b98cff}
.anr-note{margin-top:12px;padding:8px 12px;border-radius:9px;font-size:11.5px;color:var(--dim,#93a3b3);background:rgba(242,181,68,.06);border:1px dashed rgba(242,181,68,.3)}
.anr details{margin-top:10px;font-size:11.5px;color:var(--dim,#93a3b3)}
.anr details summary{cursor:pointer;color:var(--dim2,#5e6e7e)}
.anr a{color:var(--amb,#f2b544);text-decoration:none}.anr a:hover{text-decoration:underline}
.anr code{background:rgba(148,170,190,.1);padding:0 4px;border-radius:4px;font-size:11.5px}
.anr-md p{margin:4px 0}.anr-md ul{margin:4px 0 4px 16px}
`;
if(!document.getElementById('anr-css')){const st=document.createElement('style');st.id='anr-css';st.textContent=CSS;document.head.appendChild(st);}

const esc=t=>String(t??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
const inline=t=>esc(t).replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*(.+?)\*\*/g,'<b>$1</b>')
  .replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g,'<a href="$2" target="_blank" rel="noopener">$1</a>');
const chip=t=>{const u=String(t||'').toUpperCase();const c=u.includes('YUKARI')?'up':u.includes('AŞAĞI')||u.includes('ASAGI')?'dn':u.includes('YATAY')?'flat':'mix';return `<span class="anr-chip ${c}">${esc(t)}</span>`};

function md(src){ // sade markdown (yedek)
  const lines=(src||'').split('\n');let out='',ul=false,tbl=[];
  const fl=()=>{if(ul){out+='</ul>';ul=false}};
  const ft=()=>{if(!tbl.length)return;const rows=tbl.filter(r=>!/^\|?\s*:?-{2,}/.test(r));out+='<table class="anr-tbl">'+rows.map((r,i)=>{const c=r.replace(/^\||\|$/g,'').split('|').map(x=>inline(x.trim()));const tg=i?'td':'th';return '<tr>'+c.map(x=>`<${tg}>${x}</${tg}>`).join('')+'</tr>'}).join('')+'</table>';tbl=[]};
  for(const l of lines){
    if(/^\s*\|/.test(l)){fl();tbl.push(l);continue}else ft();
    const li=l.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)/);if(li){if(!ul){out+='<ul class="anr-ul">';ul=true}out+=`<li>${inline(li[1])}</li>`;continue}
    fl();if(!l.trim())continue;out+=`<p>${inline(l.replace(/^>\s?/,''))}</p>`;
  }
  fl();ft();return `<div class="anr-md">${out}</div>`;
}

function sections(text){
  // "**🪙 ...**", "**📰 Haberler**" vb. kalın satır başlıklarına göre böl
  const out=[];let cur={key:'_',title:'',lines:[]};
  for(const raw of (text||'').replace(/`?PLAN_JSON:.*$/s,'').split('\n')){
    const m=raw.match(/^\s*\*\*\s*([^*]+?)\s*:?\*\*\s*:?\s*(.*)$/);
    const isHead=m&&/^(🪙|📰|📊|🧭|🎯|⚠️|⚠|📌|🔎)/.test(m[1].trim());
    const src=raw.match(/^\s*(Sources?|Kaynaklar)\s*:?\s*$/i);
    if(isHead||src){out.push(cur);const t=src?'Kaynaklar':m[1].trim();const key=src?'src':t.slice(0,2);cur={key,title:t,lines:[]};if(isHead&&m[2])cur.lines.push(m[2]);continue}
    cur.lines.push(raw);
  }
  out.push(cur);return out.filter(s=>s.lines.join('').trim()||s.title);
}

function news(lines){
  const items=lines.filter(l=>/^\s*[-*•]\s+/.test(l)).map(l=>l.replace(/^\s*[-*•]\s+/,''));
  if(!items.length)return md(lines.join('\n'));
  if(items.length===1&&/yok|bulunamadı/i.test(items[0]))return `<div style="color:var(--dim2)">◦ ${inline(items[0])}</div>`;
  const rest=lines.filter(l=>!/^\s*[-*•]\s+/.test(l)&&l.trim());
  const html=items.map(it=>{
    const imp=it.includes('➕')?'pos':it.includes('➖')?'neg':'';
    const ic=imp==='pos'?'➕':imp==='neg'?'➖':'◦';
    let parts=it.split(/\s+·\s+/).map(x=>x.replace(/[➕➖◦]/g,'').trim()).filter(Boolean);
    let date='',srcs='';
    if(parts.length>=3){date=parts.shift();srcs=parts.shift();}
    const ri=parts.findIndex(x=>/yansı/i.test(x));
    let refl='';
    if(ri>0){refl=parts.slice(ri).join(' · ');parts=parts.slice(0,ri);}
    const body=parts.join(' · ');
    const strip=t=>t.replace(/\*\*/g,'');
    return `<div class="anr-n ${imp}"><div class="ic">${ic}</div><div><div class="meta"><b>${esc(strip(date))}</b>${srcs?' · '+esc(strip(srcs)):''}</div><div>${inline(body)}</div>${refl?`<span class="refl">${inline(refl)}</span>`:''}</div></div>`;
  }).join('');
  return `<div class="anr-news">${html}</div>`+(rest.length?md(rest.join('\n')):'');
}

function teknik(lines){
  const tbl=lines.filter(l=>/^\s*\|/.test(l)&&!/^\|?\s*:?-{2,}/.test(l));
  const rest=lines.filter(l=>!/^\s*\|/.test(l));
  let t='';
  if(tbl.length){
    const rows=tbl.map(r=>r.replace(/^\||\|$/g,'').split('|').map(x=>x.trim()));
    t='<table class="anr-tbl"><thead><tr>'+rows[0].map(h=>`<th>${esc(h)}</th>`).join('')+'</tr></thead><tbody>'+
      rows.slice(1).map(r=>'<tr>'+r.map((c,i)=>{const h=(rows[0][i]||'').toLowerCase();
        if(h.includes('trend')||h.includes('supertrend'))return `<td>${chip(c.replace(/\*/g,'').split('(')[0].trim())}${c.includes('(')?' <span style="color:var(--dim2)">('+inline(c.split('(')[1]):''}</td>`;
        return `<td>${inline(c)}</td>`}).join('')+'</tr>').join('')+'</tbody></table>';
  }
  return t+md(rest.join('\n'));
}

function karar(lines){
  const txt=lines.join(' ').trim();
  const m=txt.match(/^(YUKARI|AŞAĞI|ASAGI|YATAY)[^—:-]*/i);
  const conf=(txt.match(/güven\s*:?\s*\**\s*(düşük|dusuk|orta|yüksek|yuksek)/i)||[])[1]||'';
  const n=/yük|yuk/i.test(conf)?3:/orta/i.test(conf)?2:conf?1:0;
  return `<div class="anr-dec">${m?chip(m[0].trim()):''}<span class="big">${inline(txt.replace(/^\s*(YUKARI|AŞAĞI|ASAGI|YATAY)[^—:-]*[—:-]?\s*/i,''))}</span>
    ${n?`<span class="anr-conf">güven <i class="${n>=1?'on':''}"></i><i class="${n>=2?'on':''}"></i><i class="${n>=3?'on':''}"></i> ${esc(conf)}</span>`:''}</div>`;
}

function ladder(plan,px){
  if(!plan)return'';
  const num=v=>{const n=parseFloat(v);return isFinite(n)&&typeof v!=='boolean'?n:null};
  const lv=[];const add=(name,v,cls)=>{const n=num(v);if(n!=null)lv.push({name,v:n,cls})};
  add('TP2',plan.tp2,'tp');add('TP1',plan.tp1,'tp');add('Giriş',plan.giris,'en');add('Stop',plan.stop,'sl');
  if(typeof plan.gecersiz==='number')add('Geçersiz',plan.gecersiz,'inv');
  if(px)lv.push({name:'Şu an',v:px,cls:'now'});
  if(lv.length<2)return'';
  lv.sort((a,b)=>b.v-a.v);
  const prec=Math.max(2,Math.min(8,(String(lv[0].v).split('.')[1]||'').length));
  const rows=lv.map(x=>`<div class="anr-lv ${x.cls}"><span class="n">${x.name}</span><span class="v">${x.v.toFixed(prec)}</span><span class="d">${px&&x.cls!=='now'?((x.v/px-1)*100).toFixed(2)+'%':''}</span></div>`).join('');
  const e=num(plan.giris),s=num(plan.stop),t1=num(plan.tp1),t2=num(plan.tp2);let rr='';
  if(e!=null&&s!=null&&Math.abs(e-s)>0){const r=Math.abs(e-s);const parts=[];if(t1!=null)parts.push(`TP1 <b>${(Math.abs(t1-e)/r).toFixed(1)}R</b>`);if(t2!=null)parts.push(`TP2 <b>${(Math.abs(t2-e)/r).toFixed(1)}R</b>`);parts.push(`stop ${(r/e*100).toFixed(2)}%`);rr=`<div class="anr-rr">${parts.join(' · ')}</div>`}
  return `<div><div class="anr-ladder">${rows}</div>${rr}</div>`;
}

function plan(lines,p,px){
  const lad=ladder(p,px);
  const kv=[];const rest=[];
  for(const l of lines){
    const m=l.match(/^\s*[-*•]\s+\**([^:*]{2,24})\**\s*:\s*(.*)$/);
    if(m&&/^(yön|yon|giriş|giris|stop|hedef|tp|geçersiz|gecersiz|risk|tetik|neden)/i.test(m[1].trim())){kv.push([m[1].trim(),m[2].replace(/^\s*\*\*\s*/,'').replace(/\s*\*\*\s*$/,'').replace(/(\S)\*\*\s/g,'$1 ').replace(/\s\*\*(\S)/g,' $1')]);}
    else rest.push(l);
  }
  const cls=k=>/stop/i.test(k)?'s':/hedef|tp/i.test(k)?'t':/geçersiz|gecersiz/i.test(k)?'i':/giriş|giris|yön|yon|tetik/i.test(k)?'y':'';
  const kvh=kv.length?`<div class="anr-kv">${kv.map(([k,v])=>`<span class="k ${cls(k)}">${esc(k)}</span><span>${inline(v)}</span>`).join('')}</div>`:'';
  const body=kvh+md(rest.join('\n'));
  return lad?`<div class="anr-plan">${lad}<div>${body}</div></div>`:body;
}

window.anRender=function(text,p,opts){
  opts=opts||{};const secs=sections(text);let html='';
  const head=secs.find(s=>s.key==='🪙');
  let px=opts.fiyat||null,coin=opts.coin||(p&&p.coin)||'',ts='';
  if(head){const t=head.title.replace(/^🪙\s*/,'');const parts=t.split(/\s+[—–-]+\s+/);coin=coin||parts[0]||'';const pm=t.match(/\$\s?([\d.,]+)/);if(pm&&!px)px=parseFloat(pm[1].replace(/,/g,''));ts=parts.slice(2).join(' · ')||(t.match(/\d{4}-\d{2}-\d{2}.*$/)||[''])[0];}
  const yon=p&&p.yon?String(p.yon).toUpperCase():'';
  html+=`<div class="anr-head"><span class="anr-coin">${esc(coin)}<small>USDT perp</small></span>${px?`<span class="anr-px">$${px}</span>`:''}${yon?`<span class="anr-badge ${yon}">${yon}</span>`:''}${p&&p.trend?chip(p.trend):''}<span class="anr-ts">${esc(ts)}</span></div>`;
  for(const s of secs){
    if(s.key==='🪙'||s.key==='_'&&!s.lines.join('').trim())continue;
    let body,cnt='';
    if(s.key==='📰'){body=news(s.lines);const n=s.lines.filter(l=>/^\s*[-*•]\s+/.test(l)).length;cnt=n?`${n} haber`:''}
    else if(s.key==='📊')body=teknik(s.lines);
    else if(s.key==='🧭')body=karar(s.lines);
    else if(s.key==='🎯')body=plan(s.lines,p,px);
    else if(s.key==='⚠️'||s.key==='⚠'){html+=`<div class="anr-note">⚠️ ${inline(s.lines.join(' '))}</div>`;continue}
    else if(s.key==='src'){html+=`<details><summary>Kaynaklar</summary>${md(s.lines.join('\n'))}</details>`;continue}
    else body=md(s.lines.join('\n'));
    html+=`<div class="anr-sec"><h4>${esc(s.title||'Not')}${cnt?`<span class="cnt">${cnt}</span>`:''}</h4><div class="anr-body">${body}</div></div>`;
  }
  return `<div class="anr">${html}</div>`;
};
})();
