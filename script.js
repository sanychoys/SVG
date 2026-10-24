'use strict';
(() => {
  const $ = selector => document.querySelector(selector);
  const $$ = selector => Array.from(document.querySelectorAll(selector));
  const esc = window.SCUtils.escape;
  const fmt = window.SCUtils.date;
  const state = { sites: [], bots: [], servers: [], hostScanToken:null, site: null, tab: 'content', page: 'sites', currentCode: null, poller: null };
  let toastTimer;
  function notify(message, isError = false) {
    const node = $('#toast'); node.textContent = String(message);
    node.classList.toggle('error', isError); node.classList.add('show');
    clearTimeout(toastTimer); toastTimer = setTimeout(() => node.classList.remove('show'), 4400);
  }
  async function api(path, options = {}) {
    const headers = {'X-Site-Control': '1', ...(options.headers || {})};
    if (miniMode && window.Telegram?.WebApp?.initData) headers['X-Telegram-Init-Data'] = window.Telegram.WebApp.initData;
    if (options.body !== undefined && typeof options.body !== 'string' && !(options.body instanceof Blob)) {
      options.body = JSON.stringify(options.body);
      headers['Content-Type'] = 'application/json';
    }
    const response = await fetch(path, {credentials: 'same-origin', cache: 'no-store', ...options, headers});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw Error(data.detail || `Ошибка HTTP ${response.status}`);
    return data;
  }
  function showPage(page) {
    state.page = page;
    ['Sites','Editor','Bots','Servers'].forEach(v => $(`#view${v}`).hidden = v.toLowerCase() !== page);
    $('#breadcrumb').textContent = page === 'sites' ? 'Проекты' : page === 'bots' ? 'Telegram-боты' : page === 'servers' ? 'Серверы' : (state.site?.client_name || 'Редактор');
    $$('.nav').forEach(node => node.classList.toggle('active', node.dataset.nav === page));
    $('#sidebar').classList.remove('open');
    window.scrollTo({top:0,behavior:'instant'});
  }
  function applyAuth(loggedIn) {
    $('#auth').hidden = loggedIn;
    $('#app').hidden = !loggedIn;
  }
  let miniAuthAttempted = false;
  const miniMode = new URLSearchParams(window.location.search).get('mode') === 'miniapp';
  function denyMiniApp() {
    $('#app').hidden = true;
    $('#auth').hidden = true;
    $('#denied').hidden = false;
  }
  async function tryMiniAppAuth() {
    if (miniAuthAttempted) return false;
    const tg = window.Telegram?.WebApp;
    if (!tg?.initData) return false;
    miniAuthAttempted = true;
    try {
      tg.ready();tg.expand();document.body.classList.add('tg-miniapp');
      const result=await api('/api/sc/auth/miniapp',{method:'POST',body:{init_data:tg.initData}});
      return Boolean(result.authorized);
    } catch (_) { return false; }
  }
  async function bootstrap() {
    if (miniMode) {
      $('#app').hidden=true;$('#auth').hidden=true;
      if (!await tryMiniAppAuth()) {denyMiniApp();return;}
      try {
        const me = await api('/api/sc/auth/me');
        $('#adminLabel').textContent = 'Admin #'+me.admin_id;
        $('#denied').hidden=true;applyAuth(true);await reloadAll();return;
      } catch (_) {denyMiniApp();return;}
    }
    try {
      const me=await api('/api/sc/auth/me');
      $('#adminLabel').textContent='Admin #'+me.admin_id;
      applyAuth(true);await reloadAll();
    } catch (_) {applyAuth(false);}
  }
  async function startLogin() {
    $('#loginBtn').disabled = true;
    try {
      const data = await api('/api/sc/auth/start', {method:'POST'});
      state.currentCode = data.code;
      $('#loginStep').hidden = false;
      $('#loginCode').textContent = '/login ' + data.code;
      $('#loginLink').href = `https://t.me/${encodeURIComponent(data.bot_username)}?start=login_${encodeURIComponent(data.code)}`;
      $('#loginBtn').textContent = 'Код выдан · ожидаем подтверждения';
      clearInterval(state.poller);
      let attempts=0;
      state.poller = setInterval(async () => {
        if (!state.currentCode) return;
        attempts++;
        if (attempts > 115) {clearInterval(state.poller);state.poller=null;notify('Код устарел. Создайте новый.',true);$('#loginBtn').disabled=false;return;}
        try {
          const result=await api('/api/sc/auth/status?code='+encodeURIComponent(state.currentCode));
          if (result.authorized) {
            state.currentCode=null;clearInterval(state.poller);state.poller=null;
            $('#loginStep').hidden=true;await bootstrap();
          }
        } catch (e) {
          // Wait quietly; the user sees progress on screen.
        }
      }, 5000);
    } catch (e) {
      notify(e.message,true);
    } finally {
      $('#loginBtn').disabled = false;
      $('#loginBtn').textContent = 'Создать новый код';
    }
  }
  async function reloadAll() {
    const [sites,bots,servers] = await Promise.all([api('/api/sc/sites'),api('/api/sc/bots'),api('/api/sc/servers')]);
    state.sites=sites.sites;state.bots=bots.bots;state.servers=servers.servers;
    $('#statSites').textContent=sites.sites.length;
    $('#statPublished').textContent=sites.sites.filter(s=>s.published_version>0).length;
    $('#statLeads').textContent=sites.sites.reduce((n,s)=>n+s.new_leads,0);
    $('#statBots').textContent=bots.bots.filter(b=>b.enabled).length;
    $('#navCount').textContent=sites.sites.length;
    $('#projectCount').textContent=sites.sites.length+' сайтов';
    renderSites();renderBots();renderServers();
  }
  function renderSites() {
    const target=$('#projects');
    if (!state.sites.length) {target.innerHTML='<div class="empty-state"><h3>Здесь будут ваши сайты</h3><p>Создайте первый проект и настройте его внешний вид, контент и Telegram-бота.</p></div>';return;}
    target.innerHTML=state.sites.map((s,i)=>`<article class="project-card">
      <div class="project-art"><span class="big-icon">${esc(s.client_name.slice(0,1).toUpperCase())}</span><span class="${s.published_version?'published':'draft-badge'}">${s.published_version?'Опубликован':'Черновик'}</span></div>
      <div class="project-body"><div class="project-name">${esc(s.client_name)}</div><div class="project-domain">/api/sc/public/${esc(s.slug)}</div>
      <div class="project-footer"><span>${Number(s.new_leads)} новых заявок · ${s.bot_name?'Бот подключён':'Без бота'}</span><button data-open-site="${Number(s.id)}">Редактировать →</button></div></div></article>`).join('');
    $$('[data-open-site]').forEach(el=>el.addEventListener('click',()=>openSite(Number(el.dataset.openSite))));
  }
  function renderBots() {
    const target=$('#botsList');
    if (!state.bots.length) {target.innerHTML='<div class="empty-state"><h3>Подключённых ботов пока нет</h3><p>Создайте клиентский сайт и подключите отдельного бота во вкладке Telegram.</p></div>';return;}
    target.innerHTML=state.bots.map(b=>`<div class="bot-row"><div><strong>@${esc(b.username)}</strong><small>Клиент ID ${Number(b.tenant_id)} · ${b.enabled?'Активен':'Отключён'}</small></div><span class="${b.enabled?'published':'draft-badge'}">${b.enabled?'Активен':'Отключён'}</span></div>`).join('');
  }
  async function openSite(id) {
    try {
      const detail=await api('/api/sc/sites/'+id);
      state.site={...detail.site, leads:detail.leads, versions:detail.versions, bots:detail.bots};
      $('#editorName').textContent=state.site.client_name;
      $('#editorSlug').textContent=`/api/sc/public/${state.site.slug}`;
      $('#editorVersion').textContent=state.site.published_version ? `Версия ${state.site.published_version}`:'Черновик';
      $('#clientId').value=state.site.client_telegram_id || '';
      $$('[data-field]').forEach(field=>{field.value=state.site.draft[field.dataset.field] ?? '';});
      state.site.draft.items=state.site.draft.items || [];
      renderItems();renderLeads();renderHistory();renderProjectBots();
      switchTab('content');showPage('editor');refreshPreview();
    } catch (e) {notify(e.message,true);}
  }
  function switchTab(tab) {
    state.tab=tab;
    $$('.tab').forEach(node=>node.classList.toggle('active',node.dataset.tab===tab));
    ['Content','Design','Leads','Bot','History'].forEach(t=>$('#tab'+t).hidden=t.toLowerCase()!==tab);
  }
  function collectDraft() {
    const draft=structuredClone(state.site.draft);
    $$('[data-field]').forEach(f=>{draft[f.dataset.field]=f.value;});
    draft.items=collectItems();
    return draft;
  }
  function collectItems() {
    const result=[];
    $$('#itemsEditor .item-edit').forEach(node=>{
      const item={id:node.dataset.id||'', name:node.querySelector('[name=name]').value,
        price:node.querySelector('[name=price]').value,description:node.querySelector('[name=description]').value};
      if (item.name.trim()) result.push(item);
    });
    return result;
  }
  function renderItems() {
    const items=state.site.draft.items || [];
    $('#itemsEditor').innerHTML=items.length ? items.map((item,i)=>`<div class="item-edit" data-id="${esc(item.id||'')}"><div class="form-grid"><label>Название<input name="name" maxlength="100" value="${esc(item.name)}"></label><label>Цена<input name="price" maxlength="90" value="${esc(item.price)}"></label></div><label>Описание<textarea name="description" maxlength="300" rows="2">${esc(item.description)}</textarea></label><div class="item-actions"><button class="danger-link" data-del-item="${i}">Удалить услугу</button></div></div>`).join('') : '<p class="hint">Пока пусто. Добавьте первую услугу или товар.</p>';
    $$('[data-del-item]').forEach(el=>el.addEventListener('click',()=>{state.site.draft=collectDraft();state.site.draft.items.splice(Number(el.dataset.delItem),1);renderItems();}));
  }
  async function saveDraft(silent=false) {
    if (!state.site)return;
    const draft=collectDraft();
    const result=await api(`/api/sc/sites/${state.site.id}`,{method:'PUT',body:{draft,client_telegram_id:$('#clientId').value.trim()}});
    state.site.draft=result.draft;
    if (!silent)notify('Черновик сохранён');
    refreshPreview();
    return result;
  }
  async function publish() {
    await saveDraft(true);
    const result=await api(`/api/sc/sites/${state.site.id}/publish`,{method:'POST'});
    notify(`Сайт опубликован — версия ${result.version}`);
    await openSite(state.site.id);
    await reloadAll();
  }
  function refreshPreview() {
    if (!state.site)return;
    $('#previewFrame').src=`/api/sc/preview/${state.site.id}?v=${Date.now()}`;
  }
  function renderLeads() {
    const leads=state.site.leads;
    $('#leadsBadge').textContent=leads.filter(l=>l.status==='new').length||'';
    $('#leadsList').innerHTML=leads.length ? leads.map(l=>`<article class="lead-row"><header><strong>Заявка #${l.id} · ${esc(l.customer)}</strong><small>${fmt(l.created_at)}</small></header><p>Контакты: ${esc(l.contact)}<br>${esc(l.message)}</p><select data-lead-id="${l.id}"><option value="new" ${l.status==='new'?'selected':''}>Новая</option><option value="in_progress" ${l.status==='in_progress'?'selected':''}>В работе</option><option value="done" ${l.status==='done'?'selected':''}>Завершена</option><option value="rejected" ${l.status==='rejected'?'selected':''}>Отклонена</option></select></article>`).join('') : '<div class="empty-state"><h3>Заявок ещё нет</h3><p>После публикации сайта посетители смогут отправлять заявки из формы.</p></div>';
    $$('[data-lead-id]').forEach(el=>el.addEventListener('change',async()=>{
      try{await api(`/api/sc/sites/${state.site.id}/leads/${el.dataset.leadId}`,{method:'PATCH',body:{status:el.value}});notify('Статус заявки обновлён');await openSite(state.site.id);switchTab('leads');await reloadAll();}
      catch(e){notify(e.message,true);}
    }));
  }
  function renderHistory() {
    $('#historyList').innerHTML=state.site.versions.length?state.site.versions.map(v=>`<div class="history-row"><div><strong>Версия ${v.version}</strong><small>${fmt(v.created_at)} · ${esc(v.author)}</small></div><button data-rollback="${v.version}">Восстановить</button></div>`).join(''):'<p class="hint">Здесь появятся публикации и версии сайта.</p>';
    $$('[data-rollback]').forEach(button=>button.addEventListener('click',async()=>{
      if(!SCUtils.confirmDanger(`Восстановить версию ${button.dataset.rollback}? Текущее оформление будет заменено новой публикацией.`))return;
      try{await api(`/api/sc/sites/${state.site.id}/rollback/${button.dataset.rollback}`,{method:'POST'});notify('Версия восстановлена');await openSite(state.site.id);switchTab('history');await reloadAll();}
      catch(e){notify(e.message,true);}
    }));
  }
  function renderProjectBots() {
    const list=state.site.bots;
    $('#projectBots').innerHTML=list.length?list.map(b=>`<div class="bot-row"><div><strong>@${esc(b.username)}</strong><small>${b.enabled?'Подключён к клиенту':'Отключён'}</small></div>${b.enabled?`<button data-disable-bot="${b.id}">Отключить</button>`:''}</div>`).join(''):'<p class="hint">У клиента нет подключённого бота.</p>';
    $$('[data-disable-bot]').forEach(el=>el.addEventListener('click',async()=>{
      if (!SCUtils.confirmDanger('Отключить бота? Его webhook будет удалён, уведомления перестанут приходить.'))return;
      try{await api(`/api/sc/bots/${el.dataset.disableBot}/disable`,{method:'POST'});notify('Бот отключён');await openSite(state.site.id);switchTab('bot');await reloadAll();}
      catch(e){notify(e.message,true);}
    }));
  }
  async function attachBot() {
    const input=$('#botToken');
    if(!input.value.trim())return notify('Введите токен из BotFather',true);
    try{
      const result=await api('/api/sc/bots',{method:'POST',body:{tenant_id:state.site.tenant_id,token:input.value.trim()}});
      input.value='';notify(`Подключён @${result.bot.username}`);await openSite(state.site.id);switchTab('bot');await reloadAll();
    }catch(e){notify(e.message,true);}
  }
  async function imageUpload() {
    const file=$('#heroUpload').files[0];
    if(!file)return;
    try{
      SCUtils.loadImage(file);
      const result=await api(`/api/sc/sites/${state.site.id}/media`,{method:'POST',body:file,headers:{'Content-Type':file.type}});
      $('[data-field=hero_image]').value=result.url;
      notify('Изображение загружено. Сохраните черновик и опубликуйте сайт.');
    }catch(e){notify(e.message,true);}
    $('#heroUpload').value='';
  }
  function openNewModal(){ $('#modal').hidden=false;$('#newName').focus(); }
  async function createNew() {
    const name=$('#newName').value.trim(),slug=$('#newSlug').value.trim().toLowerCase(),client=$('#newClient').value.trim();
    try{
      const result=await api('/api/sc/sites',{method:'POST',body:{name,slug,client_telegram_id:client}});
      $('#modal').hidden=true;['#newName','#newSlug','#newClient'].forEach(id=>$(id).value='');
      notify('Проект создан');await reloadAll();await openSite(result.id);
    }catch(e){notify(e.message,true);}
  }
  function renderServers() {
    const select=$('#serverSite');
    const chosen=select.value;
    select.innerHTML='<option value="">Без привязки к сайту</option>'+state.sites.map(s=>`<option value="${Number(s.id)}">${esc(s.client_name)}</option>`).join('');
    select.value=chosen;
    const list=$('#serversList');
    list.innerHTML=state.servers.length?state.servers.map(s=>`<article class="server-card" data-server-id="${Number(s.id)}"><div class="server-card-head"><div><strong>${esc(s.name)}</strong><small>${esc(s.username)}@${esc(s.host)}:${Number(s.port)} · ${esc(s.site_slug||'Не привязан')}</small></div><span class="published">SSH</span></div><div class="server-actions"><button data-remote-template="uptime">Нагрузка</button><button data-remote-template="disk">Диск</button><button data-remote-template="memory">Память</button><button data-remote-template="site_status">Сервис</button><button data-remote-template="site_logs">Логи</button><button data-remote-template="site_restart">Перезапуск</button></div><label>Ручная SSH-команда<input data-remote-input placeholder="Например, ls -la /srv" maxlength="1200" autocomplete="off"></label><button class="secondary compact" data-remote-template="manual">Выполнить вручную</button><button class="danger-link" data-remote-delete>Удалить подключение</button><pre class="ssh-output" data-remote-output>Результат выполнения появится здесь.</pre></article>`).join(''):'<div class="empty-state"><h3>Подключённых серверов нет</h3><p>Добавьте VPS и настройте SSH-ключ по инструкции.</p></div>';
    list.querySelectorAll('[data-remote-template]').forEach(btn=>btn.addEventListener('click',safeAction(async()=>{
      const card=btn.closest('[data-server-id]'),id=Number(card.dataset.serverId),template=btn.dataset.remoteTemplate;
      const command=template==='manual'?card.querySelector('[data-remote-input]').value.trim():undefined;
      if (template==='manual' && !command)return notify('Введите SSH-команду',true);
      if (!SCUtils.confirmDanger(`Выполнить на VPS #${id}: ${command||template}?`))return;
      const confirm=['manual','site_restart'].includes(template)?prompt('Для подтверждения введите EXECUTE'):undefined;
      if (['manual','site_restart'].includes(template) && confirm!=='EXECUTE')return;
      const output=card.querySelector('[data-remote-output]');output.textContent='Выполняется…';
      try {const res=await api(`/api/sc/servers/${id}/execute`,{method:'POST',body:{template,command,confirm}});output.textContent=`Код ${res.exit_code}${res.truncated?' · вывод сокращён':''}\n\n${res.output}`;notify(res.ok?'Команда выполнена':'Команда вернула ошибку',!res.ok);}
      catch(e){output.textContent=e.message;notify(e.message,true);}
    })));
    list.querySelectorAll('[data-remote-delete]').forEach(btn=>btn.addEventListener('click',safeAction(async()=>{
      const id=Number(btn.closest('[data-server-id]').dataset.serverId);
      if(!SCUtils.confirmDanger('Удалить SSH-подключение? Сайт и удалённый сервер не удаляются.'))return;
      await api(`/api/sc/servers/${id}`,{method:'DELETE'});notify('Подключение удалено');await reloadAll();
    })));
  }
  async function createServer() {
    const data={name:$('#serverName').value.trim(),host:$('#serverHost').value.trim(),port:Number($('#serverPort').value),username:$('#serverUser').value.trim(),key_alias:$('#serverKey').value.trim(),service:$('#serverService').value.trim(),site_id:$('#serverSite').value||null};
    await api('/api/sc/servers',{method:'POST',body:data});
    notify('Подключение сохранено. SSH доступ проверится при первой команде.');
    await reloadAll();
  }
  const safeAction=fn=>async()=>{try{await fn();}catch(e){notify(e.message,true);}};
  function prepareTelegramSdk() {
    if (window.Telegram?.WebApp) return Promise.resolve();
    return new Promise(resolve => {
      let settled=false;
      const finish=()=>{ if(!settled) {settled=true;resolve();} };
      const sdk=document.createElement('script');
      sdk.src='https://telegram.org/js/telegram-web-app.js';
      sdk.async=true;
      sdk.onload=()=>{finish(); if (window.Telegram?.WebApp?.initData && (miniMode || ($('#auth') && !$('#auth').hidden))) bootstrap();};
      sdk.onerror=finish;
      document.head.appendChild(sdk);
      // Browser log-in remains available even if Telegram's CDN is unavailable.
      setTimeout(finish,1600);
    });
  }
  document.addEventListener('DOMContentLoaded',()=>{
    $('#loginBtn').addEventListener('click',startLogin);
    $('#copyCode').addEventListener('click',safeAction(async()=>{await SCUtils.copy($('#loginCode').textContent);notify('Команда скопирована');}));
    $('#logout').addEventListener('click',safeAction(async()=>{await api('/api/sc/auth/logout',{method:'POST'});if(miniMode)denyMiniApp();else applyAuth(false);$('#sidebar').classList.remove('open');}));
    $('#mobileMenu').addEventListener('click',()=>$('#sidebar').classList.toggle('open'));
    $$('[data-nav]').forEach(n=>n.addEventListener('click',async()=>{showPage(n.dataset.nav);if(['bots','servers'].includes(n.dataset.nav))await reloadAll().catch(e=>notify(e.message,true));}));
    $('#createBtn').addEventListener('click',openNewModal);
    $('#serverCreateBtn').addEventListener('click',safeAction(createServer));
    $('#makeSshKey').addEventListener('click',safeAction(async()=>{
      const alias=$('#serverKey').value.trim();
      const result=await api('/api/sc/ssh/key',{method:'POST',body:{alias}});
      $('#sshPublic').value=result.public_key;notify('Публичный ключ готов. Скопируйте его на целевой VPS.');
    }));
    $('#scanHost').addEventListener('click',safeAction(async()=>{
      const result=await api('/api/sc/ssh/scan',{method:'POST',body:{host:$('#serverHost').value.trim(),port:Number($('#serverPort').value)}});
      state.hostScanToken=result.token;$('#hostFingerprint').value=result.fingerprint;notify('Сверьте отпечаток с целевым VPS перед подтверждением.');
    }));
    $('#trustHost').addEventListener('click',safeAction(async()=>{
      if(!state.hostScanToken)return notify('Сначала получите отпечаток',true);
      const fingerprint=$('#hostFingerprintConfirm').value.trim();
      if(!SCUtils.confirmDanger('Отпечаток точно сверен непосредственно на целевом сервере?'))return;
      await api('/api/sc/ssh/trust',{method:'POST',body:{token:state.hostScanToken,fingerprint}});
      state.hostScanToken=null;notify('Отпечаток сохранён. SSH-подключение готово к проверке.');
    }));
    $('#closeModal').addEventListener('click',()=>$('#modal').hidden=true);
    $('#modal').addEventListener('click',event=>{if(event.target===$('#modal'))$('#modal').hidden=true;});
    $('#submitNew').addEventListener('click',safeAction(createNew));
    $('#backBtn').addEventListener('click',()=>showPage('sites'));
    $$('.tab').forEach(t=>t.addEventListener('click',()=>switchTab(t.dataset.tab)));
    $('#addItemBtn').addEventListener('click',()=>{
      state.site.draft=collectDraft();
      if (state.site.draft.items.length>=60)return notify('Максимум 60 позиций',true);
      state.site.draft.items.push({name:'Новая услуга',price:'',description:'',id:''});renderItems();
    });
    $('#saveBtn').addEventListener('click',safeAction(()=>saveDraft()));
    $('#publishBtn').addEventListener('click',safeAction(publish));
    $('#refreshPreview').addEventListener('click',refreshPreview);
    $('#previewBtn').addEventListener('click',()=>{if(state.site)window.open(`/api/sc/preview/${state.site.id}`,'_blank','noopener');});
    $('#linkBotBtn').addEventListener('click',safeAction(attachBot));
    $('#heroUpload').addEventListener('change',imageUpload);
    $('#newName').addEventListener('input',()=>{
      if($('#newSlug').dataset.manual==='yes')return;
      const translit={'а':'a','б':'b','в':'v','г':'g','д':'d','е':'e','ё':'e','ж':'zh','з':'z','и':'i','й':'y','к':'k','л':'l','м':'m','н':'n','о':'o','п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f','х':'h','ц':'ts','ч':'ch','ш':'sh','щ':'sch','ъ':'','ы':'y','ь':'','э':'e','ю':'yu','я':'ya'};
      $('#newSlug').value=($('#newName').value.toLowerCase().split('').map(c=>translit[c]??c).join('').replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'').slice(0,56));
    });
    $('#newSlug').addEventListener('input',()=>$('#newSlug').dataset.manual='yes');
    prepareTelegramSdk().then(bootstrap);
  });
})();
