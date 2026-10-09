/* SVGTracker product modules v28: schedule + notes.
   This file intentionally owns these domains so the legacy script.js can be
   reduced gradually without changing existing training/finance behavior. */
window.SVGTRACKER_PRODUCT_VERSION = 28;
let productScheduleDefinitions = [];
let productScheduleLoaded = false;
let productNotesLoaded = false;
let productSyncInFlight = false;
let scheduleWeekCursor = null;
let scheduleSelectedDate = new Date();
let scheduleEditingId = null;
let noteEditingId = null;

function productId(prefix) {
  try { if (typeof createEntityId === 'function') return createEntityId(prefix); } catch (_) {}
  return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
}
function productDateKey(date) {
  try { if (typeof getDateKey === 'function') return getDateKey(date); } catch (_) {}
  return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
}
function productLocalDateInput(date) {
  return productDateKey(date);
}
function productLocalTimeInput(date) {
  return `${String(date.getHours()).padStart(2,'0')}:${String(date.getMinutes()).padStart(2,'0')}`;
}
function productFormatDate(date, opts={}) {
  return new Intl.DateTimeFormat('ru-RU', opts).format(date);
}
function productEscapeText(value) { return String(value ?? ''); }
function productSetSyncLabel(id, text) { const el=document.getElementById(id); if(el) el.textContent=text; }
function normalizeProductNote(note) {
  if (!note || typeof note !== 'object') return null;
  return {...note, createdAt:note.createdAt||note.created_at||null, updatedAt:note.updatedAt||note.updated_at||null, reminderAt:note.reminderAt||note.reminder_at||null};
}
function productHeaders(json=false) {
  try { return telegramApiHeaders(json); } catch (_) { return json ? {'Content-Type':'application/json'} : {}; }
}
function productHasAuth() { try { return hasServerAuth(); } catch (_) { return false; } }
function productToast(text) { try { showToast(text); } catch (_) { console.log(text); } }
function productRenderDashboard() {
  try { renderHomeTrainingSummary(); } catch (_) {}
}
function productCloseOtherScreens() {
  for (const id of ['training-screen','finance-screen','schedule-screen','notes-screen']) {
    const el=document.getElementById(id); if(el) el.hidden=true;
  }
}
function productLockBody(locked) { document.body.classList.toggle('training-open', Boolean(locked)); }

function productMonthAdd(date) {
  const next=new Date(date); const day=next.getDate(); next.setDate(1); next.setMonth(next.getMonth()+1);
  const last=new Date(next.getFullYear(),next.getMonth()+1,0).getDate(); next.setDate(Math.min(day,last)); return next;
}
function expandScheduleDefinitions(definitions, rangeStart=null, rangeEnd=null) {
  const startRange=rangeStart || new Date(Date.now()-45*86400000);
  const endRange=rangeEnd || new Date(Date.now()+240*86400000);
  const out=[];
  for (const event of definitions || []) {
    const base=new Date(event.starts_at || event.startAt || event.start || '');
    if(Number.isNaN(base.getTime())) continue;
    const baseEnd=event.ends_at ? new Date(event.ends_at) : null;
    const duration=baseEnd && !Number.isNaN(baseEnd.getTime()) ? Math.max(0,baseEnd-base) : 0;
    const recurrence=['daily','weekly','monthly'].includes(event.recurrence)?event.recurrence:'none';
    const until=event.recurrence_until?new Date(event.recurrence_until):null;
    let current=new Date(base);
    if(recurrence!=='none' && current<startRange){
      if(recurrence==='daily'||recurrence==='weekly'){
        const period=(recurrence==='daily'?1:7)*86400000;
        const steps=Math.max(0,Math.floor((startRange-current)/period));
        current=new Date(current.getTime()+steps*period);
        while(current<startRange) current=new Date(current.getTime()+period);
      }else{
        for(let i=0;i<2400&&current<startRange;i++) current=productMonthAdd(current);
      }
    }
    for(let count=0;count<1000;count+=1){
      if(until && !Number.isNaN(until.getTime()) && current>until) break;
      if(current>endRange) break;
      if(current>=startRange){
        const end=duration?new Date(current.getTime()+duration):null;
        out.push({...event, occurrenceId:`${event.id}__${current.toISOString()}`, startAt:current.toISOString(), endAt:end?end.toISOString():null, date:productDateKey(current), time:productLocalTimeInput(current), isOccurrence:recurrence!=='none'});
      }
      if(recurrence==='none') break;
      if(recurrence==='daily') current=new Date(current.getTime()+86400000);
      else if(recurrence==='weekly') current=new Date(current.getTime()+7*86400000);
      else current=productMonthAdd(current);
    }
  }
  return out.sort((a,b)=>new Date(a.startAt)-new Date(b.startAt));
}

function applyProductCaches() {
  scheduleData = expandScheduleDefinitions(productScheduleDefinitions);
  notesData = Array.isArray(notesData) ? notesData : [];
  try { localStorage.setItem(STORAGE.schedule, JSON.stringify(productScheduleDefinitions)); } catch (_) {}
  try { localStorage.setItem(STORAGE.notes, JSON.stringify(notesData)); } catch (_) {}
  productRenderDashboard();
}

async function syncScheduleNotesWithServer(force=false) {
  if (!productHasAuth() || productSyncInFlight) {
    if(!productHasAuth()) {
      if(!productScheduleLoaded){ const local=readJSON(STORAGE.schedule,[]); productScheduleDefinitions=Array.isArray(local)?local:[]; productScheduleLoaded=true; }
      if(!productNotesLoaded){ const local=readJSON(STORAGE.notes,[]); notesData=Array.isArray(local)?local.map(normalizeProductNote).filter(Boolean):[]; productNotesLoaded=true; }
      applyProductCaches();
    }
    return false;
  }
  productSyncInFlight=true;
  productSetSyncLabel('schedule-sync-label','Синхронизация…'); productSetSyncLabel('notes-sync-label','Синхронизация…');
  try{
    const [scheduleResponse,notesResponse]=await Promise.all([
      fetch('/api/schedule/events',{headers:productHeaders(false),cache:'no-store'}),
      fetch('/api/notes',{headers:productHeaders(false),cache:'no-store'})
    ]);
    if(!scheduleResponse.ok) throw new Error(`schedule HTTP ${scheduleResponse.status}`);
    if(!notesResponse.ok) throw new Error(`notes HTTP ${notesResponse.status}`);
    const schedulePayload=await scheduleResponse.json(); const notesPayload=await notesResponse.json();
    productScheduleDefinitions=Array.isArray(schedulePayload.events)?schedulePayload.events:[];
    notesData=Array.isArray(notesPayload.notes)?notesPayload.notes.map(normalizeProductNote).filter(Boolean):[];
    productScheduleLoaded=true; productNotesLoaded=true; applyProductCaches();
    productSetSyncLabel('schedule-sync-label','Синхронизировано'); productSetSyncLabel('notes-sync-label','Синхронизировано');
    if(!document.getElementById('schedule-screen')?.hidden) renderSchedule();
    if(!document.getElementById('notes-screen')?.hidden) renderNotes();
    return true;
  }catch(error){
    svgDiag?.('product:sync-error',{level:'warning',message:error?.message||String(error)});
    if(!productScheduleLoaded){ const local=readJSON(STORAGE.schedule,[]); productScheduleDefinitions=Array.isArray(local)?local:[]; productScheduleLoaded=true; }
    if(!productNotesLoaded){ const local=readJSON(STORAGE.notes,[]); notesData=Array.isArray(local)?local.map(normalizeProductNote).filter(Boolean):[]; productNotesLoaded=true; }
    applyProductCaches(); productSetSyncLabel('schedule-sync-label','Офлайн'); productSetSyncLabel('notes-sync-label','Офлайн'); return false;
  }finally{productSyncInFlight=false;}
}
window.syncScheduleNotesWithServer=syncScheduleNotesWithServer;


function getScheduleEventDate(event) {
  if (!event || typeof event !== 'object') return null;
  const raw = event.startAt || event.starts_at || event.start || event.dateTime || event.datetime || event.date || null;
  if (!raw) return null;
  if (typeof raw === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(raw)) {
    const time = String(event.time || '12:00').trim();
    const parsed = new Date(`${raw}T${/^\d{1,2}:\d{2}$/.test(time) ? time : '12:00'}:00`);
    return Number.isNaN(parsed.getTime()) ? null : parsed;
  }
  const parsed = new Date(raw);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}
function getScheduleEventsForDate(date) {
  const key = productDateKey(date);
  return (scheduleData || []).filter(event => { const eventDate=getScheduleEventDate(event); return eventDate && productDateKey(eventDate)===key && event?.status!=='cancelled'; });
}
function getNextScheduleEvent(now = new Date()) {
  const nowMs=now.getTime(),today=productDateKey(now);
  return (scheduleData || []).map(event=>{
    const date=getScheduleEventDate(event);
    const endRaw=event?.endAt||event?.ends_at;
    const end=endRaw?new Date(endRaw):null;
    const allDay=Boolean(event?.all_day);
    const activeAllDay=allDay&&date&&productDateKey(date)===today;
    const activeTimed=end&&!Number.isNaN(end.getTime())&&end.getTime()>=nowMs&&date&&date.getTime()<=nowMs;
    const upcoming=date&&date.getTime()>=nowMs-60000;
    return {event,date,active:Boolean(activeAllDay||activeTimed),eligible:Boolean(activeAllDay||activeTimed||upcoming)};
  }).filter(item=>item.date&&item.eligible&&item.event?.status!=='cancelled'&&item.event?.status!=='completed').sort((a,b)=>Number(b.active)-Number(a.active)||a.date-b.date)[0]||null;
}
function getNotesForDate(date) {
  const key=productDateKey(date);
  return (notesData || []).filter(note=>{const raw=note?.updatedAt||note?.updated_at||note?.createdAt||note?.created_at||note?.date;const d=raw?new Date(raw):null;return d&&!Number.isNaN(d.getTime())&&productDateKey(d)===key&&note?.archived!==true;});
}
window.getScheduleEventDate=getScheduleEventDate;window.getScheduleEventsForDate=getScheduleEventsForDate;window.getNextScheduleEvent=getNextScheduleEvent;window.getNotesForDate=getNotesForDate;

function scheduleMonday(date){const d=new Date(date);d.setHours(12,0,0,0);const day=(d.getDay()+6)%7;d.setDate(d.getDate()-day);return d;}
function scheduleOccurrencesForDate(date){const key=productDateKey(date);const start=new Date(date);start.setHours(0,0,0,0);const end=new Date(date);end.setHours(23,59,59,999);return expandScheduleDefinitions(productScheduleDefinitions,start,end).filter(item=>productDateKey(new Date(item.startAt||item.starts_at))===key);}
function scheduleBaseById(id){return productScheduleDefinitions.find(event=>String(event.id)===String(id));}
function scheduleParticipantLabel(person){return person?.first_name||person?.username||'Друг';}
function scheduleShared(event){return (event?.participants||[]).length>1;}
function openSchedule(){productCloseOtherScreens();const screen=document.getElementById('schedule-screen');if(!screen)return;screen.hidden=false;productLockBody(true);scheduleSelectedDate=new Date();scheduleWeekCursor=scheduleMonday(scheduleSelectedDate);syncScheduleNotesWithServer().finally(renderSchedule);}
function closeSchedule(){const s=document.getElementById('schedule-screen');if(s)s.hidden=true;productLockBody(false);}
function shiftScheduleWeek(delta){const base=scheduleWeekCursor||scheduleMonday(new Date());base.setDate(base.getDate()+Number(delta||0)*7);scheduleWeekCursor=scheduleMonday(base);scheduleSelectedDate=new Date(scheduleWeekCursor);renderSchedule();}
function selectScheduleDate(key){const d=new Date(`${key}T12:00:00`);if(Number.isNaN(d.getTime()))return;scheduleSelectedDate=d;scheduleWeekCursor=scheduleMonday(d);renderSchedule();}
function renderSchedule(){
  const strip=document.getElementById('schedule-week-strip'),root=document.getElementById('schedule-event-list');if(!strip||!root)return;
  const monday=scheduleWeekCursor||scheduleMonday(scheduleSelectedDate||new Date());scheduleWeekCursor=new Date(monday);
  const days=Array.from({length:7},(_,i)=>{const d=new Date(monday);d.setDate(d.getDate()+i);return d;});
  const sunday=days[6];const title=document.getElementById('schedule-week-title');if(title)title.textContent=`${productFormatDate(monday,{day:'numeric',month:'short'})} – ${productFormatDate(sunday,{day:'numeric',month:'short'})}`;
  strip.replaceChildren(...days.map(day=>{const b=document.createElement('button');b.type='button';b.className='schedule-day-button';const key=productDateKey(day);if(key===productDateKey(scheduleSelectedDate))b.classList.add('is-selected');if(key===productDateKey(new Date()))b.classList.add('is-today');if(scheduleOccurrencesForDate(day).length)b.classList.add('has-events');const w=document.createElement('small');w.textContent=productFormatDate(day,{weekday:'short'}).replace('.','');const n=document.createElement('strong');n.textContent=String(day.getDate());const dot=document.createElement('i');b.append(w,n,dot);b.onclick=()=>selectScheduleDate(key);return b;}));
  const selected=scheduleSelectedDate||new Date();const items=scheduleOccurrencesForDate(selected).sort((a,b)=>new Date(a.startAt)-new Date(b.startAt));
  const listTitle=document.getElementById('schedule-list-title');if(listTitle)listTitle.textContent=productDateKey(selected)===productDateKey(new Date())?'Сегодня':productFormatDate(selected,{weekday:'long',day:'numeric',month:'long'});
  const sharedCount=document.getElementById('schedule-shared-count');if(sharedCount)sharedCount.textContent=String(items.filter(scheduleShared).length);
  if(!items.length){const e=document.createElement('p');e.className='product-empty';e.textContent='На этот день ничего не запланировано.';root.replaceChildren(e);return;}
  root.replaceChildren(...items.map(item=>{
    const base=scheduleBaseById(item.id)||item;const button=document.createElement('button');button.type='button';button.className='schedule-event-row';button.onclick=()=>openScheduleEditor(item.id);
    const time=document.createElement('span');time.className='schedule-time';const d=new Date(item.startAt||item.starts_at);const strong=document.createElement('strong');strong.textContent=base.all_day?'Весь день':productFormatDate(d,{hour:'2-digit',minute:'2-digit'});const small=document.createElement('small');small.textContent=base.recurrence&&base.recurrence!=='none'?'повтор':'событие';time.append(strong,small);
    const copy=document.createElement('span');copy.className='schedule-event-copy';const name=document.createElement('strong');name.textContent=base.title||'Событие';const meta=document.createElement('small');const participantNames=(base.participants||[]).filter(p=>p.role!=='owner').map(scheduleParticipantLabel);meta.textContent=base.details||(participantNames.length?`Вместе: ${participantNames.join(', ')}`:'Личное событие');copy.append(name,meta);
    const tail=document.createElement('span');tail.className='schedule-event-meta';if(scheduleShared(base)){const badge=document.createElement('span');badge.className='shared-badge';badge.textContent=`${(base.participants||[]).length}`;tail.append(badge);}if(base.reminder_minutes!==null&&base.reminder_minutes!==undefined){const r=document.createElement('span');r.className='reminder-badge';r.textContent='⌁';tail.append(r);}const arrow=document.createElement('b');arrow.textContent='›';tail.append(arrow);button.append(time,copy,tail);return button;
  }));
}

function renderScheduleFriendPicker(event=null){const root=document.getElementById('schedule-friend-picker');if(!root)return;const friends=profileState?.friends||[];const isOwner=!event||event.is_owner!==false;const selected=new Set((event?.participants||[]).filter(p=>p.role!=='owner').map(p=>String(p.id)));if(!friends.length){const e=document.createElement('span');e.className='friend-picker-empty';e.textContent='Добавь друзей в профиле, чтобы планировать вместе.';root.replaceChildren(e);return;}root.replaceChildren(...friends.map(friend=>{const b=document.createElement('button');b.type='button';b.className='friend-choice';b.dataset.userId=friend.id;if(selected.has(String(friend.id)))b.classList.add('is-selected');if(!isOwner)b.disabled=true;const dot=document.createElement('i');const text=document.createElement('span');text.textContent=scheduleParticipantLabel(friend);b.append(dot,text);b.onclick=()=>b.classList.toggle('is-selected');return b;}));}
function selectedScheduleFriends(){return [...document.querySelectorAll('#schedule-friend-picker .friend-choice.is-selected')].map(b=>Number(b.dataset.userId)).filter(Number.isFinite);}
function openScheduleEditor(eventId=null){
  if(!productHasAuth()){productToast('Сначала войди через Telegram');return;}scheduleEditingId=eventId||null;const event=eventId?scheduleBaseById(eventId):null;const now=new Date();now.setMinutes(Math.ceil(now.getMinutes()/15)*15,0,0);const start=event?new Date(event.starts_at):now;const end=event?.ends_at?new Date(event.ends_at):new Date(start.getTime()+3600000);
  document.getElementById('schedule-event-id').value=event?.id||'';document.getElementById('schedule-event-title').value=event?.title||'';document.getElementById('schedule-event-date').value=productLocalDateInput(start);document.getElementById('schedule-event-all-day').checked=Boolean(event?.all_day);document.getElementById('schedule-event-time').value=productLocalTimeInput(start);document.getElementById('schedule-event-end-time').value=productLocalTimeInput(end);document.getElementById('schedule-event-recurrence').value=event?.recurrence||'none';document.getElementById('schedule-event-recurrence-until').value=event?.recurrence_until?productLocalDateInput(new Date(event.recurrence_until)):'';document.getElementById('schedule-event-reminder').value=event?.reminder_minutes===null||event?.reminder_minutes===undefined?'-1':String(event.reminder_minutes);document.getElementById('schedule-event-details').value=event?.details||'';document.getElementById('schedule-editor-title').textContent=event?'Событие':'Новое событие';const del=document.getElementById('schedule-delete-button');if(del){del.hidden=!event;del.textContent=event?.is_owner===false?'Убрать у себя':'Удалить событие';}renderScheduleFriendPicker(event);toggleScheduleRecurrenceField();toggleScheduleAllDay();document.getElementById('schedule-editor-sheet').hidden=false;setTimeout(()=>document.getElementById('schedule-event-title')?.focus(),80);
}
function closeScheduleEditor(){const s=document.getElementById('schedule-editor-sheet');if(s)s.hidden=true;scheduleEditingId=null;}
function toggleScheduleRecurrenceField(){const repeat=document.getElementById('schedule-event-recurrence')?.value||'none';const f=document.getElementById('schedule-recurrence-until-field');if(f)f.hidden=repeat==='none';}
function toggleScheduleAllDay(){const allDay=Boolean(document.getElementById('schedule-event-all-day')?.checked);const fields=document.getElementById('schedule-time-fields');if(fields)fields.hidden=allDay;}
document.addEventListener('change',event=>{if(event.target?.id==='schedule-event-recurrence')toggleScheduleRecurrenceField();});
async function saveScheduleEvent(){
  const title=document.getElementById('schedule-event-title').value.trim(),date=document.getElementById('schedule-event-date').value,time=document.getElementById('schedule-event-time').value,endTime=document.getElementById('schedule-event-end-time').value,allDay=Boolean(document.getElementById('schedule-event-all-day')?.checked);if(!title){productToast('Укажи название');return;}if(!date||(!allDay&&!time)){productToast(allDay?'Укажи дату':'Укажи дату и время');return;}
  const start=new Date(`${date}T${allDay?'00:00':time}:00`);const end=!allDay&&endTime?new Date(`${date}T${endTime}:00`):null;if(end&&end<start){productToast('Окончание раньше начала');return;}
  const recurrence=document.getElementById('schedule-event-recurrence').value;const untilRaw=document.getElementById('schedule-event-recurrence-until').value;const reminderRaw=document.getElementById('schedule-event-reminder').value;
  const payload={title,details:document.getElementById('schedule-event-details').value.trim(),starts_at:start.toISOString(),ends_at:end?end.toISOString():null,all_day:allDay,recurrence,recurrence_until:recurrence!=='none'&&untilRaw?new Date(`${untilRaw}T23:59:59`).toISOString():null,reminder_minutes:Number(reminderRaw)<0?null:Number(reminderRaw),participant_ids:selectedScheduleFriends()};
  const id=scheduleEditingId;try{const response=await fetch(id?`/api/schedule/events/${encodeURIComponent(id)}`:'/api/schedule/events',{method:id?'PUT':'POST',headers:productHeaders(true),body:JSON.stringify(payload)});const data=await response.json().catch(()=>({}));if(!response.ok)throw new Error(data.detail||'Не удалось сохранить событие');closeScheduleEditor();await syncScheduleNotesWithServer(true);renderSchedule();productToast(id?'Событие обновлено':'Событие добавлено');}catch(error){productToast(error?.message||'Ошибка сохранения');}
}
function deleteScheduleEvent(){const id=scheduleEditingId;if(!id)return;const event=scheduleBaseById(id);const message=event?.is_owner===false?'Убрать это совместное событие из своего расписания?':'Удалить событие у всех участников?';requestConfirm(message,async()=>{try{const response=await fetch(`/api/schedule/events/${encodeURIComponent(id)}`,{method:'DELETE',headers:productHeaders(false)});const data=await response.json().catch(()=>({}));if(!response.ok)throw new Error(data.detail||'Ошибка удаления');closeScheduleEditor();await syncScheduleNotesWithServer(true);renderSchedule();productToast(data.result==='left'?'Событие убрано':'Событие удалено');}catch(error){productToast(error?.message||'Ошибка удаления');}});}

function openNotes(){productCloseOtherScreens();const screen=document.getElementById('notes-screen');if(!screen)return;screen.hidden=false;productLockBody(true);syncScheduleNotesWithServer().finally(renderNotes);}
function closeNotes(){const s=document.getElementById('notes-screen');if(s)s.hidden=true;productLockBody(false);}
function renderNotes(){const root=document.getElementById('notes-list');if(!root)return;const q=String(document.getElementById('notes-search-input')?.value||'').trim().toLocaleLowerCase('ru-RU');const items=(notesData||[]).filter(note=>note.archived!==true).filter(note=>!q||`${note.title||''} ${note.body||''}`.toLocaleLowerCase('ru-RU').includes(q)).sort((a,b)=>Number(b.pinned)-Number(a.pinned)||String(b.updated_at||b.updatedAt||'').localeCompare(String(a.updated_at||a.updatedAt||'')));const count=document.getElementById('notes-count');if(count)count.textContent=String(items.length);if(!items.length){const e=document.createElement('p');e.className='product-empty';e.textContent=q?'Ничего не найдено.':'Заметок пока нет. Здесь можно хранить идеи, списки и напоминания.';root.replaceChildren(e);return;}root.replaceChildren(...items.map(note=>{const b=document.createElement('button');b.type='button';b.className='note-row';b.onclick=()=>openNoteEditor(note.id);const copy=document.createElement('span');copy.className='note-row-copy';const title=document.createElement('strong');title.textContent=note.title||'Без названия';const text=document.createElement('small');text.textContent=note.body||'Пустая заметка';copy.append(title,text);const meta=document.createElement('span');meta.className='note-row-meta';if(note.pinned){const pin=document.createElement('span');pin.className='pin-badge';pin.textContent='Закреплено';meta.append(pin);}if(note.reminder_at){const rem=document.createElement('span');rem.className='reminder-badge';rem.textContent='⌁';meta.append(rem);}b.append(copy,meta);return b;}));}
function noteById(id){return (notesData||[]).find(n=>String(n.id)===String(id));}
function openNoteEditor(noteId=null){if(!productHasAuth()){productToast('Сначала войди через Telegram');return;}noteEditingId=noteId||null;const note=noteId?noteById(noteId):null;document.getElementById('note-edit-id').value=note?.id||'';document.getElementById('note-title-input').value=note?.title||'';document.getElementById('note-body-input').value=note?.body||'';document.getElementById('note-pinned-input').checked=Boolean(note?.pinned);const enabled=Boolean(note?.reminder_at);document.getElementById('note-reminder-enabled').checked=enabled;const reminderInput=document.getElementById('note-reminder-input');if(reminderInput)reminderInput.value=enabled?toDateTimeLocal(new Date(note.reminder_at)):'';document.getElementById('note-editor-title').textContent=note?'Заметка':'Новая заметка';document.getElementById('note-delete-button').hidden=!note;toggleNoteReminderField();document.getElementById('note-editor-sheet').hidden=false;setTimeout(()=>document.getElementById(note?.title?'note-body-input':'note-title-input')?.focus(),80);}
function closeNoteEditor(){const s=document.getElementById('note-editor-sheet');if(s)s.hidden=true;noteEditingId=null;}
function toDateTimeLocal(date){if(!(date instanceof Date)||Number.isNaN(date.getTime()))return'';return `${productLocalDateInput(date)}T${productLocalTimeInput(date)}`;}
function toggleNoteReminderField(){const enabled=document.getElementById('note-reminder-enabled')?.checked;const field=document.getElementById('note-reminder-field');if(field)field.hidden=!enabled;if(enabled&&!document.getElementById('note-reminder-input').value){const d=new Date(Date.now()+3600000);d.setMinutes(Math.ceil(d.getMinutes()/15)*15,0,0);document.getElementById('note-reminder-input').value=toDateTimeLocal(d);}}
async function saveNote(){const title=document.getElementById('note-title-input').value.trim(),body=document.getElementById('note-body-input').value.trim();if(!title&&!body){productToast('Заметка пустая');return;}const reminderEnabled=document.getElementById('note-reminder-enabled').checked;const reminderRaw=document.getElementById('note-reminder-input').value;const payload={title,body,pinned:document.getElementById('note-pinned-input').checked,archived:false,reminder_at:reminderEnabled&&reminderRaw?new Date(reminderRaw).toISOString():null};const id=noteEditingId;try{const response=await fetch(id?`/api/notes/${encodeURIComponent(id)}`:'/api/notes',{method:id?'PUT':'POST',headers:productHeaders(true),body:JSON.stringify(payload)});const data=await response.json().catch(()=>({}));if(!response.ok)throw new Error(data.detail||'Не удалось сохранить заметку');closeNoteEditor();await syncScheduleNotesWithServer(true);renderNotes();productToast(id?'Заметка обновлена':'Заметка сохранена');}catch(error){productToast(error?.message||'Ошибка сохранения');}}
function deleteNote(){const id=noteEditingId;if(!id)return;requestConfirm('Удалить заметку?',async()=>{try{const response=await fetch(`/api/notes/${encodeURIComponent(id)}`,{method:'DELETE',headers:productHeaders(false)});if(!response.ok)throw new Error('Не удалось удалить заметку');closeNoteEditor();await syncScheduleNotesWithServer(true);renderNotes();productToast('Заметка удалена');}catch(error){productToast(error?.message||'Ошибка удаления');}});}

window.toggleScheduleAllDay=toggleScheduleAllDay;window.openSchedule=openSchedule;window.closeSchedule=closeSchedule;window.shiftScheduleWeek=shiftScheduleWeek;window.selectScheduleDate=selectScheduleDate;window.renderSchedule=renderSchedule;window.openScheduleEditor=openScheduleEditor;window.closeScheduleEditor=closeScheduleEditor;window.saveScheduleEvent=saveScheduleEvent;window.deleteScheduleEvent=deleteScheduleEvent;window.openNotes=openNotes;window.closeNotes=closeNotes;window.renderNotes=renderNotes;window.openNoteEditor=openNoteEditor;window.closeNoteEditor=closeNoteEditor;window.toggleNoteReminderField=toggleNoteReminderField;window.saveNote=saveNote;window.deleteNote=deleteNote;

/* Preserve the actual V28 implementations before legacy script.js defines its
   validation-compatible forwarding functions. The old V27 deploy preflight
   checks only script.js, so it cannot see handlers owned by product.js. */
window.SVGTrackerProductHandlers = Object.freeze({
  closeNoteEditor,
  closeNotes,
  closeSchedule,
  closeScheduleEditor,
  deleteNote,
  deleteScheduleEvent,
  openNoteEditor,
  openNotes,
  openSchedule,
  openScheduleEditor,
  renderNotes,
  saveNote,
  saveScheduleEvent,
  shiftScheduleWeek,
  toggleNoteReminderField,
  toggleScheduleAllDay
});

document.addEventListener('DOMContentLoaded',()=>{
  const localSchedule=readJSON(STORAGE.schedule,[]);productScheduleDefinitions=Array.isArray(localSchedule)?localSchedule:[];
  const localNotes=readJSON(STORAGE.notes,[]);notesData=Array.isArray(localNotes)?localNotes.map(normalizeProductNote).filter(Boolean):[];
  applyProductCaches();
});
window.addEventListener('online',()=>syncScheduleNotesWithServer(true));
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&productHasAuth())syncScheduleNotesWithServer(true);});
