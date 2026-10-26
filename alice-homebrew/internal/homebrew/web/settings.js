'use strict';
let sourceFilter='';
const settingsWords={
 'Настройки колонки':'Speaker settings','Память, плагины и источники.':'Storage, plugins and sources.',
 'Место на диске':'Storage','Оперативная память':'Memory','Доступно':'Available','Всего':'Total','Занято':'Used','Для новых плагинов':'For new plugins',
 'Данные недоступны':'Unavailable','Homebrew в памяти':'Homebrew memory','Память WASM':'WASM memory',
 'Источники плагинов':'Plugin sources','Добавить источник':'Add source','Официальный источник':'Official source','Сторонний источник':'Community source',
 'Подпись каталога проверена':'Catalog signature verified','Обновить':'Refresh','Удалить источник':'Remove source',
 'Удалить источник? Установленные из него плагины останутся.':'Remove this source? Its installed plugins will remain.',
 'Ссылка на источник':'Source URL','Ссылка на GitHub-репозиторий, сайт источника или catalog/v1.json. Ключ обычно определяется автоматически.':'Enter a GitHub repository, source website or catalog/v1.json URL. The public key is usually detected automatically.',
 'Название (необязательно)':'Name (optional)','Публичный ключ (необязательно)':'Public key (optional)','Проверить источник':'Check source',
 'Этот источник не относится к официальному Alice Homebrew. Его плагины проверяет владелец источника. Добавлять только если доверяете автору.':'This is not an official Alice Homebrew source. Its owner reviews its plugins. Add it only if you trust the author.',
 'Доверяю автору и хочу добавить этот сторонний источник':'I trust the author and want to add this community source','Источник добавлен':'Source added',
 'Плагинов в каталоге':'Catalog plugins','Ревизия':'Revision','Включён':'Enabled','Выключен':'Disabled',
 'Место, занятое плагинами':'Plugin storage','Размер включает установленный пакет и сохранённую версию для отката.':'Size includes the installed package and its saved rollback version.',
 'Точка отката':'Rollback version','Удалить плагин':'Remove plugin','Устройство':'Device','Архитектура':'Architecture','Версия Homebrew':'Homebrew version',
 'Работает на колонке':'Running on the speaker','Предпросмотр на компьютере':'Computer preview','Установлено плагинов':'Installed plugins','Запущено плагинов':'Running plugins',
 'Гайд разработчика':'Developer guide','Создание плагинов и своих источников':'Create plugins and sources',
 'Недостаточно места':'Not enough storage','Нужно освободить':'Space to free','Размер пакета':'Package size','Нужно с запасом':'Required including reserve',
 'Для работы системы оставляем 8 МиБ свободного места.':'We keep 8 MiB available for system operation.',
 'Выберите плагины для удаления. Сначала показаны самые большие. Их настройки также будут удалены.':'Choose plugins to remove, largest first. Their configurations will also be removed.',
 'Выбрано для удаления':'Selected to free','Удалить выбранные и продолжить':'Remove selected and continue','Плагины не выбраны':'No plugins selected',
 'Установленных плагинов нет. Освободите место на колонке вручную и повторите проверку.':'There are no installed plugins. Free some storage on the speaker and check again.',
 'Проверить место снова':'Check storage again','Освободить место':'Free up storage','Места достаточно':'Enough storage','Места от удаления этих плагинов может не хватить.':'Removing these plugins may not free enough storage.',
 'Настройки сохраняются на колонке и остаются после перезагрузки.':'Settings are saved on the speaker and survive restarts.',
 'Сохранить настройки':'Save settings','Скачать конфиг':'Download config','Все источники':'All sources','Источники ещё не загружены. Нажмите «Обновить каталог».':'Sources have not loaded yet. Press Refresh catalog.',
 'Некоторые источники недоступны. Подробности — в настройках.':'Some sources are unavailable. See Settings for details.',
 'Нет плагинов для выбранного источника':'No plugins in this source','Проверено владельцем источника':'Reviewed by source owner',
 'Вход в менеджер не требуется.':'No sign-in is required for this manager.','Плагин не выбран':'No plugin selected','Точное значение':'Exact value'
};
function formatBytes(value){if(!Number.isFinite(Number(value)))return '—';let n=Number(value);const units=lang==='ru'?['Б','КиБ','МиБ','ГиБ']:['B','KiB','MiB','GiB'];let i=0;while(n>=1024&&i<3){n/=1024;i++}return `${n.toLocaleString(lang==='ru'?'ru-RU':'en-US',{maximumFractionDigits:i?1:0})} ${units[i]}`}
function metric(name,value){const e=el('strong','',value);e.dataset.live=name;return e}
function sourceBadge(official,reviewed=true){return el('span','badge'+(!official?' local':''),t(official?'Официальный источник':reviewed?'Сторонний источник':'Локальный пакет'))}
function openConfirm(){if(!$('confirm').open)$('confirm').showModal()}
function systemSummary(){
 const grid=el('section','system-grid');
 for(const [kind,title]of [['disk','Место на диске'],['memory','Оперативная память']]){
  const card=el('article','stat-card');card.append(el('span','stat-label',t(title)),metric(kind+'-free','—'));
  const progress=el('progress','usage-meter');progress.max=100;progress.value=0;progress.dataset.meter=kind;card.append(progress);
  const row=el('div','stat-meta');row.append(el('span','',t('Доступно')),metric(kind+'-total','—'));card.append(row);
  if(kind==='disk')card.append(el('div','stat-meta',t('Для новых плагинов')),metric('plugin-free','—'));
  else card.append(el('div','stat-meta',t('Homebrew в памяти')),metric('process-memory','—'));
  grid.append(card);
 }
 return grid;
}
function updateSystemMetrics(){
 const system=state.system||{},disk=system.disk||{},memory=system.memory||{};
 const values={'disk-free':disk.available?formatBytes(disk.available_bytes):t('Данные недоступны'),'disk-total':disk.available?`${t('Всего')}: ${formatBytes(disk.total_bytes)}`:'—',
  'memory-free':memory.available?formatBytes(memory.available_bytes):t('Данные недоступны'),'memory-total':memory.available?`${t('Всего')}: ${formatBytes(memory.total_bytes)}`:'—',
  'plugin-free':disk.available?formatBytes(disk.plugin_available_bytes):'—','process-memory':formatBytes(memory.process_bytes||memory.go_heap_bytes)};
 for(const node of document.querySelectorAll('[data-live]'))node.textContent=values[node.dataset.live]??'—';
 for(const node of document.querySelectorAll('[data-meter]')){const info=node.dataset.meter==='disk'?disk:memory;node.value=info.total_bytes?100*info.used_bytes/info.total_bytes:0}
}
function settingsPage(c){
 c.append(head('Настройки колонки','Память, плагины и источники.'),systemSummary());
 managerUpdates(c);
 const heading=el('div','section-head');heading.append(el('h2','',t('Источники плагинов')),button('Добавить источник',()=>sourceDialog(),'primary'));c.append(heading);
 const sources=el('div','source-list');
 for(const source of state.sources||[]){
  const card=el('article','source-card'),top=el('div','source-top'),name=el('div');name.append(el('h3','',source.name),sourceBadge(source.official));top.append(name,el('span','metadata',t(source.enabled?'Включён':'Выключен')));card.append(top,el('div','source-url',source.url));
  const count=state.catalog?.entries?.filter(p=>p.source_id===source.id).length||0;
  card.append(el('p','metadata',`${t('Плагинов в каталоге')}: ${count} · ${t('Ревизия')}: ${source.revision}`));
  if(source.error)card.append(el('div','info warn',source.error));
  const details=el('details','source-details');details.append(el('summary','',t('Публичный ключ каталога')),el('div','hash',source.public_key));card.append(details);
  const row=el('div','button-row');const refresh=button('Обновить',async()=>{try{await api(`/api/sources/${source.id}/refresh`,{})}finally{await load()}},'secondary');refresh.disabled=!source.enabled;
  row.append(refresh,button(source.enabled?'Выключить':'Включить',async()=>{await api(`/api/sources/${source.id}/enabled`,{enabled:!source.enabled});await load()},'quiet'),button('Удалить источник',async()=>{if(confirm(t('Удалить источник? Установленные из него плагины останутся.'))){await api(`/api/sources/${source.id}/remove`,{});await load()}},'quiet danger'));card.append(row);sources.append(card);
 }
 c.append(sources);
 const storageHead=el('div','section-head spaced');storageHead.append(el('h2','',t('Место, занятое плагинами')));c.append(storageHead,el('p','caption',t('Размер включает установленный пакет и сохранённую версию для отката.')));
 const list=el('div','settings-box storage-list');
 const plugins=[...(state.plugins||[])].sort((a,b)=>(b.storage_bytes||0)-(a.storage_bytes||0));
 if(!plugins.length)list.append(el('p','',t('Пока без плагинов')));
 for(const p of plugins){const row=el('div','storage-row'),info=el('div','storage-info');info.append(el('strong','',text(p.manifest.title)),el('div','metadata',`${formatBytes(p.storage_bytes||0)}${p.previous_bytes?' · '+t('Точка отката')+': '+formatBytes(p.previous_bytes):''}`));row.append(info,button('Настроить',()=>{route='plugins';selected=p.manifest.id;render()},'quiet'),button('Удалить плагин',async()=>{if(confirm(`${text(p.manifest.title)}: ${t('Удалить плагин и его настройки?')}`)){await api(`/api/plugins/${p.manifest.id}/remove`,{});await load()}},'quiet danger'));list.append(row)}
 c.append(list);
 const system=state.system||{},device=el('section','settings-box spaced');device.append(el('h2','',t('Устройство')));
 for(const [label,value]of [['Версия Homebrew',system.version||about.version],['Архитектура',`${system.os||''} / ${system.architecture||''}`],['Установлено плагинов',`${system.plugin_count||0} / ${system.max_plugins||8}`],['Запущено плагинов',`${system.running_count||0} / ${system.max_running||4}`]]){const row=el('div','device-row');row.append(el('span','',t(label)),el('strong','',String(value)));device.append(row)}
 device.append(el('p','caption',t(system.hardware_led?'Работает на колонке':'Предпросмотр на компьютере')),el('p','caption',t('Вход в менеджер не требуется.')));
 const guide=el('a','secondary guide-link',t('Гайд разработчика'));guide.href='https://github.com/hertebeznat-cell/alice-homebrew/blob/main/docs/PLUGIN_GUIDE.md';guide.target='_blank';guide.rel='noopener';device.append(guide);device.append(button(lang==='ru'?'Оборудование колонки':'Speaker hardware',async()=>{const hw=await api('/api/hardware');const d=$('confirm-content');d.replaceChildren(el('h2','',lang==='ru'?'Оборудование колонки':'Speaker hardware'),el('pre','',JSON.stringify(hw,null,2)),button('Отмена',()=>$('confirm').close(),'quiet'));openConfirm()},'secondary'));c.append(device);updateSystemMetrics();
}
function sourceDialog(){
 const d=$('confirm-content');d.replaceChildren(el('h2','',t('Добавить источник')),el('p','',t('Ссылка на GitHub-репозиторий, сайт источника или catalog/v1.json. Ключ обычно определяется автоматически.')));
 const form=el('form'),url=el('input'),name=el('input'),key=el('input');url.type='url';url.required=true;url.placeholder='https://github.com/author/plugin-source';name.maxLength=80;key.autocomplete='off';
 for(const [label,input]of [['Ссылка на источник',url],['Название (необязательно)',name]]){input.setAttribute('aria-label',t(label));const f=el('div','field');f.append(el('label','',t(label)),input);form.append(f)}
 key.setAttribute('aria-label',t('Публичный ключ (необязательно)'));
 const advanced=el('details','field');advanced.append(el('summary','',t('Публичный ключ (необязательно)')),key);form.append(advanced);
 const check=el('button','primary',t('Проверить источник'));check.type='submit';form.append(check);
 form.onsubmit=e=>{e.preventDefault();action(async()=>{const preview=await api('/api/sources/inspect',{url:url.value.trim(),name:name.value.trim(),public_key:key.value.trim()});sourceConfirmation(preview)},check)};
 d.append(form,button('Отмена',()=>$('confirm').close(),'quiet'));openConfirm();
}
function sourceConfirmation(preview){
 const s=preview.source,d=$('confirm-content');d.replaceChildren(sourceBadge(s.official),el('h2','',s.name),el('p','source-url',s.url),el('div','info',`${t('Подпись каталога проверена')} · ${t('Плагинов в каталоге')}: ${preview.plugin_count}`));
 let trust;
 if(!s.official){d.append(el('div','info warn',t('Этот источник не относится к официальному Alice Homebrew. Его плагины проверяет владелец источника. Добавлять только если доверяете автору.')));const label=el('label','permission');trust=el('input');trust.type='checkbox';label.append(trust,document.createTextNode(t('Доверяю автору и хочу добавить этот сторонний источник')));d.append(label)}
 d.append(el('div','hash',s.public_key));
 const add=button('Добавить источник',async()=>{await api('/api/sources',{url:s.url,public_key:s.public_key,name:s.name,acknowledge_unofficial:!!trust?.checked});$('confirm').close();await load();toast(t('Источник добавлен'))},'primary');add.disabled=!!trust;if(trust)trust.onchange=()=>add.disabled=!trust.checked;d.append(add,button('Отмена',()=>$('confirm').close(),'quiet'));openConfirm();
}
function storageDialog(plan,staged){
 const d=$('confirm-content');d.replaceChildren(el('h2','',t('Недостаточно места')),el('p','',`${t('Нужно освободить')}: ${formatBytes(plan.missing_bytes)}`),el('div','info',`${t('Размер пакета')}: ${formatBytes(plan.package_bytes)} · ${t('Доступно')}: ${formatBytes(plan.available_bytes)}. ${t('Для работы системы оставляем 8 МиБ свободного места.')}`));
 d.append(el('p','',t('Выберите плагины для удаления. Сначала показаны самые большие. Их настройки также будут удалены.')));
 const choices=[];
 for(const p of plan.candidates||[]){const label=el('label','permission cleanup-option'),check=el('input');check.type='checkbox';const info=el('span');info.append(el('strong','',text(p.title)),el('span','metadata',formatBytes(p.bytes)));label.append(check,info);d.append(label);choices.push({check,plugin:p})}
 if(!choices.length)d.append(el('div','info warn',t('Установленных плагинов нет. Освободите место на колонке вручную и повторите проверку.')));
 const selectedBytes=el('p','caption'),remove=button('Удалить выбранные и продолжить',async()=>{const selected=choices.filter(v=>v.check.checked);if(!selected.length)throw Error(t('Плагины не выбраны'));for(const item of selected)await api(`/api/plugins/${item.plugin.id}/remove`,{});await load();await retry()},'primary');
 const sync=()=>{const selected=choices.filter(v=>v.check.checked),bytes=selected.reduce((n,v)=>n+v.plugin.bytes,0);remove.disabled=!selected.length;selectedBytes.textContent=`${t('Выбрано для удаления')}: ${formatBytes(bytes)}${bytes<plan.missing_bytes?' · '+t('Места от удаления этих плагинов может не хватить.'):''}`};choices.forEach(v=>v.check.onchange=sync);sync();
 const retry=async()=>{const fresh=await api('/api/storage/plan',{ticket:staged.ticket});if(fresh.fits){const next={...staged,storage:fresh};if(next.update&&next.fast_update)await finishPluginUpdate(next);else showConfirmation(next)}else storageDialog(fresh,staged)};
 d.append(selectedBytes,remove,button('Проверить место снова',retry,'secondary'),button('Отмена',()=>$('confirm').close(),'quiet'));openConfirm();
}
function catalogPage(c){
 const refresh=button('Обновить каталог',async()=>{try{await api('/api/catalog/refresh',{})}finally{await load()}},'secondary');c.append(head('Магазин','Плагины из проверенного каталога.',refresh));
 if((state.sources||[]).some(s=>s.enabled&&s.error))c.append(el('div','info warn',t('Некоторые источники недоступны. Подробности — в настройках.')));
 const controls=el('div','catalog-controls'),filter=el('select');filter.setAttribute('aria-label',t('Источники плагинов'));const all=el('option','',t('Все источники'));all.value='';filter.append(all);
 for(const s of state.sources||[]){if(!s.enabled)continue;const option=el('option','',s.name+(s.official?' · '+t('Официальный источник'):''));option.value=s.id;filter.append(option)}filter.value=sourceFilter;filter.onchange=()=>{sourceFilter=filter.value;render()};controls.append(filter,button('Добавить источник',sourceDialog,'quiet'));c.append(controls);
 if(sourceFilter&&!(state.sources||[]).some(s=>s.id===sourceFilter&&s.enabled)){sourceFilter='';filter.value=''}
 const entries=(state.catalog?.entries||[]).filter(p=>!sourceFilter||p.source_id===sourceFilter);
 if(!entries.length){c.append(el('div','empty',t(sourceFilter?'Нет плагинов для выбранного источника':'Источники ещё не загружены. Нажмите «Обновить каталог».')));return}
 const cards=el('div','cards');entries.forEach(p=>cards.append(pluginCard(p,true)));c.append(cards);
}
function detailPage(c){
 const p=state.plugins?.find(p=>p.manifest.id===selected);if(!p){selected='';pluginsPage(c);return}
 const headerActions=el('div','button-row');if(p.update)headerActions.append(pluginUpdateButton(p));headerActions.append(button(p.enabled?'Выключить':'Включить',async()=>{await togglePlugin(p)},p.enabled?'secondary':'primary'));c.append(button('Назад к плагинам',()=>{selected='';render()},'quiet back'),head(text(p.manifest.title),`${p.manifest.author} · v${p.manifest.version}`,headerActions));if(p.update)c.append(el('div','info',t('Доступна версия')+' '+p.update.manifest.version));
 if(p.last_error)c.append(el('div','info warn',p.last_error));
 const grid=el('div','detail-grid'),form=el('form','settings-box'),values={...p.config};
 form.append(el('p','caption',t('Настройки сохраняются на колонке и остаются после перезагрузки.')));
 for(const f of p.manifest.config){
  const field=el('div','field'),id=`config-${f.key}`,label=el('label','',text(f.label));label.htmlFor=id;field.append(label);const row=el('div','field-row');let input;
  if(f.type==='range'){
   input=el('input');input.type='range';input.id=id;input.min=f.min??0;input.max=f.max??0;input.step=f.step;input.value=values[f.key];const exact=el('input','exact-number');exact.type='number';exact.min=input.min;exact.max=input.max;exact.step=input.step;exact.value=values[f.key];exact.required=true;exact.setAttribute('aria-label',text(f.label)+' · '+t('Точное значение'));
   input.oninput=()=>{exact.value=input.value;values[f.key]=Number(input.value)};exact.oninput=()=>{values[f.key]=exact.valueAsNumber;if(exact.checkValidity())input.value=exact.value};row.append(input,exact);
  }else if(f.type==='color'){
   input=el('input');input.type='color';input.id=id;input.value=values[f.key];const exact=el('input','exact-color');exact.type='text';exact.pattern='#[0-9a-fA-F]{6}';exact.maxLength=7;exact.value=values[f.key];exact.required=true;exact.setAttribute('aria-label',text(f.label)+' HEX');input.oninput=()=>{exact.value=input.value;values[f.key]=input.value};exact.oninput=()=>{values[f.key]=exact.value;if(exact.checkValidity())input.value=exact.value};row.append(input,exact);
  }else{
   input=el(f.type==='select'?'select':'input');input.id=id;
   if(f.type==='select'){for(const o of f.options){const option=el('option','',text(o.label));option.value=o.value;input.append(option)}input.value=values[f.key]}
   else if(f.type==='boolean'){input.type='checkbox';input.checked=values[f.key]}
   else{input.type='text';input.maxLength=f.max_length;input.value=values[f.key]}
   input.oninput=()=>values[f.key]=f.type==='boolean'?input.checked:input.value;row.append(input);
  }
  field.append(row);form.append(field);
 }
 const apply=el('button','primary',t('Сохранить настройки'));apply.type='submit';form.append(apply);form.onsubmit=e=>{e.preventDefault();action(async()=>{await api(`/api/plugins/${p.manifest.id}/config`,{values});await load();toast(t('Настройки сохранены'))},apply)};
 form.append(el('div','split-line'));const controls=el('div','button-row');
 controls.append(button('Скачать конфиг',()=>{const blob=new Blob([JSON.stringify({plugin_id:p.manifest.id,version:p.manifest.version,values:p.config},null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=el('a');a.href=url;a.download=p.manifest.id+'.config.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)},'secondary'));
 if(p.previous)controls.append(button('Вернуть предыдущую версию',async()=>{if(confirm(t('Вернуть предыдущую версию и её настройки?'))){await api(`/api/plugins/${p.manifest.id}/rollback`,{permissions:p.previous.manifest.permissions});await load()}},'quiet'));
 controls.append(button('Удалить',async()=>{if(confirm(t('Удалить плагин и его настройки?'))){await api(`/api/plugins/${p.manifest.id}/remove`,{});selected='';await load()}},'quiet danger'));form.append(controls);
 form.append(el('div','metadata',`${t('Место на диске')}: ${formatBytes(p.storage_bytes||0)} · ${t('Память WASM')}: ${formatBytes(p.wasm_memory_bytes||0)}`));
 if(p.logs?.length)form.append(el('h3','',t('Журнал')),el('pre','',p.logs.join('\n')));
 grid.append(form,pluginPanel(p));c.append(grid);updateRings();updatePluginPanel();
}

function updateWords(ru,en){return lang==='ru'?ru:en}
function managerUpdates(c){
 if(!state.updates)return;
 const box=el('section','settings-box spaced');box.append(el('h2','',updateWords('Обновления менеджера','Manager updates')));
 box.append(el('p','caption',updateWords('Проверяем стабильные релизы на GitHub раз в 5 часов. Плагины и настройки сохраняются.','Stable GitHub releases are checked every 5 hours. Plugins and settings are preserved.')));
 const status=el('p','info');status.id='manager-update-status';box.append(status);
 const times=el('p','caption');times.id='manager-update-times';box.append(times);
 const row=el('div','button-row'),label=el('label','permission'),enabled=el('input');enabled.type='checkbox';enabled.id='manager-update-enabled';enabled.checked=!!state.updates.enabled;
 enabled.onchange=()=>action(async()=>{try{state.updates=await api('/api/updates/settings',{enabled:enabled.checked})}finally{updateManagerUpdates()}});
 label.append(enabled,document.createTextNode(updateWords('Автоматически устанавливать обновления','Automatically install updates')));
 const check=button(updateWords('Проверить сейчас','Check now'),async()=>{await api('/api/updates/check',{});toast(updateWords('Проверка обновлений запущена','Update check started'));state.updates=await api('/api/updates');updateManagerUpdates()},'secondary');check.id='manager-update-check';
 row.append(label,check);box.append(row);
 const repo=el('a','caption',updateWords('Релизы на GitHub ↗','GitHub releases ↗'));repo.href='https://github.com/hertebeznat-cell/alice-homebrew/releases';repo.target='_blank';repo.rel='noopener';box.append(repo);c.append(box);updateManagerUpdates();
}
function updateManagerUpdates(){
 const u=state.updates,status=$('manager-update-status'),times=$('manager-update-times');if(!u||!status)return;
 const states={idle:['Ожидает проверки','Waiting to check'],checking:['Проверяем GitHub…','Checking GitHub…'],downloading:['Скачиваем обновление…','Downloading update…'],restarting:['Обновляем и перезапускаем менеджер…','Installing and restarting…'],'up-to-date':['Установлена актуальная версия','You are up to date'],updated:['Менеджер обновлён','Manager updated'],available:['Доступна новая версия','An update is available'],error:['Не удалось обновить','Update failed'],'rolled-back':['Восстановлена предыдущая версия','Previous version restored']};
 const words=states[u.state]||states.idle;status.textContent=updateWords(words[0],words[1])+' · '+u.current_version+(isNewerManagerVersion(u.latest_version,u.current_version)?' → '+u.latest_version:'')+(u.error?' · '+u.error:'');
 if(!u.supported)status.textContent+=' · '+updateWords('Автоустановка работает в службе Linux на колонке.','Automatic installation is available in the managed Linux speaker service.');
 status.className='info'+(['error','rolled-back'].includes(u.state)?' warn':'');
 const date=value=>value&&!value.startsWith('0001-')?new Date(value).toLocaleString(lang==='ru'?'ru-RU':'en-US'):'—';
 times.textContent=updateWords('Последняя проверка: ','Last check: ')+date(u.last_check)+' · '+updateWords('Следующая: ','Next: ')+date(u.next_check);
 const busy=['checking','downloading','restarting'].includes(u.state),enabled=$('manager-update-enabled'),check=$('manager-update-check');if(enabled){enabled.checked=!!u.enabled;enabled.disabled=busy||!u.supported}if(check)check.disabled=busy;
}

function isNewerManagerVersion(candidate,current){if(!candidate)return false;const a=candidate.split('.').map(Number),b=current.split('.').map(Number);for(let i=0;i<3;i++){if(a[i]!==b[i])return a[i]>b[i]}return false}

function pluginPanel(p){
 const m=p.manifest,ring=m.permissions.includes('led.ring'),audio=m.capabilities?.includes('audio.playback'),panel=el('aside','preview-panel plugin-panel');panel.dataset.pluginPanel=m.id;
 const words=(ru,en)=>lang==='ru'?ru:en;
 if(ring){const speaker=makeSpeaker();speaker.querySelector('.ring').dataset.plugin=m.id;panel.append(speaker,el('h3','',words('Кольцо подсветки','LED ring')),el('p','caption',words('Живой кадр выбранного плагина. Выключенный плагин не показывает чужую подсветку.','Live frame for this plugin. Disabled plugins do not display another plugin’s LEDs.')))}
 else if(audio){panel.append(el('div','audio-glyph','♫'),el('h3','',m.id==='airplay'?words('Приёмник AirPlay','AirPlay receiver'):words('Воспроизведение звука','Audio playback')),el('strong','receiver-name',String(p.config.name||text(m.title))));if(m.id==='airplay'){panel.append(el('p','caption',words('На iPhone: Пункт управления → музыка → AirPlay.','On iPhone: Control Center → music → AirPlay.')));for(const [label,value]of [[words('Выход','Output'),p.config.device],[words('Порт','Port'),p.config.port],[words('Защита','Protection'),p.config.password?words('Пароль включён','Password enabled'):words('Без пароля','No password')]]){const row=el('div','panel-value');row.append(el('span','',label),el('strong','',String(value??'—')));panel.append(row)}}}
 else{panel.append(el('div','audio-glyph',m.capabilities?.includes('audio.capture')?'◉':'⌘'),el('h3','',text(m.title)),el('p','caption',text(m.description)));for(const cap of m.capabilities||[])panel.append(el('span','badge capability',cap))}
 const status=el('div','badge panel-status');status.dataset.panelStatus=m.id;panel.append(status);
 if(m.id==='music-ring'||m.id==='airplay'){panel.dataset.audioPanel=m.id;const bars=el('div','audio-spectrum');bars.setAttribute('aria-label',words('Уровни музыки','Music levels'));for(let i=0;i<12;i++)bars.append(el('i'));panel.append(bars);const note=el('p','caption');note.dataset.audioNote=m.id;panel.append(note)}
 return panel;
}
function updatePluginPanel(){
 const panel=document.querySelector('[data-plugin-panel]');if(!panel)return;const id=panel.dataset.pluginPanel,p=state.plugins?.find(p=>p.manifest.id===id);if(!p)return;const words=(ru,en)=>lang==='ru'?ru:en;
 const status=panel.querySelector('[data-panel-status]');status.textContent=p.last_error||words(p.enabled?'Плагин запущен':'Плагин выключен',p.enabled?'Plugin running':'Plugin disabled');status.className='badge panel-status'+(p.last_error?' error':'');
 const data=state.audio,audio=!!p.enabled&&data?.available&&data.source==='airplay';const bands=panel.querySelectorAll('.audio-spectrum i');bands.forEach((bar,i)=>{const level=audio?Math.min(1,Math.sqrt((data.bands?.[i]||0)*12)):0;bar.style.height=`${5+level*95}%`;bar.classList.toggle('active',audio&&data.active)});
 const note=panel.querySelector('[data-audio-note]');if(!note)return;
 note.textContent=!p.enabled?words('Включите плагин, чтобы начать.','Enable this plugin to begin.'):data?.error?data.error:audio?words(data.active?'Музыка поступает из AirPlay':'Поток AirPlay: тишина',data.active?'Receiving AirPlay audio':'AirPlay stream: silence'):id==='music-ring'?words('Ожидаем музыку. Включите AirPlay версии beta.2 или новее и запустите воспроизведение с iPhone.','Waiting for music. Enable AirPlay beta.2 or newer and start playback on your iPhone.'):words('Выберите колонку на iPhone. Живой спектр доступен с AirPlay beta.2.','Select the speaker on your iPhone. Live spectrum requires AirPlay beta.2.');
}
