'use strict';
const $=id=>document.getElementById(id);
const el=(tag,cls,txt)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(txt!==undefined)e.textContent=txt;return e};
const bytes=s=>Uint8Array.from(atob(s),c=>c.charCodeAt(0));
async function start(){
 const [site,keyText]=await Promise.all([fetch('./site.json').then(r=>r.json()),fetch('./public-key.txt').then(r=>r.text())]);
 const catalogURL=new URL(site.catalog,location.href),key=keyText.trim();
 if(catalogURL.origin!==location.origin)throw Error('Каталог должен находиться на этом сайте.');
 const envelope=await fetch(catalogURL).then(r=>{if(!r.ok)throw Error('Не удалось загрузить каталог');return r.json()});
 const data=bytes(envelope.payload),signature=bytes(envelope.signature);
 const publicKey=await crypto.subtle.importKey('raw',bytes(key),{name:'Ed25519'},false,['verify']);
 if(!await crypto.subtle.verify({name:'Ed25519'},publicKey,signature,data))throw Error('Подпись каталога не совпала с ключом модератора.');
 const catalog=JSON.parse(new TextDecoder().decode(data));if(catalog.format!==1||Date.parse(catalog.expires_at)<=Date.now())throw Error('Срок каталога истёк — модератору нужно обновить экспорт.');
 $('signature').textContent='Подпись подтверждена';$('catalog-meta').textContent=`Плагинов: ${catalog.entries.length} · ревизия ${catalog.revision}`;
 $('catalog-url').textContent=catalogURL.href;$('public-key').textContent=key;
 if(site.repository){const u=new URL(site.repository);if(u.protocol==='https:'&&u.hostname==='github.com'){$('submit-link').href=site.repository.replace(/\/$/,'')+'/issues/new?template=plugin.yml';$('submit-link').classList.remove('hidden')}}
 if(!catalog.entries.length)$('catalog').append(el('div','empty','Пока нет одобренных версий. Первый плагин появится после проверки модератором.'));
 for(const p of catalog.entries){
  const m=p.manifest,card=el('article','card'),top=el('div','card-top');top.append(el('span','plugin-icon',m.permissions.includes('led.ring')?'◉':'⌘'),el('span','badge','Проверено модератором'));card.append(top,el('h3','',m.title.ru||m.title.en),el('p','',m.description.ru||m.description.en));
  const url=new URL(p.download,catalogURL);if(url.origin!==location.origin||!/^https?:$/.test(url.protocol))throw Error('Недопустимый адрес пакета.');
  if(m.engine==='native')card.append(el('div','info warn','Homebrew 0.3+ · нативный плагин · полный доступ к системе'));
  const footer=el('div','card-footer'),a=el('a','secondary',`Скачать .aliceplugin ↓`);a.href=url.href;a.download=`${m.id}-${m.version}.aliceplugin`;footer.append(el('span','metadata',`${m.author} · v${m.version}`),a);card.append(footer);$('catalog').append(card);
 }
}
start().catch(e=>{$('signature').textContent='Каталог не подтверждён';$('signature').classList.add('error');$('error').textContent=e.message;$('catalog').replaceChildren()});
