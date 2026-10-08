// Telegram WebApp bootstrap and user binding
document.addEventListener('DOMContentLoaded', function initTelegram(){
  const telegramApp = window.Telegram?.WebApp;
  if (!telegramApp) {
    window.SVG_TELEGRAM_USER = null;
    return;
  }

  telegramApp.ready();
  telegramApp.expand();

  const tgUser = telegramApp.initDataUnsafe?.user || null;
  window.SVG_TELEGRAM_USER = tgUser;
  if (!tgUser) return;

  const name = document.getElementById('name');
  if (name && tgUser.first_name) name.textContent = tgUser.first_name;

  const avatar = document.getElementById('avatar');
  const fallback = document.getElementById('avatar-fallback');
  if (avatar && tgUser.photo_url) {
    avatar.onload = () => {
      avatar.style.display = 'block';
      if (fallback) fallback.style.display = 'none';
    };
    avatar.onerror = () => {
      avatar.style.display = 'none';
      if (fallback) fallback.style.display = '';
    };
    avatar.src = tgUser.photo_url;
  }
});

const tg = window.Telegram?.WebApp;

const STORAGE = {
  goals: 'fitness_goals',
  plan: 'fitness_plan',
  history: 'fitness_history',
  attendance: 'fitness_attendance',
  planMeta: 'fitness_plan_meta_v2',
  activeWorkout: 'active_workout',
  trainingDirty: 'fitness_server_dirty_v1',
  finance: 'finance_budget_v2',
  schedule: 'schedule_events_v1',
  notes: 'notes_v1'
};

let calendarCursor = new Date(new Date().getFullYear(), new Date().getMonth(), 1);
let workoutTimerHandle = null;
let trainingSaveTimer = null;
let trainingServerReady = false;
let trainingSyncInFlight = false;
let selectedGoalType = 'numeric';
let selectedGoalColor = null;
let selectedGoalId = null;
let editingGoalId = null;
const MAX_ACTIVE_WORKOUT_SECONDS = 18 * 60 * 60;
const GOAL_COLORS = ['#9b83ff', '#45d6ff', '#ff8bb7', '#73e6a3', '#ffb45e', '#5f9cff', '#f2df68', '#c78cff'];

function readJSON(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? fallback : JSON.parse(raw);
  } catch (_) {
    return fallback;
  }
}

let financeData = readJSON(STORAGE.finance, null) || {
  monthlyIncome:0,
  mandatoryExpenses:[],
  expenses:[],
  debts:[],
  categories:{},
  budgetHistory:[]
};
let scheduleData = readJSON(STORAGE.schedule, []);
if (!Array.isArray(scheduleData)) scheduleData = [];
let notesData = readJSON(STORAGE.notes, []);
if (!Array.isArray(notesData)) notesData = [];
let profileState = { notifications_enabled: true, friends: [], incoming: [], outgoing: [] };
let profileLoaded = false;
let profileLoading = false;

const state = {
  goals: readJSON(STORAGE.goals, []),
  plan: readJSON(STORAGE.plan, {}),
  history: readJSON(STORAGE.history, []),
  attendance: readJSON(STORAGE.attendance, {}),
  planMeta: readJSON(STORAGE.planMeta, null) || { effectiveFrom: null },
  activeWorkout: readJSON(STORAGE.activeWorkout, null)
};

function parseGoalNumber(value) {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (typeof value !== 'string') return null;
  const normalized = value.trim().replace(',', '.').replace(/\s+/g, '');
  if (!normalized) return null;
  const number = Number(normalized);
  return Number.isFinite(number) ? number : null;
}

function normalizeGoalDate(value, fallback = new Date()) {
  if (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  if (value) {
    const parsed = new Date(value);
    if (!Number.isNaN(parsed.getTime())) return getDateKey(parsed);
  }
  return getDateKey(fallback);
}

function goalDateToTime(dateKey) {
  if (typeof dateKey !== 'string') return Number.NaN;
  const parsed = new Date(`${dateKey}T00:00:00`);
  return Number.isNaN(parsed.getTime()) ? Number.NaN : parsed.getTime();
}

function sortGoalHistory(history) {
  return [...history].sort((a, b) => {
    const dateDiff = goalDateToTime(a.date) - goalDateToTime(b.date);
    if (Number.isFinite(dateDiff) && dateDiff !== 0) return dateDiff;
    return String(a.recordedAt || '').localeCompare(String(b.recordedAt || ''));
  });
}

function appendGoalHistoryEntry(history, entry) {
  const day = normalizeGoalDate(entry.date);
  return sortGoalHistory([...(Array.isArray(history) ? history : []), { ...entry, date: day }]);
}

function getGoalDailyHistory(history) {
  const byDate = new Map();
  sortGoalHistory(Array.isArray(history) ? history : []).forEach(item => {
    const date = normalizeGoalDate(item?.date || item?.recordedAt);
    byDate.set(date, { ...item, date });
  });
  return [...byDate.values()];
}



function hashGoalIdentity(value) {
  let hash = 2166136261;
  const text = String(value || 'goal');
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(36);
}

function getStableGoalId(goal, fallbackTimestamp) {
  const existing = String(goal?.id || '').trim();
  if (existing) return existing;
  return `goal_${hashGoalIdentity(`${goal?.createdAt || goal?.updatedAt || fallbackTimestamp}|${goal?.type || ''}|${goal?.name || ''}`)}`;
}

function createGoalId() {
  if (globalThis.crypto?.randomUUID) return `goal_${globalThis.crypto.randomUUID()}`;
  return `goal_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 9)}`;
}

function getGoalById(goalId) {
  if (!goalId) return null;
  return state.goals.find(goal => goal.id === goalId) || null;
}



Object.assign(state, normalizeTrainingState(state));

if (!state.planMeta.effectiveFrom && Object.keys(state.plan).length) {
  state.planMeta.effectiveFrom = getDateKey(new Date());
}







function closeSheets() {
  const active = document.activeElement;
  if (active?.closest?.('.ios-sheet')) active.blur();
  document.querySelectorAll('.ios-sheet').forEach(sheet => {
    sheet.hidden = true;
    const panel = sheet.querySelector('.sheet-panel');
    if (panel) panel.style.transform = '';
  });
}

function closeSheetByBackdrop(event) {
  if (event.target.classList.contains('ios-sheet')) {
    closeSheets();
  }
}

function setupSheetGestures() {
  document.querySelectorAll('.sheet-panel').forEach(panel => {
    let startY = 0;
    panel.addEventListener('touchstart', e => {
      startY = e.touches[0].clientY;
    }, {passive:true});
    panel.addEventListener('touchend', e => {
      const delta = e.changedTouches[0].clientY - startY;
      if (delta > 100) closeSheets();
    }, {passive:true});
  });
}

function showToast(text) {
  let el = document.querySelector('.app-toast');
  if (!el) {
    el = document.createElement('div');
    el.className = 'app-toast';
    document.body.appendChild(el);
  }
  const sheetOpen = [...document.querySelectorAll('.ios-sheet')].some(sheet => !sheet.hidden);
  el.classList.toggle('is-over-sheet', sheetOpen);
  el.textContent = text;
  el.classList.add('is-visible');
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => el.classList.remove('is-visible'), 1800);
}


function closeTraining() {
  const screen = document.getElementById('training-screen');
  if (!screen) return;
  screen.hidden = true;
  document.body.classList.remove('training-open');
}


function openFinance(){
 const screen=document.getElementById('finance-screen');
 if(!screen)return;
 document.getElementById('training-screen').hidden=true;
 screen.hidden=false;
 document.body.classList.add('training-open');
 renderFinance();
}
function closeFinance(){
 const screen=document.getElementById('finance-screen');
 if(!screen)return;
 screen.hidden=true;
 document.body.classList.remove('training-open');
}
function saveFinance(){localStorage.setItem(STORAGE.finance,JSON.stringify(financeData));}
function openFinanceSheet(type='expense', editId=null){
 const sheet=document.getElementById('finance-sheet');
 if(!sheet)return;
 sheet.hidden=false;
 document.getElementById('finance-entry-type').value=type;
 document.getElementById('finance-edit-id').value=editId||'';
 document.getElementById('finance-entry-title').value='';
 document.getElementById('finance-entry-amount').value='';
}
function daysLeft(){
 const now=new Date();
 return new Date(now.getFullYear(),now.getMonth()+1,0).getDate()-now.getDate()+1;
}
function saveFinanceEntry(){
 const type=document.getElementById('finance-entry-type').value;
 const title=document.getElementById('finance-entry-title').value.trim();
 const amount=Number(document.getElementById('finance-entry-amount').value);
 const category=document.getElementById('finance-entry-category').value;
 const editId=document.getElementById('finance-edit-id').value;
 if(!title || !amount || amount<=0){showToast('Заполни все поля');return;}
 const item={id:editId?Number(editId):Date.now(),title,amount,category,date:new Date().toISOString()};
 if(type==='income') financeData.monthlyIncome=amount;
 if(type==='mandatory') editId ? financeData.mandatoryExpenses=financeData.mandatoryExpenses.map(x=>x.id===Number(editId)?item:x) : financeData.mandatoryExpenses.push(item);
 if(type==='expense') editId ? financeData.expenses=financeData.expenses.map(x=>x.id===Number(editId)?item:x) : financeData.expenses.push(item);
 if(type==='debt'){
  item.kind=document.getElementById('finance-debt-kind').value;
  item.status='Открыт';
  editId ? financeData.debts=financeData.debts.map(x=>x.id===Number(editId)?item:x) : financeData.debts.push(item);
 }
 saveFinance();closeSheets();renderFinance();renderHomeTrainingSummary();
}
function deleteFinance(type,id){
 financeData[type]=financeData[type].filter(x=>x.id!==id);
 saveFinance();renderFinance();renderHomeTrainingSummary();
}
function renderFinance(){
 const income=Number(financeData.monthlyIncome || financeData.income?.amount || 0);
 const mandatory=financeData.mandatoryExpenses.reduce((a,b)=>a+Number(b.amount),0);
 const spent=financeData.expenses.reduce((a,b)=>a+Number(b.amount),0);
 const activeDebt=financeData.debts.filter(d=>d.kind==='Я должен' && d.status!=='Закрыт').reduce((a,b)=>a+Number(b.amount),0);
 const available=Math.max(0,income-mandatory-spent-activeDebt);
 const percent=income?Math.round(available/income*100):0;
 const daily=Math.floor(available/daysLeft());
 document.getElementById('finance-budget-left').textContent=income?available.toLocaleString('ru-RU')+' ₽':'Настрой свой бюджет';
 document.getElementById('finance-day-limit').textContent=income?daily.toLocaleString('ru-RU')+' ₽':'—';
 document.getElementById('finance-percent').textContent=income?percent+'% бюджета осталось':'—';
 document.getElementById('finance-mandatory').innerHTML=financeData.mandatoryExpenses.length?financeData.mandatoryExpenses.map(x=>`<div class="finance-row"><span>${x.title}<small>${x.category||''}</small></span><b>${x.amount} ₽ <button onclick="deleteFinance('mandatoryExpenses',${x.id})">×</button></b></div>`).join(''):'<div class="empty-state">Нет обязательных расходов</div>';
 const cats={}; financeData.expenses.forEach(x=>cats[x.category]=(cats[x.category]||0)+x.amount);
 document.getElementById('finance-cats').innerHTML=Object.keys(cats).length?Object.entries(cats).map(([k,v])=>`<div class="finance-row"><span>${k}</span><b>${v} ₽ (${Math.round(v/spent*100)}%)</b></div>`).join(''):'<div class="empty-state">Расходов пока нет</div>';
 document.getElementById('finance-debts').innerHTML=financeData.debts.length?financeData.debts.map(x=>`<div class="finance-row"><span>${x.title}<small>${x.kind} · ${x.status}</small></span><b>${x.amount} ₽ <button onclick="deleteFinance('debts',${x.id})">×</button></b></div>`).join(''):'<div class="empty-state">Нет долгов</div>';
}


function formatGoalValue(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value ?? '');
  return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 }).format(number);
}

function getGoalProgress(goal) {
  if (!goal || goal.type !== 'numeric') return 0;
  const start = Number(goal.start);
  const current = Number(goal.current);
  const target = Number(goal.target);
  if (![start, current, target].every(Number.isFinite)) return 0;
  if (target === start) return current === target ? 100 : 0;
  const raw = target > start
    ? (current - start) / (target - start)
    : (start - current) / (start - target);
  return Math.max(0, Math.min(100, Math.round(raw * 100)));
}

function buildSmoothPath(coords) {
  if (!coords.length) return '';
  if (coords.length === 1) return `M${coords[0].x} ${coords[0].y}`;
  let path = `M${coords[0].x} ${coords[0].y}`;
  for (let i = 0; i < coords.length - 1; i++) {
    const p0 = coords[i - 1] || coords[i];
    const p1 = coords[i];
    const p2 = coords[i + 1];
    const p3 = coords[i + 2] || p2;
    const cp1x = p1.x + (p2.x - p0.x) / 6;
    const cp1y = p1.y + (p2.y - p0.y) / 6;
    const cp2x = p2.x - (p3.x - p1.x) / 6;
    const cp2y = p2.y - (p3.y - p1.y) / 6;
    path += ` C${cp1x.toFixed(1)} ${cp1y.toFixed(1)} ${cp2x.toFixed(1)} ${cp2y.toFixed(1)} ${p2.x} ${p2.y}`;
  }
  return path;
}

function formatGoalHistoryDate(value, withTime = false) {
  const parsed = value ? new Date(value) : null;
  const time = parsed && !Number.isNaN(parsed.getTime()) ? parsed.getTime() : goalDateToTime(normalizeGoalDate(value));
  if (!Number.isFinite(time)) return String(value || '');
  return new Intl.DateTimeFormat('ru-RU', withTime
    ? { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }
    : { day: 'numeric', month: 'short' }
  ).format(new Date(time));
}


function normalizeGoalColor(value) {
  const normalized = String(value || '').toLowerCase();
  return GOAL_COLORS.find(color => color.toLowerCase() === normalized) || null;
}

function getGoalColor(goal, fallbackIndex = 0) {
  const explicit = normalizeGoalColor(goal?.color);
  if (explicit) return explicit;
  const index = Math.abs(Number(fallbackIndex) || 0) % GOAL_COLORS.length;
  return GOAL_COLORS[index];
}

function getGoalDisplayColor(goal) {
  const active = getActiveGoals();
  const activeIndex = active.findIndex(item => item.id === goal?.id);
  if (activeIndex >= 0) return getGoalColor(goal, activeIndex);
  const archived = getArchivedGoals();
  const archivedIndex = archived.findIndex(item => item.id === goal?.id);
  return getGoalColor(goal, Math.max(0, archivedIndex));
}

function getNextGoalColor() {
  const used = new Set((state.goals || []).filter(goal => !goal.archivedAt).map(goal => normalizeGoalColor(goal?.color)).filter(Boolean));
  return GOAL_COLORS.find(color => !used.has(color)) || GOAL_COLORS[(state.goals || []).length % GOAL_COLORS.length];
}

function renderGoalColorPicker() {
  const picker = document.getElementById('goal-color-picker');
  if (!picker) return;
  if (!selectedGoalColor) selectedGoalColor = getNextGoalColor();
  picker.replaceChildren(...GOAL_COLORS.map(color => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `goal-color-choice${selectedGoalColor === color ? ' is-selected' : ''}`;
    button.style.setProperty('--goal-choice-color', color);
    button.setAttribute('aria-label', `Выбрать цвет ${color}`);
    button.setAttribute('aria-pressed', selectedGoalColor === color ? 'true' : 'false');
    button.addEventListener('click', () => {
      selectedGoalColor = color;
      renderGoalColorPicker();
    });
    return button;
  }));
}

function getGoalRawProgress(goal, value) {
  if (!goal || goal.type !== 'numeric') return 0;
  const start = Number(goal.start);
  const target = Number(goal.target);
  const current = Number(value);
  if (![start, target, current].every(Number.isFinite)) return 0;
  if (target === start) return current === target ? 100 : 0;
  return target > start
    ? ((current - start) / (target - start)) * 100
    : ((start - current) / (start - target)) * 100;
}

function getGoalEntryTime(item, fallbackIndex = 0) {
  const recorded = new Date(item?.recordedAt || '').getTime();
  if (Number.isFinite(recorded)) return recorded;
  const day = goalDateToTime(normalizeGoalDate(item?.date));
  return Number.isFinite(day) ? day + fallbackIndex * 1000 : Number.NaN;
}

function getNumericGoalSeries(goals) {
  return goals
    .map((goal, goalIndex) => {
      if (goal.type !== 'numeric') return null;
      const entries = (Array.isArray(goal.history) ? goal.history : [])
        .map((item, itemIndex) => ({
          value: parseGoalNumber(item?.value),
          time: getGoalEntryTime(item, itemIndex),
          recordedAt: item?.recordedAt || item?.date,
          date: normalizeGoalDate(item?.date || item?.recordedAt)
        }))
        .filter(item => item.value !== null && Number.isFinite(item.time))
        .sort((a, b) => a.time - b.time);

      if (!entries.length) return null;
      return {
        goal,
        goalIndex,
        color: getGoalColor(goal, goalIndex),
        entries
      };
    })
    .filter(Boolean);
}

function renderGoalsChart(goals) {
  const chartWrap = document.querySelector('.goals-widget .goal-chart');
  const svg = chartWrap?.querySelector('svg');
  const guides = chartWrap?.querySelector('.goal-guides');
  const axisLabels = chartWrap?.querySelector('.goal-axis-labels');
  const seriesGroup = document.querySelector('.goals-widget .goal-series-group');
  const range = document.querySelector('.goals-widget .goal-chart-range');
  const rangeStart = document.querySelector('.goals-widget .goal-range-start');
  const rangeEnd = document.querySelector('.goals-widget .goal-range-end');
  if (!chartWrap || !seriesGroup || !svg) return;

  const series = getNumericGoalSeries(goals);
  if (!series.length) {
    chartWrap.hidden = true;
    if (range) range.hidden = true;
    seriesGroup.replaceChildren();
    guides?.replaceChildren();
    axisLabels?.replaceChildren();
    return;
  }

  chartWrap.hidden = false;
  const allEntries = series.flatMap(item => item.entries.map(entry => ({
    ...entry,
    progress: getGoalRawProgress(item.goal, entry.value)
  })));
  const minTime = Math.min(...allEntries.map(item => item.time));
  const maxTime = Math.max(...allEntries.map(item => item.time));
  const timeSpan = Math.max(1, maxTime - minTime);
  const singleInstant = maxTime === minTime;

  const rawMin = Math.min(0, ...allEntries.map(item => item.progress));
  const rawMax = Math.max(100, ...allEntries.map(item => item.progress));
  const yMin = rawMin < 0 ? Math.floor(rawMin / 25) * 25 : 0;
  const yMax = rawMax > 100 ? Math.ceil(rawMax / 25) * 25 : 100;
  const ySpan = Math.max(1, yMax - yMin);
  const left = 32, right = 312, top = 14, bottom = 96;
  const yFor = progress => bottom - ((progress - yMin) / ySpan) * (bottom - top);

  const guideValues = yMin === 0 && yMax === 100
    ? [100, 50, 0]
    : [...new Set([yMax, 100, 0, yMin])].filter(value => value >= yMin && value <= yMax).sort((a, b) => b - a);

  if (guides && axisLabels) {
    const guideFragment = document.createDocumentFragment();
    const labelFragment = document.createDocumentFragment();
    guideValues.forEach(value => {
      const y = Number(yFor(value).toFixed(1));
      const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
      line.setAttribute('x1', String(left));
      line.setAttribute('x2', String(right));
      line.setAttribute('y1', String(y));
      line.setAttribute('y2', String(y));
      if (value === 100) line.classList.add('is-target');
      guideFragment.appendChild(line);

      const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
      label.setAttribute('x', '2');
      label.setAttribute('y', String(y + 3));
      label.textContent = `${Math.round(value)}%`;
      if (value === 100) label.classList.add('is-target');
      labelFragment.appendChild(label);
    });
    guides.replaceChildren(guideFragment);
    axisLabels.replaceChildren(labelFragment);
  }

  const fragment = document.createDocumentFragment();
  const hasSelectedNumericSeries = series.some(item => item.goal.id === selectedGoalId);
  series.forEach((item, seriesIndex) => {
    const coords = item.entries.map(entry => {
      const progress = getGoalRawProgress(item.goal, entry.value);
      const singleOffset = singleInstant ? (seriesIndex - (series.length - 1) / 2) * 12 : 0;
      const x = singleInstant ? 172 + singleOffset : left + ((entry.time - minTime) / timeSpan) * (right - left);
      const y = yFor(progress);
      return { ...entry, x: Number(x.toFixed(1)), y: Number(y.toFixed(1)), progress };
    });

    const isSelected = item.goal.id === selectedGoalId;
    if (isSelected && coords.length > 1) {
      const baselineY = Number(yFor(0).toFixed(1));
      const area = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      area.setAttribute('class', 'goal-series-area');
      area.setAttribute('d', `${buildSmoothPath(coords)} L${coords[coords.length - 1].x} ${baselineY} L${coords[0].x} ${baselineY} Z`);
      area.setAttribute('fill', item.color);
      fragment.appendChild(area);
    }
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('class', `goal-series-line${isSelected ? ' is-selected' : hasSelectedNumericSeries ? ' is-muted' : ''}`);
    if (coords.length === 1) {
      path.setAttribute('d', `M${Math.max(left, coords[0].x - 5)} ${coords[0].y} L${Math.min(right, coords[0].x + 5)} ${coords[0].y}`);
    } else {
      path.setAttribute('d', buildSmoothPath(coords));
    }
    path.setAttribute('stroke', item.color);
    path.setAttribute('data-goal-id', item.goal.id);
    path.setAttribute('tabindex', '0');
    path.setAttribute('role', 'button');
    const selectSeries = () => selectGoal(item.goal.id);
    path.addEventListener('click', selectSeries);
    path.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectSeries(); }
    });
    fragment.appendChild(path);

    coords.forEach(point => {
      const group = document.createElementNS('http://www.w3.org/2000/svg', 'g');
      group.setAttribute('class', `goal-point-group${isSelected ? ' is-selected' : ''}`);
      const hit = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      hit.setAttribute('class', 'goal-point-hit');
      hit.setAttribute('cx', point.x);
      hit.setAttribute('cy', point.y);
      hit.setAttribute('r', '9');
      hit.setAttribute('fill', 'transparent');
      const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      circle.setAttribute('class', 'goal-series-point');
      circle.setAttribute('cx', point.x);
      circle.setAttribute('cy', point.y);
      circle.setAttribute('r', isSelected ? '3.8' : '3.2');
      circle.setAttribute('fill', item.color);
      circle.setAttribute('data-goal-id', item.goal.id);
      const unit = item.goal.unit ? ` ${item.goal.unit}` : '';
      const title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
      title.textContent = `${item.goal.name} · ${formatGoalHistoryDate(point.recordedAt, true)} · ${formatGoalValue(point.value)}${unit} · ${Math.round(point.progress)}%`;
      group.append(hit, circle, title);
      group.setAttribute('tabindex', '0');
      group.setAttribute('role', 'button');
      const announce = () => {
        selectGoal(item.goal.id);
        showToast(`${item.goal.name}: ${formatGoalValue(point.value)}${unit} · ${Math.round(point.progress)}%`);
      };
      group.addEventListener('click', announce);
      group.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); announce(); }
      });
      fragment.appendChild(group);
    });
  });

  seriesGroup.replaceChildren(fragment);
  if (range && rangeStart && rangeEnd) {
    range.hidden = false;
    const minIso = new Date(minTime).toISOString();
    const maxIso = new Date(maxTime).toISOString();
    const sameDay = getDateKey(new Date(minTime)) === getDateKey(new Date(maxTime));
    rangeStart.textContent = formatGoalHistoryDate(minIso, sameDay && !singleInstant);
    if (singleInstant) {
      const count = allEntries.length;
      rangeEnd.textContent = `${count} ${count === 1 ? 'запись' : count < 5 ? 'записи' : 'записей'}`;
    } else {
      rangeEnd.textContent = formatGoalHistoryDate(maxIso, sameDay);
    }
  }
}


function selectGoal(goalId) {
  if (!getGoalById(goalId)) return;
  selectedGoalId = goalId;
  renderGoals();
}

function renderPlan() {
  const plan = document.querySelector('.week-widget');
  if (!plan) return;

  plan.querySelectorAll('.dynamic-plan-row, .empty-plan').forEach(e => e.remove());

  const orderedDays = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
  const entries = orderedDays
    .filter(day => Object.prototype.hasOwnProperty.call(state.plan, day))
    .map(day => [day, state.plan[day]]);

  if (!entries.length) {
    const empty = document.createElement('div');
    empty.className = 'empty-plan';
    const copy = document.createElement('p');
    copy.textContent = 'Настрой свой тренировочный график';
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = 'Создать план';
    button.addEventListener('click', openPlan);
    empty.append(copy, button);
    plan.appendChild(empty);
  } else {
    entries.forEach(([day, workout]) => {
      const row = document.createElement('div');
      row.className = 'workout-line dynamic-plan-row';
      const dayLabel = document.createElement('small');
      dayLabel.textContent = day;
      const workoutLabel = document.createElement('strong');
      workoutLabel.textContent = workout;
      row.append(dayLabel, workoutLabel);
      plan.appendChild(row);
    });
  }

  const button = plan.querySelector('.widget-action');
  if (button) button.onclick = editPlan;
}


function normalizeWorkoutSeconds(value) {
  const seconds = Number(value);
  return Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
}

function getWorkoutElapsedSeconds(workout = state.activeWorkout, nowMs = Date.now()) {
  if (!workout?.started) return 0;
  const startedMs = new Date(workout.started).getTime();
  if (!Number.isFinite(startedMs)) return 0;
  return Math.max(0, Math.floor((nowMs - startedMs) / 1000));
}

function formatWorkoutDuration(seconds) {
  const total = normalizeWorkoutSeconds(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;

  if (hours > 0) {
    return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
  }
  return `${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

function formatClock(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' }).format(date);
}

function clearWorkoutTimer() {
  if (workoutTimerHandle) {
    clearTimeout(workoutTimerHandle);
    workoutTimerHandle = null;
  }
}

function reconcileStaleActiveWorkout(shouldPersist = true) {
  if (!state.activeWorkout?.started) return false;
  const started = new Date(state.activeWorkout.started);
  if (Number.isNaN(started.getTime())) {
    markActiveWorkoutCleared();
    state.activeWorkout = null;
    if (shouldPersist) persist();
    return true;
  }

  const elapsed = getWorkoutElapsedSeconds(state.activeWorkout);
  if (elapsed <= MAX_ACTIVE_WORKOUT_SECONDS) return false;

  // A session may legitimately cross midnight. We only retire it when it has
  // been left running for an implausibly long time, so a late-night workout is
  // not silently lost at 00:00.
  const startedKey = state.activeWorkout.dateKey || getDateKey(started);
  state.attendance[startedKey] = 'started';
  markActiveWorkoutCleared();
  state.activeWorkout = null;
  if (shouldPersist) persist();
  return true;
}


function getDayKey(date = new Date()) {
  const days = ['Вс','Пн','Вт','Ср','Чт','Пт','Сб'];
  return days[date.getDay()];
}

function getDateKey(date = new Date()) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function startOfDay(date = new Date()) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function isSameMonth(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth();
}

function getPlanEffectiveDate() {
  const key = state.planMeta?.effectiveFrom;
  if (!key) return null;
  const date = new Date(`${key}T00:00:00`);
  return Number.isNaN(date.getTime()) ? null : startOfDay(date);
}

function isWorkoutCompletedOnDate(date) {
  const key = getDateKey(date);
  return (state.history || []).some(item => {
    if (item.status !== 'completed') return false;
    if (item.dateKey) return item.dateKey === key;
    const historyDate = new Date(item.date);
    return !Number.isNaN(historyDate.getTime()) && getDateKey(historyDate) === key;
  });
}



function getCalendarStatusLabel(status) {
  if (status.includes('done')) return 'тренировка выполнена';
  if (status.includes('active')) return 'тренировка идёт';
  if (status.includes('started')) return 'тренировка была начата';
  if (status.includes('missed')) return 'тренировка пропущена';
  if (status.includes('scheduled')) return 'тренировка запланирована';
  if (status.includes('rest')) return 'день отдыха';
  if (status.includes('future')) return 'будущий день';
  return 'нет данных';
}


function shiftCalendarMonth(delta) {
  const next = new Date(calendarCursor.getFullYear(), calendarCursor.getMonth() + delta, 1);
  const currentMonth = new Date(new Date().getFullYear(), new Date().getMonth(), 1);

  if (next > currentMonth) return;
  calendarCursor = next;
  renderCalendar();
}

function renderCalendar() {
  const grid = document.querySelector('.attendance-grid');
  if (!grid) return;

  syncAttendanceByDate();

  const now = new Date();
  const year = calendarCursor.getFullYear();
  const month = calendarCursor.getMonth();
  const monthTitle = document.querySelector('.attendance-month');
  const nextButton = document.querySelector('.calendar-next');

  if (monthTitle) {
    monthTitle.textContent = new Intl.DateTimeFormat('ru-RU', {
      month: 'short'
    }).format(calendarCursor).replace('.', '');
  }

  if (nextButton) {
    const currentMonth = new Date(now.getFullYear(), now.getMonth(), 1);
    nextButton.disabled = isSameMonth(calendarCursor, currentMonth);
  }

  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const firstDay = (new Date(year, month, 1).getDay() + 6) % 7; // Monday = 0
  const totalSlots = Math.ceil((firstDay + daysInMonth) / 7) * 7;
  const fragment = document.createDocumentFragment();

  for (let i = 0; i < firstDay; i++) {
    const empty = document.createElement('span');
    empty.className = 'calendar-day empty';
    empty.setAttribute('aria-hidden', 'true');
    fragment.appendChild(empty);
  }

  for (let day = 1; day <= daysInMonth; day++) {
    const date = new Date(year, month, day);
    const status = getCalendarStatus(date);
    const isToday = getDateKey(date) === getDateKey(now);
    const label = getCalendarStatusLabel(status);
    const monthName = new Intl.DateTimeFormat('ru-RU', { month: 'long' }).format(date);

    const dot = document.createElement('span');
    dot.className = `calendar-day ${status}${isToday ? ' today' : ''}`;
    dot.setAttribute('aria-label', `${day} ${monthName}: ${label}`);
    dot.title = `${day} ${monthName}: ${label}`;
    dot.setAttribute('role', 'button');
    dot.tabIndex = 0;
    dot.addEventListener('click', () => showCalendarDayDetails(date, status));
    dot.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        showCalendarDayDetails(date, status);
      }
    });
    fragment.appendChild(dot);
  }

  const usedSlots = firstDay + daysInMonth;
  for (let i = usedSlots; i < totalSlots; i++) {
    const empty = document.createElement('span');
    empty.className = 'calendar-day empty';
    empty.setAttribute('aria-hidden', 'true');
    fragment.appendChild(empty);
  }

  grid.replaceChildren(fragment);
}



function setGoalType(type) {
  selectedGoalType = type === 'text' ? 'text' : 'numeric';
  document.querySelectorAll('[data-goal-type]').forEach(button => {
    button.classList.toggle('is-selected', button.dataset.goalType === selectedGoalType);
  });
  const numeric = document.getElementById('goal-numeric-fields');
  const text = document.getElementById('goal-text-fields');
  if (numeric) numeric.hidden = selectedGoalType !== 'numeric';
  if (text) text.hidden = selectedGoalType !== 'text';
}

function fillGoalSheet(goal = null) {
  editingGoalId = goal?.id || null;
  selectedGoalColor = goal ? getGoalDisplayColor(goal) : getNextGoalColor();
  renderGoalColorPicker();
  const currentInput = document.getElementById('goal-current');
  document.getElementById('goal-name').value = goal?.name || '';
  if (currentInput) {
    currentInput.value = goal?.type === 'numeric' ? goal.start : '';
    currentInput.readOnly = Boolean(goal?.type === 'numeric');
    currentInput.placeholder = 'Стартовое значение';
  }
  document.getElementById('goal-target').value = goal?.type === 'numeric' ? goal.target : '';
  document.getElementById('goal-unit').value = goal?.type === 'numeric' ? goal.unit || '' : '';
  document.getElementById('goal-text').value = goal?.type === 'text' ? goal.text || '' : '';
  setGoalType(goal?.type || 'numeric');
  const deleteButton = document.querySelector('#goal-sheet .sheet-danger-button');
  if (deleteButton) deleteButton.hidden = !goal;
  document.getElementById('goal-sheet').hidden = false;
}

function addGoal() {
  fillGoalSheet(null);
}

function editGoal(goalId) {
  const goal = getGoalById(goalId);
  if (!goal) return;
  selectedGoalId = goal.id;
  fillGoalSheet(normalizeGoal(goal));
}


function editPlan() {
  openPlan();
}

function openGoal() {
  const selected = getGoalById(selectedGoalId);
  if (selected) editGoal(selected.id);
  else addGoal();
}

function saveGoal() {
  const name = document.getElementById('goal-name').value.trim();
  if (!name) {
    showToast('Добавь название цели');
    return;
  }

  const previous = editingGoalId ? normalizeGoal(getGoalById(editingGoalId)) : null;
  const now = new Date().toISOString();
  let nextGoal;

  if (selectedGoalType === 'text') {
    const text = document.getElementById('goal-text').value.trim();
    if (!text) {
      showToast('Опиши желаемый результат');
      return;
    }

    const previousText = previous?.type === 'text' ? previous : null;
    nextGoal = normalizeGoal({
      id: previous?.id || createGoalId(),
      type: 'text',
      name,
      text,
      color: selectedGoalColor || previousText?.color || getNextGoalColor(),
      status: previousText?.status || 'active',
      history: previousText?.history ? [...previousText.history] : [],
      createdAt: previous?.createdAt || now,
      updatedAt: now
    });
  } else {
    const startValue = parseGoalNumber(document.getElementById('goal-current').value);
    const target = parseGoalNumber(document.getElementById('goal-target').value);
    const previousNumeric = previous?.type === 'numeric' ? previous : null;

    if ((!previousNumeric && startValue === null) || target === null) {
      showToast('Укажи стартовое и целевое значение');
      return;
    }

    const initialValue = previousNumeric ? Number(previousNumeric.start) : startValue;
    const history = previousNumeric?.history ? [...previousNumeric.history] : [{
      date: getDateKey(new Date()),
      value: initialValue,
      recordedAt: now
    }];

    nextGoal = normalizeGoal({
      id: previous?.id || createGoalId(),
      type: 'numeric',
      name,
      current: previousNumeric ? Number(previousNumeric.current) : initialValue,
      target,
      start: initialValue,
      unit: document.getElementById('goal-unit').value.trim(),
      color: selectedGoalColor || previousNumeric?.color || getNextGoalColor(),
      history,
      createdAt: previous?.createdAt || now,
      updatedAt: now
    });
  }

  if (previous) {
    state.goals = state.goals.map(goal => goal.id === previous.id ? nextGoal : goal);
  } else {
    state.goals = [...state.goals, nextGoal];
  }
  selectedGoalId = nextGoal.id;
  editingGoalId = null;
  persist();
  closeSheets();
  renderFitness();
  showToast(previous ? 'Цель обновлена' : 'Цель создана');
}




function savePlan(){
  // Freeze past results against the old plan before replacing its template.
  syncAttendanceByDate();
  const newPlan = {};
  document.querySelectorAll('.day-picker').forEach(row => {
    const day = row.dataset.day;
    const input = row.querySelector('.day-input');
    const value = input ? input.value.trim() : '';
    if (value) newPlan[day] = value;
  });

  const days = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
  const now = new Date().toISOString();
  const dayUpdatedAt = { ...(state.planMeta?.dayUpdatedAt || {}) };
  let changed = false;
  days.forEach(day => {
    const before = String(state.plan?.[day] || '');
    const after = String(newPlan?.[day] || '');
    if (before !== after) {
      dayUpdatedAt[day] = now;
      changed = true;
    }
  });

  if (!changed) {
    closeSheets();
    showToast('План без изменений');
    return;
  }

  state.plan = newPlan;
  state.planMeta = {
    effectiveFrom: state.planMeta?.effectiveFrom || getDateKey(new Date()),
    dayUpdatedAt
  };
  persist();
  closeSheets();
  renderFitness();
  showToast('План сохранён');
}



function updateWorkoutTimer() {
  clearWorkoutTimer();

  const el = document.querySelector('.workout-timer');
  if (!el || !state.activeWorkout) return;

  const started = new Date(state.activeWorkout.started);
  if (Number.isNaN(started.getTime())) return;

  const tick = () => {
    if (!state.activeWorkout) {
      clearWorkoutTimer();
      return;
    }

    const nowMs = Date.now();
    const sec = getWorkoutElapsedSeconds(state.activeWorkout, nowMs);
    if (sec > MAX_ACTIVE_WORKOUT_SECONDS) {
      reconcileStaleActiveWorkout(true);
      clearWorkoutTimer();
      renderFitness();
      showToast('Незавершённая тренировка закрыта без добавления времени');
      return;
    }

    el.textContent = formatWorkoutDuration(sec);
    renderActivityChart(nowMs);
    renderHomeTrainingSummary(nowMs);

    const elapsedMilliseconds = Math.max(0, nowMs - started.getTime());
    const millisecondsUntilNextSecond = 1000 - (elapsedMilliseconds % 1000);
    workoutTimerHandle = setTimeout(tick, Math.max(50, millisecondsUntilNextSecond));
  };

  tick();
}

document.addEventListener('DOMContentLoaded', () => {
  if (tg) tg.ready();

  // Startup route: всегда Dashboard.
  // Состояние данных загружается отдельно, но никогда не вызывает навигацию.
  const training = document.getElementById('training-screen');
  if (training) {
    training.hidden = true;
    document.body.classList.remove('training-open');
  }

  closeSheets();
  setupSheetGestures();
  const staleWorkoutChanged = reconcileStaleActiveWorkout(false);
  if (staleWorkoutChanged) persist(); else persistLocal();
  renderFitness();
  syncTrainingWithServer();
});

/* === Training product layer v11 ==========================================
   Cumulative patch on top of v9. It keeps the existing visual language while
   completing one-off planning, workouts, exercises, goal history, weekly
   analytics and conflict-aware synchronization. */

function createEntityId(prefix = 'id') {
  if (globalThis.crypto?.randomUUID) return `${prefix}_${globalThis.crypto.randomUUID()}`;
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 9)}`;
}

function createStableGoalEntryId(goalId, item, index = 0) {
  const existing = String(item?.id || '').trim();
  if (existing) return existing;
  const identity = `${goalId}|${item?.recordedAt || item?.date || ''}|${item?.value ?? item?.note ?? ''}|${index}`;
  return `goalentry_${hashGoalIdentity(identity)}`;
}

function normalizeExerciseSet(set, index = 0) {
  const reps = parseGoalNumber(String(set?.reps ?? ''));
  const weight = parseGoalNumber(String(set?.weight ?? ''));
  return {
    id: String(set?.id || '') || `set_${hashGoalIdentity(`${set?.createdAt || ''}|${reps ?? ''}|${weight ?? ''}|${index}`)}`,
    reps: reps === null ? '' : Math.max(0, Math.floor(reps)),
    weight: weight === null ? '' : Math.max(0, weight),
    createdAt: set?.createdAt || new Date().toISOString()
  };
}

function normalizeExercise(exercise, index = 0) {
  const name = String(exercise?.name || '').trim();
  if (!name) return null;
  return {
    id: String(exercise?.id || '') || `exercise_${hashGoalIdentity(`${name}|${exercise?.createdAt || ''}|${index}`)}`,
    name,
    sets: Array.isArray(exercise?.sets) ? exercise.sets.map(normalizeExerciseSet) : [],
    createdAt: exercise?.createdAt || new Date().toISOString()
  };
}

function normalizeWorkoutRecord(item, index = 0) {
  if (!item || typeof item !== 'object') return null;
  const started = item.started || item.date || null;
  const ended = item.ended || item.date || null;
  const workout = String(item.workout || '').trim() || 'Тренировка';
  const dateKey = item.dateKey || (started ? getDateKey(new Date(started)) : null);
  return {
    ...item,
    id: String(item.id || '') || `session_${hashGoalIdentity(`${started || ''}|${ended || ''}|${workout}|${index}`)}`,
    workout,
    dateKey,
    duration: normalizeWorkoutSeconds(item.duration),
    exercises: Array.isArray(item.exercises) ? item.exercises.map(normalizeExercise).filter(Boolean) : [],
    source: item.source || 'planned'
  };
}

// Preserve archive state and stable entry IDs while staying backward-compatible
// with all goal formats produced by v7-v9.
function normalizeGoal(goal) {
  if (!goal || typeof goal !== 'object') return null;
  const name = String(goal.name || '').trim() || 'Моя цель';
  const declaredType = goal.type === 'text' ? 'text' : goal.type === 'numeric' ? 'numeric' : null;
  const current = parseGoalNumber(goal.current);
  const target = parseGoalNumber(goal.target);
  const type = declaredType || (current !== null && target !== null ? 'numeric' : 'text');
  const fallbackTimestamp = goal.updatedAt || goal.createdAt || new Date().toISOString();
  const updatedAt = goal.updatedAt || fallbackTimestamp;
  const id = getStableGoalId(goal, fallbackTimestamp);
  const archivedAt = goal.archivedAt || null;
  const color = normalizeGoalColor(goal.color);

  if (type === 'text') {
    const targetText = String(goal.targetText || goal.text || [goal.current, goal.target]
      .filter(value => value !== undefined && value !== null && String(value).trim())
      .map(String)
      .join(' → ') || '').trim();
    const fallbackStatus = goal.status === 'completed' ? 'completed' : 'active';
    const rawHistory = normalizeTextGoalHistory(goal, targetText, fallbackStatus);
    const history = rawHistory.map((item, index) => ({
      ...item,
      id: createStableGoalEntryId(id, item, index)
    }));
    const latest = history[history.length - 1];
    const latestWithNote = [...history].reverse().find(item => String(item?.note || '').trim());
    return {
      id,
      type: 'text',
      name,
      text: targetText,
      status: latest?.status === 'completed' ? 'completed' : fallbackStatus,
      lastResult: String(latestWithNote?.note || '').trim(),
      history,
      color,
      archivedAt,
      createdAt: goal.createdAt || history[0]?.recordedAt || fallbackTimestamp,
      updatedAt
    };
  }

  const safeCurrent = current ?? 0;
  const safeTarget = target ?? safeCurrent;
  const rawHistory = normalizeNumericGoalHistory(goal, safeCurrent);
  const history = rawHistory.map((item, index) => ({
    ...item,
    id: createStableGoalEntryId(id, item, index),
    kind: item.kind || (index === 0 ? 'baseline' : 'result')
  }));
  if (history[0]) history[0].kind = 'baseline';
  const latest = history[history.length - 1];
  const first = history[0];
  const start = parseGoalNumber(goal.start) ?? first?.value ?? safeCurrent;
  const latestValue = latest?.value ?? safeCurrent;
  const denominator = safeTarget - start;
  const progress = denominator === 0
    ? (latestValue === safeTarget ? 100 : 0)
    : ((latestValue - start) / denominator) * 100;
  const status = progress >= 100 ? 'completed' : 'active';
  return {
    id,
    type: 'numeric',
    name,
    current: latestValue,
    target: safeTarget,
    start,
    unit: String(goal.unit || '').trim(),
    status,
    history,
    color,
    archivedAt,
    createdAt: goal.createdAt || history[0]?.recordedAt || fallbackTimestamp,
    updatedAt
  };
}

function normalizeTrainingState(raw = {}) {
  const goals = Array.isArray(raw.goals) ? raw.goals.map(normalizeGoal).filter(Boolean) : [];
  const history = Array.isArray(raw.history) ? raw.history.map(normalizeWorkoutRecord).filter(Boolean) : [];
  const activeWorkout = raw.activeWorkout && typeof raw.activeWorkout === 'object'
    ? normalizeWorkoutRecord({ ...raw.activeWorkout, status: 'active' })
    : null;
  const rawSync = raw.sync && typeof raw.sync === 'object' ? raw.sync : {};
  const tombstones = rawSync.tombstones && typeof rawSync.tombstones === 'object'
    ? rawSync.tombstones
    : { goals: {}, goalEntries: {}, planOverrides: {} };
  const rawPlanMeta = raw.planMeta && typeof raw.planMeta === 'object' ? raw.planMeta : {};
  return {
    goals,
    plan: raw.plan && typeof raw.plan === 'object' && !Array.isArray(raw.plan) ? raw.plan : {},
    planOverrides: raw.planOverrides && typeof raw.planOverrides === 'object' && !Array.isArray(raw.planOverrides) ? raw.planOverrides : {},
    history,
    attendance: raw.attendance && typeof raw.attendance === 'object' && !Array.isArray(raw.attendance) ? raw.attendance : {},
    planMeta: {
      effectiveFrom: rawPlanMeta.effectiveFrom || null,
      dayUpdatedAt: rawPlanMeta.dayUpdatedAt && typeof rawPlanMeta.dayUpdatedAt === 'object' ? rawPlanMeta.dayUpdatedAt : {}
    },
    activeWorkout,
    sync: {
      revision: Number(rawSync.revision) || 0,
      updatedAt: rawSync.updatedAt || null,
      resetAt: rawSync.resetAt || null,
      activeWorkoutClearedAt: rawSync.activeWorkoutClearedAt || null,
      deviceId: rawSync.deviceId || null,
      tombstones: {
        goals: tombstones.goals || {},
        goalEntries: tombstones.goalEntries || {},
        planOverrides: tombstones.planOverrides || {}
      }
    }
  };
}

STORAGE.planOverrides = 'fitness_plan_overrides_v1';
STORAGE.sync = 'fitness_sync_meta_v1';
const V10_DEVICE_KEY = 'fitness_device_id_v1';
let trainingServerUpdatedAt = localStorage.getItem('fitness_server_updated_at_v1') || null;
let trainingRetryHandle = null;
let activityWeekCursor = null;
let editingGoalHistoryEntryId = null;

if (!state.planOverrides || !Object.keys(state.planOverrides).length) {
  const localOverrides = readJSON(STORAGE.planOverrides, {});
  if (localOverrides && typeof localOverrides === 'object' && !Array.isArray(localOverrides)) state.planOverrides = localOverrides;
}
if (!state.sync?.updatedAt) {
  const localSync = readJSON(STORAGE.sync, null);
  if (localSync && typeof localSync === 'object') state.sync = { ...state.sync, ...localSync };
}
if (!localStorage.getItem(V10_DEVICE_KEY)) localStorage.setItem(V10_DEVICE_KEY, createEntityId('device'));
state.sync = state.sync || { revision: 0, updatedAt: null, resetAt: null, deviceId: null, tombstones: { goals: {}, goalEntries: {}, planOverrides: {} } };
state.sync.deviceId = state.sync.deviceId || localStorage.getItem(V10_DEVICE_KEY);

function touchTrainingState() {
  state.sync = state.sync || { revision: 0, updatedAt: null, resetAt: null, deviceId: null, tombstones: { goals: {}, goalEntries: {}, planOverrides: {} } };
  state.sync.revision = (Number(state.sync.revision) || 0) + 1;
  state.sync.updatedAt = new Date().toISOString();
  state.sync.deviceId = state.sync.deviceId || localStorage.getItem(V10_DEVICE_KEY);
}

function getTrainingPayload() {
  return {
    goals: state.goals,
    plan: state.plan,
    planOverrides: state.planOverrides || {},
    history: state.history,
    attendance: state.attendance,
    planMeta: state.planMeta,
    activeWorkout: state.activeWorkout,
    sync: state.sync
  };
}

function persistLocal() {
  localStorage.setItem(STORAGE.goals, JSON.stringify(state.goals));
  localStorage.setItem(STORAGE.plan, JSON.stringify(state.plan));
  localStorage.setItem(STORAGE.planOverrides, JSON.stringify(state.planOverrides || {}));
  localStorage.setItem(STORAGE.history, JSON.stringify(state.history));
  localStorage.setItem(STORAGE.attendance, JSON.stringify(state.attendance));
  localStorage.setItem(STORAGE.planMeta, JSON.stringify(state.planMeta));
  localStorage.setItem(STORAGE.activeWorkout, JSON.stringify(state.activeWorkout));
  localStorage.setItem(STORAGE.sync, JSON.stringify(state.sync || {}));
}

function setTrainingSyncStatus(status, text) {
  const el = document.querySelector('.training-sync-status');
  if (!el) return;
  el.dataset.status = status;
  el.textContent = text;
}

function persist(options = {}) {
  if (options.touch !== false) touchTrainingState();
  persistLocal();
  if (options.remote !== false) {
    localStorage.setItem(STORAGE.trainingDirty, '1');
    scheduleTrainingSave();
  }
}

function compareIso(a, b) {
  const at = new Date(a || 0).getTime();
  const bt = new Date(b || 0).getTime();
  return (Number.isFinite(at) ? at : 0) - (Number.isFinite(bt) ? bt : 0);
}

function mergeTombstoneMaps(local = {}, remote = {}) {
  const merged = { ...remote };
  Object.entries(local || {}).forEach(([key, value]) => {
    if (!merged[key] || compareIso(value, merged[key]) >= 0) merged[key] = value;
  });
  return merged;
}

function getMergedTombstones(localSync = {}, remoteSync = {}) {
  return {
    goals: mergeTombstoneMaps(localSync?.tombstones?.goals, remoteSync?.tombstones?.goals),
    goalEntries: mergeTombstoneMaps(localSync?.tombstones?.goalEntries, remoteSync?.tombstones?.goalEntries),
    planOverrides: mergeTombstoneMaps(localSync?.tombstones?.planOverrides, remoteSync?.tombstones?.planOverrides)
  };
}

function recordSyncTombstone(kind, id) {
  if (!id) return;
  state.sync = state.sync || { revision: 0, updatedAt: null, resetAt: null, deviceId: null, tombstones: {} };
  state.sync.tombstones = state.sync.tombstones || {};
  state.sync.tombstones[kind] = state.sync.tombstones[kind] || {};
  state.sync.tombstones[kind][id] = new Date().toISOString();
}


function markActiveWorkoutCleared(at = new Date().toISOString()) {
  state.sync = state.sync || { revision: 0, updatedAt: null, resetAt: null, deviceId: null, tombstones: {} };
  if (!state.sync.activeWorkoutClearedAt || compareIso(at, state.sync.activeWorkoutClearedAt) > 0) {
    state.sync.activeWorkoutClearedAt = at;
  }
}

function touchActiveWorkout() {
  if (!state.activeWorkout) return;
  state.activeWorkout.updatedAt = new Date().toISOString();
}

function mergeActiveWorkout(local, remote, mergedHistory) {
  const completedIds = new Set((mergedHistory || []).filter(item => item.status === 'completed').map(item => item.id));
  const clearAt = [local.sync?.activeWorkoutClearedAt, remote.sync?.activeWorkoutClearedAt]
    .filter(Boolean)
    .sort((a, b) => compareIso(a, b))
    .pop() || null;
  const candidates = [local.activeWorkout, remote.activeWorkout]
    .filter(Boolean)
    .map(item => normalizeWorkoutRecord(item))
    .filter(item => item && !completedIds.has(item.id))
    .filter(item => !clearAt || compareIso(item.updatedAt || item.started, clearAt) > 0)
    .sort((a, b) => compareIso(a.updatedAt || a.started, b.updatedAt || b.started));
  return candidates[candidates.length - 1] || null;
}

function mergeGoalCollections(localGoals, remoteGoals, tombstones = {}) {
  const byId = new Map();
  [...(remoteGoals || []), ...(localGoals || [])].forEach(rawGoal => {
    const goal = normalizeGoal(rawGoal);
    if (!goal) return;
    const previous = byId.get(goal.id);
    if (!previous) {
      byId.set(goal.id, goal);
      return;
    }
    const newer = compareIso(goal.updatedAt, previous.updatedAt) >= 0 ? goal : previous;
    const older = newer === goal ? previous : goal;
    const historyById = new Map();
    [...(older.history || []), ...(newer.history || [])].forEach((entry, index) => {
      const id = String(entry?.id || '') || createStableGoalEntryId(goal.id, entry, index);
      const previousEntry = historyById.get(id);
      if (!previousEntry || compareIso(entry?.updatedAt || entry?.recordedAt, previousEntry?.updatedAt || previousEntry?.recordedAt) >= 0) {
        historyById.set(id, { ...entry, id });
      }
    });
    byId.set(goal.id, normalizeGoal({ ...newer, history: [...historyById.values()] }));
  });
  return [...byId.values()]
    .filter(goal => !tombstones.goals?.[goal.id] || compareIso(goal.updatedAt, tombstones.goals[goal.id]) > 0)
    .map(goal => normalizeGoal({
      ...goal,
      history: (goal.history || []).filter(entry => !tombstones.goalEntries?.[entry.id] || compareIso(entry.updatedAt || entry.recordedAt, tombstones.goalEntries[entry.id]) > 0)
    }));
}

function mergeWorkoutHistory(localHistory, remoteHistory) {
  const byId = new Map();
  [...(remoteHistory || []), ...(localHistory || [])].forEach((raw, index) => {
    const session = normalizeWorkoutRecord(raw, index);
    if (!session) return;
    const previous = byId.get(session.id);
    if (!previous || compareIso(session.ended || session.date, previous.ended || previous.date) >= 0) {
      byId.set(session.id, session);
    }
  });
  return [...byId.values()].sort((a, b) => compareIso(a.started || a.date, b.started || b.date));
}

function mergeAttendance(localValue = {}, remoteValue = {}, history = [], activeWorkout = null) {
  const priority = { done: 6, started: 4, missed: 3, scheduled: 2, rest: 1, untracked: 0 };
  const merged = { ...remoteValue };
  Object.entries(localValue || {}).forEach(([key, status]) => {
    if (status === 'active') return;
    if (!(key in merged) || (priority[status] ?? 0) >= (priority[merged[key]] ?? 0)) merged[key] = status;
  });
  Object.keys(merged).forEach(key => {
    if (merged[key] === 'active') delete merged[key];
  });
  (history || []).forEach(item => {
    if (item.status === 'completed' && item.dateKey) merged[item.dateKey] = 'done';
  });
  if (activeWorkout?.started) {
    const activeKey = activeWorkout.dateKey || getDateKey(new Date(activeWorkout.started));
    if (activeKey) merged[activeKey] = 'active';
  }
  return merged;
}

function mergePlanOverrides(localValue = {}, remoteValue = {}, tombstones = {}) {
  const merged = { ...remoteValue };
  Object.entries(localValue || {}).forEach(([dateKey, item]) => {
    const remoteItem = merged[dateKey];
    if (!remoteItem || compareIso(item?.updatedAt, remoteItem?.updatedAt) >= 0) merged[dateKey] = item;
  });
  Object.entries(tombstones.planOverrides || {}).forEach(([dateKey, deletedAt]) => {
    if (!merged[dateKey] || compareIso(merged[dateKey]?.updatedAt, deletedAt) <= 0) delete merged[dateKey];
  });
  return merged;
}


function mergeWeeklyPlan(local, remote, preferLocal = true) {
  const days = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
  const plan = {};
  const dayUpdatedAt = {};
  days.forEach(day => {
    const localHasDayTime = Object.prototype.hasOwnProperty.call(local.planMeta?.dayUpdatedAt || {}, day);
    const remoteHasDayTime = Object.prototype.hasOwnProperty.call(remote.planMeta?.dayUpdatedAt || {}, day);
    const localAt = localHasDayTime ? local.planMeta.dayUpdatedAt[day] : (local.sync?.updatedAt || '');
    const remoteAt = remoteHasDayTime ? remote.planMeta.dayUpdatedAt[day] : (remote.sync?.updatedAt || '');
    let useLocal;
    if (localHasDayTime !== remoteHasDayTime) useLocal = localHasDayTime;
    else {
      const comparison = compareIso(localAt, remoteAt);
      useLocal = comparison > 0 || (comparison === 0 && preferLocal);
    }
    const source = useLocal ? local : remote;
    const chosenAt = useLocal ? localAt : remoteAt;
    if (Object.prototype.hasOwnProperty.call(source.plan || {}, day)) plan[day] = source.plan[day];
    if (chosenAt) dayUpdatedAt[day] = chosenAt;
  });
  const effectiveCandidates = [local.planMeta?.effectiveFrom, remote.planMeta?.effectiveFrom].filter(Boolean).sort();
  return {
    plan,
    planMeta: {
      effectiveFrom: effectiveCandidates[0] || null,
      dayUpdatedAt
    }
  };
}

function mergeTrainingStates(localRaw, remoteRaw) {
  const local = normalizeTrainingState(localRaw);
  const remote = normalizeTrainingState(remoteRaw);
  if (remote.sync?.resetAt && compareIso(remote.sync.resetAt, local.sync?.updatedAt) >= 0) return remote;
  if (local.sync?.resetAt && compareIso(local.sync.resetAt, remote.sync?.updatedAt) >= 0) return local;

  const localIsNewer = compareIso(local.sync?.updatedAt, remote.sync?.updatedAt) >= 0;
  const newer = localIsNewer ? local : remote;
  const older = localIsNewer ? remote : local;
  const tombstones = getMergedTombstones(local.sync, remote.sync);
  const weeklyPlan = mergeWeeklyPlan(local, remote, localIsNewer);
  const history = mergeWorkoutHistory(local.history, remote.history);
  const activeWorkout = mergeActiveWorkout(local, remote, history);
  const activeWorkoutClearedAt = [local.sync?.activeWorkoutClearedAt, remote.sync?.activeWorkoutClearedAt]
    .filter(Boolean)
    .sort((a, b) => compareIso(a, b))
    .pop() || null;

  const merged = {
    goals: mergeGoalCollections(local.goals, remote.goals, tombstones),
    plan: weeklyPlan.plan,
    planOverrides: mergePlanOverrides(local.planOverrides, remote.planOverrides, tombstones),
    history,
    attendance: mergeAttendance(local.attendance, remote.attendance, history, activeWorkout),
    planMeta: weeklyPlan.planMeta,
    activeWorkout,
    sync: {
      revision: Math.max(Number(local.sync?.revision) || 0, Number(remote.sync?.revision) || 0) + 1,
      updatedAt: new Date().toISOString(),
      resetAt: [local.sync?.resetAt, remote.sync?.resetAt].filter(Boolean).sort((a, b) => compareIso(a, b)).pop() || null,
      activeWorkoutClearedAt,
      deviceId: localStorage.getItem(V10_DEVICE_KEY),
      tombstones
    }
  };
  return normalizeTrainingState(merged);
}

function scheduleSyncRetry() {
  clearTimeout(trainingRetryHandle);
  if (!navigator.onLine) return;
  trainingRetryHandle = setTimeout(() => syncTrainingWithServer(), 4000);
}

async function saveTrainingToServer(retryConflict = true) {
  if (!tg?.initData) {
    setTrainingSyncStatus('local', 'Локально');
    return false;
  }
  if (!navigator.onLine) {
    localStorage.setItem(STORAGE.trainingDirty, '1');
    setTrainingSyncStatus('offline', 'Офлайн · сохранено локально');
    return false;
  }
  setTrainingSyncStatus('saving', 'Синхронизация…');
  try {
    const response = await fetch('/api/training/state', {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        'X-Telegram-Init-Data': tg.initData
      },
      body: JSON.stringify({ state: getTrainingPayload(), baseUpdatedAt: trainingServerUpdatedAt })
    });

    if (response.status === 409 && retryConflict) {
      const conflict = await response.json();
      Object.assign(state, mergeTrainingStates(state, conflict.state || {}));
      trainingServerUpdatedAt = conflict.updated_at || null;
      if (trainingServerUpdatedAt) localStorage.setItem('fitness_server_updated_at_v1', trainingServerUpdatedAt);
      persistLocal();
      localStorage.setItem(STORAGE.trainingDirty, '1');
      renderFitness();
      return saveTrainingToServer(false);
    }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    trainingServerReady = true;
    trainingServerUpdatedAt = payload.updated_at || trainingServerUpdatedAt;
    if (trainingServerUpdatedAt) localStorage.setItem('fitness_server_updated_at_v1', trainingServerUpdatedAt);
    localStorage.removeItem(STORAGE.trainingDirty);
    setTrainingSyncStatus('saved', 'Сохранено');
    return true;
  } catch (_) {
    trainingServerReady = false;
    localStorage.setItem(STORAGE.trainingDirty, '1');
    setTrainingSyncStatus(navigator.onLine ? 'error' : 'offline', navigator.onLine ? 'Нет связи · повторим' : 'Офлайн · сохранено локально');
    scheduleSyncRetry();
    return false;
  }
}

function scheduleTrainingSave() {
  if (!tg?.initData) {
    setTrainingSyncStatus('local', 'Локально');
    return;
  }
  clearTimeout(trainingSaveTimer);
  setTrainingSyncStatus(navigator.onLine ? 'saving' : 'offline', navigator.onLine ? 'Синхронизация…' : 'Офлайн · сохранено локально');
  trainingSaveTimer = setTimeout(() => saveTrainingToServer(), 350);
}

async function syncTrainingWithServer() {
  if (!tg?.initData || trainingSyncInFlight) {
    if (!tg?.initData) setTrainingSyncStatus('local', 'Локально');
    return false;
  }
  if (!navigator.onLine) {
    setTrainingSyncStatus('offline', 'Офлайн · сохранено локально');
    return false;
  }
  trainingSyncInFlight = true;
  setTrainingSyncStatus('saving', 'Синхронизация…');
  try {
    const response = await fetch('/api/training/state', {
      headers: { 'X-Telegram-Init-Data': tg.initData }
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    trainingServerReady = true;
    trainingServerUpdatedAt = payload.updated_at || null;
    if (trainingServerUpdatedAt) localStorage.setItem('fitness_server_updated_at_v1', trainingServerUpdatedAt);

    const localDirty = localStorage.getItem(STORAGE.trainingDirty) === '1';
    const remoteResetWins = payload.state?.sync?.resetAt && compareIso(payload.state.sync.resetAt, state.sync?.updatedAt) >= 0;
    if (!payload.exists || !payload.state) {
      await saveTrainingToServer();
    } else if (remoteResetWins) {
      Object.assign(state, normalizeTrainingState(payload.state));
      persistLocal();
      localStorage.removeItem(STORAGE.trainingDirty);
      setTrainingSyncStatus('saved', 'Сохранено');
    } else if (localDirty || compareIso(state.sync?.updatedAt, payload.state?.sync?.updatedAt) > 0) {
      Object.assign(state, mergeTrainingStates(state, payload.state));
      persistLocal();
      localStorage.setItem(STORAGE.trainingDirty, '1');
      await saveTrainingToServer();
    } else {
      Object.assign(state, normalizeTrainingState(payload.state));
      reconcileStaleActiveWorkout(false);
      persistLocal();
      localStorage.removeItem(STORAGE.trainingDirty);
      setTrainingSyncStatus('saved', 'Сохранено');
    }
    renderFitness();
    return true;
  } catch (_) {
    trainingServerReady = false;
    setTrainingSyncStatus(navigator.onLine ? 'error' : 'offline', navigator.onLine ? 'Нет связи · повторим' : 'Офлайн · сохранено локально');
    scheduleSyncRetry();
    return false;
  } finally {
    trainingSyncInFlight = false;
  }
}

window.addEventListener('online', () => syncTrainingWithServer());
window.addEventListener('offline', () => setTrainingSyncStatus('offline', 'Офлайн · сохранено локально'));

document.addEventListener('visibilitychange', () => {
  if (!document.hidden && localStorage.getItem(STORAGE.trainingDirty) === '1') syncTrainingWithServer();
});

function getBaseWorkoutForDate(date) {
  return state.plan?.[getDayKey(date)] || '';
}

function getPlanOverride(date) {
  return state.planOverrides?.[getDateKey(date)] || null;
}

function getPlannedWorkoutForDate(date) {
  const override = getPlanOverride(date);
  if (override?.type === 'cancel') return '';
  if (override?.type === 'workout') return String(override.workout || '').trim();
  return getBaseWorkoutForDate(date);
}

function formatShortDate(value) {
  const date = value instanceof Date ? value : new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return String(value || '');
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short' }).format(date);
}

function clearPlanOverride(dateKey, silent = false, recordDeletion = true) {
  const override = state.planOverrides?.[dateKey];
  if (!override) return;
  const keys = new Set([dateKey]);
  if (override.movedTo && state.planOverrides?.[override.movedTo]?.movedFrom === dateKey) keys.add(override.movedTo);
  if (override.movedFrom && state.planOverrides?.[override.movedFrom]?.movedTo === dateKey) keys.add(override.movedFrom);
  keys.forEach(key => {
    if (recordDeletion) recordSyncTombstone('planOverrides', key);
    delete state.planOverrides[key];
  });
  persist();
  renderPlanExceptionsList();
  renderFitness();
  if (!silent) showToast('Разовое изменение удалено');
}

function togglePlanExceptionFields() {
  const action = document.getElementById('plan-exception-action')?.value;
  const target = document.getElementById('plan-move-date');
  if (target) target.hidden = action !== 'move';
}

function savePlanException() {
  const sourceInput = document.getElementById('plan-exception-date');
  const action = document.getElementById('plan-exception-action')?.value || 'cancel';
  const sourceKey = sourceInput?.value;
  if (!sourceKey) { showToast('Выбери дату'); return; }

  const sourceDate = new Date(`${sourceKey}T12:00:00`);
  const today = startOfDay(new Date());
  if (startOfDay(sourceDate) < today) { showToast('Прошедший план менять нельзя'); return; }
  const sourceOverride = state.planOverrides?.[sourceKey];
  if (sourceOverride?.movedFrom) {
    showToast('Измени перенос через его исходную дату');
    return;
  }
  const workout = getPlannedWorkoutForDate(sourceDate) || getBaseWorkoutForDate(sourceDate);
  if (!workout || workout === 'Отдых') { showToast('На эту дату нет тренировки'); return; }

  let targetKey = null;
  if (action === 'move') {
    targetKey = document.getElementById('plan-move-date')?.value;
    if (!targetKey || targetKey === sourceKey) { showToast('Выбери другую дату переноса'); return; }
    const targetDate = new Date(`${targetKey}T12:00:00`);
    if (startOfDay(targetDate) < today) { showToast('Нельзя переносить в прошлое'); return; }
    const targetOverride = state.planOverrides?.[targetKey];
    const belongsToCurrentMove = targetOverride?.movedFrom === sourceKey;
    if (targetOverride && !belongsToCurrentMove) {
      showToast('На новую дату уже есть разовое изменение');
      return;
    }
    const targetWorkout = getPlannedWorkoutForDate(targetDate);
    if (!belongsToCurrentMove && targetWorkout && targetWorkout !== 'Отдых') {
      showToast('На новую дату уже есть тренировка');
      return;
    }
  }

  // Validation is complete. Only now mutate the override map, so a typo in the
  // new date can never erase a previously valid transfer.
  state.planOverrides = state.planOverrides || {};
  const removeKeys = new Set();
  const collectPair = key => {
    const item = state.planOverrides?.[key];
    if (!item) return;
    removeKeys.add(key);
    if (item.movedTo && state.planOverrides?.[item.movedTo]?.movedFrom === key) removeKeys.add(item.movedTo);
    if (item.movedFrom && state.planOverrides?.[item.movedFrom]?.movedTo === key) removeKeys.add(item.movedFrom);
  };
  collectPair(sourceKey);
  if (targetKey) collectPair(targetKey);
  removeKeys.forEach(key => {
    if (key !== sourceKey && key !== targetKey) recordSyncTombstone('planOverrides', key);
    delete state.planOverrides[key];
  });

  const now = new Date().toISOString();
  if (action === 'move') {
    state.planOverrides[sourceKey] = { type: 'cancel', workout, movedTo: targetKey, updatedAt: now };
    state.planOverrides[targetKey] = { type: 'workout', workout, movedFrom: sourceKey, updatedAt: now };
    showToast(`Перенесено на ${formatShortDate(targetKey)}`);
  } else {
    state.planOverrides[sourceKey] = { type: 'cancel', workout, updatedAt: now };
    showToast('Тренировка отменена на выбранную дату');
  }
  persist();
  renderPlanExceptionsList();
  renderFitness();
}

function renderPlanExceptionsList() {
  const list = document.querySelector('.plan-exception-list');
  if (!list) return;
  list.replaceChildren();
  const todayKey = getDateKey(new Date());
  const entries = Object.entries(state.planOverrides || {})
    .filter(([dateKey, item]) => dateKey >= todayKey && !item?.movedFrom)
    .sort(([a], [b]) => a.localeCompare(b))
    .slice(0, 5);
  entries.forEach(([dateKey, item]) => {
    const row = document.createElement('div');
    row.className = 'plan-exception-row';
    const copy = document.createElement('span');
    const strong = document.createElement('strong');
    strong.textContent = item?.movedTo ? `${formatShortDate(dateKey)} → ${formatShortDate(item.movedTo)}` : formatShortDate(dateKey);
    const small = document.createElement('small');
    small.textContent = item?.movedTo ? `Перенос · ${item.workout}` : `Отменено · ${item.workout || 'Тренировка'}`;
    copy.append(strong, small);
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.textContent = '×';
    remove.setAttribute('aria-label', 'Удалить разовое изменение');
    remove.addEventListener('click', () => clearPlanOverride(dateKey));
    row.append(copy, remove);
    list.appendChild(row);
  });
}

function openPlan() {
  const sheet = document.getElementById('plan-sheet');
  if (!sheet) return;
  document.querySelectorAll('.day-picker').forEach(row => {
    const day = row.dataset.day;
    const input = row.querySelector('.day-input');
    if (input) input.value = state.plan[day] || '';
  });
  const source = document.getElementById('plan-exception-date');
  const target = document.getElementById('plan-move-date');
  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  if (source && !source.value) source.value = getDateKey(tomorrow);
  if (target && !target.value) {
    const dayAfter = new Date(tomorrow);
    dayAfter.setDate(dayAfter.getDate() + 1);
    target.value = getDateKey(dayAfter);
  }
  togglePlanExceptionFields();
  renderPlanExceptionsList();
  sheet.hidden = false;
}

function syncAttendanceByDate() {
  const effectiveFrom = getPlanEffectiveDate();
  if (!effectiveFrom) return;
  const today = startOfDay(new Date());
  const cursor = new Date(effectiveFrom);
  const oldestAllowed = new Date(today);
  oldestAllowed.setDate(oldestAllowed.getDate() - 366);
  if (cursor < oldestAllowed) cursor.setTime(oldestAllowed.getTime());
  let changed = false;
  while (cursor < today) {
    const key = getDateKey(cursor);
    const workout = getPlannedWorkoutForDate(cursor);
    if (isWorkoutCompletedOnDate(cursor)) {
      if (state.attendance[key] !== 'done') {
        state.attendance[key] = 'done';
        changed = true;
      }
    } else if (workout && workout !== 'Отдых' && !state.attendance[key]) {
      state.attendance[key] = 'missed';
      changed = true;
    }
    cursor.setDate(cursor.getDate() + 1);
  }
  if (changed) persist();
}

function getCalendarStatus(date) {
  const today = startOfDay(new Date());
  const current = startOfDay(date);
  const key = getDateKey(current);
  const recordedStatus = state.attendance?.[key];
  const workout = getPlannedWorkoutForDate(current);
  const effectiveFrom = getPlanEffectiveDate();
  if (isWorkoutCompletedOnDate(current) || recordedStatus === 'done') return 'done';
  if (recordedStatus === 'missed' && current < today) return 'missed';
  if (recordedStatus === 'started') return 'started';
  if (state.activeWorkout) {
    const started = new Date(state.activeWorkout.started);
    if (!Number.isNaN(started.getTime()) && getDateKey(started) === key) return 'active';
  }
  if (current > today) return workout && workout !== 'Отдых' ? 'future scheduled' : 'future';
  if (current.getTime() === today.getTime()) return workout && workout !== 'Отдых' ? 'scheduled' : 'rest';
  if (!effectiveFrom || current < effectiveFrom) return recordedStatus || 'untracked';
  if (!workout || workout === 'Отдых') return 'rest';
  return 'missed';
}

function showCalendarDayDetails(date, status) {
  const dateLabel = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short' }).format(date);
  const workout = getPlannedWorkoutForDate(date);
  const sessions = getCompletedWorkoutsForDate(date);
  const override = getPlanOverride(date);
  if (sessions.length) {
    const total = sessions.reduce((sum, item) => sum + normalizeWorkoutSeconds(item.duration), 0);
    const label = sessions.length === 1 ? sessions[0].workout : `${sessions.length} тренировки`;
    showToast(`${dateLabel} · ${label} · ${formatWorkoutDuration(total)}`);
    return;
  }
  if (status.includes('active') && state.activeWorkout) {
    showToast(`${dateLabel} · ${state.activeWorkout.workout || workout || 'Тренировка'} · ${formatWorkoutDuration(getWorkoutElapsedSeconds())}`);
    return;
  }
  if (override?.movedTo) {
    showToast(`${dateLabel} · Перенесено на ${formatShortDate(override.movedTo)}`);
    return;
  }
  if (override?.type === 'cancel') {
    showToast(`${dateLabel} · Тренировка отменена`);
    return;
  }
  if (override?.movedFrom) {
    showToast(`${dateLabel} · Перенесено с ${formatShortDate(override.movedFrom)} · ${override.workout}`);
    return;
  }
  if (status.includes('missed')) showToast(`${dateLabel} · Пропущено${workout ? `: ${workout}` : ''}`);
  else if (status.includes('scheduled')) showToast(`${dateLabel} · Запланировано${workout ? `: ${workout}` : ''}`);
  else if (status.includes('rest')) showToast(`${dateLabel} · День отдыха`);
  else showToast(`${dateLabel} · ${getCalendarStatusLabel(status)}`);
}

function getCompletedWorkoutsForDate(date = new Date()) {
  const key = getDateKey(date);
  return (state.history || []).filter(item => {
    if (item.status !== 'completed') return false;
    if (item.dateKey) return item.dateKey === key;
    const historyDate = new Date(item.date);
    return !Number.isNaN(historyDate.getTime()) && getDateKey(historyDate) === key;
  });
}

function getCompletedWorkoutForDate(date = new Date()) {
  const sessions = getCompletedWorkoutsForDate(date);
  return sessions[sessions.length - 1] || null;
}

function openCustomWorkout() {
  if (state.activeWorkout) {
    showToast('Сначала заверши текущую тренировку');
    return;
  }
  const input = document.getElementById('custom-workout-name');
  if (input) input.value = '';
  const sheet = document.getElementById('custom-workout-sheet');
  if (sheet) sheet.hidden = false;
  setTimeout(() => input?.focus(), 80);
}

function startCustomWorkout() {
  const name = document.getElementById('custom-workout-name')?.value.trim();
  if (!name) {
    showToast('Назови тренировку');
    return;
  }
  closeSheets();
  startWorkout(name, 'custom');
}

function startWorkout(customName = null, source = 'planned') {
  const now = new Date();
  if (state.activeWorkout) { showToast('Тренировка уже идёт'); return; }
  const planned = getPlannedWorkoutForDate(now);
  const workout = String(customName || planned || '').trim();
  if (!workout || workout === 'Отдых') { openCustomWorkout(); return; }
  state.activeWorkout = normalizeWorkoutRecord({
    id: createEntityId('session'),
    started: now.toISOString(),
    updatedAt: now.toISOString(),
    workout,
    day: getDayKey(now),
    dateKey: todayKey,
    source: customName ? source : (getPlanOverride(now)?.movedFrom ? 'moved' : 'planned'),
    exercises: [],
    status: 'active'
  });
  state.attendance[todayKey] = 'active';
  persist();
  renderFitness();
  showToast('Тренировка начата');
}

function requestConfirm(message, callback) {
  if (tg?.showConfirm) {
    tg.showConfirm(message, confirmed => { if (confirmed) callback(); });
    return;
  }
  if (window.confirm(message)) callback();
}

function cancelActiveWorkout() {
  if (!state.activeWorkout) return;
  requestConfirm('Отменить запуск? Время и незавершённые подходы этой сессии не попадут в историю.', () => {
    const key = state.activeWorkout?.dateKey || getDateKey(new Date(state.activeWorkout.started));
    if (state.attendance?.[key] === 'active') delete state.attendance[key];
    markActiveWorkoutCleared();
    state.activeWorkout = null;
    clearWorkoutTimer();
    persist();
    closeSheets();
    renderFitness();
    showToast('Запуск отменён');
  });
}

function finishWorkout() {
  if (!state.activeWorkout) return;
  const sheet = document.getElementById('finish-workout-sheet');
  const summary = sheet?.querySelector('.finish-workout-summary');
  if (summary) {
    const elapsed = getWorkoutElapsedSeconds();
    const exerciseCount = state.activeWorkout.exercises?.length || 0;
    summary.textContent = `${formatWorkoutDuration(elapsed)} · ${exerciseCount ? `${exerciseCount} упр.` : 'без упражнений'} · результат сохранится в историю.`;
  }
  if (sheet) sheet.hidden = false;
}

function confirmFinishWorkout() {
  if (!state.activeWorkout) { closeSheets(); return; }
  const active = normalizeWorkoutRecord(state.activeWorkout);
  const startedDate = new Date(active.started);
  if (Number.isNaN(startedDate.getTime())) {
    markActiveWorkoutCleared();
    state.activeWorkout = null;
    persist();
    closeSheets();
    renderFitness();
    return;
  }
  const finishedDate = new Date();
  const elapsed = getWorkoutElapsedSeconds(active, finishedDate.getTime());
  const workoutDateKey = active.dateKey || getDateKey(startedDate);
  const exercises = (active.exercises || [])
    .map(exercise => ({
      ...exercise,
      sets: (exercise.sets || []).filter(set => Number(set.reps) > 0 || Number(set.weight) > 0)
    }))
    .filter(exercise => exercise.sets.length > 0);
  state.history.push(normalizeWorkoutRecord({
    ...active,
    updatedAt: finishedDate.toISOString(),
    ended: finishedDate.toISOString(),
    date: finishedDate.toISOString(),
    dateKey: workoutDateKey,
    duration: elapsed,
    status: 'completed',
    exercises
  }));
  state.attendance[workoutDateKey] = 'done';
  markActiveWorkoutCleared(finishedDate.toISOString());
  state.activeWorkout = null;
  clearWorkoutTimer();
  persist();
  closeSheets();
  renderFitness();
  showToast('Тренировка завершена');
}

function getLastExercisePerformance(name, beforeTime = Date.now()) {
  const normalized = String(name || '').trim().toLocaleLowerCase('ru-RU');
  if (!normalized) return null;
  const sessions = [...(state.history || [])]
    .filter(item => item.status === 'completed' && new Date(item.ended || item.date || 0).getTime() < beforeTime)
    .sort((a, b) => new Date(b.ended || b.date || 0) - new Date(a.ended || a.date || 0));
  for (const session of sessions) {
    const exercise = (session.exercises || []).find(item => String(item.name || '').trim().toLocaleLowerCase('ru-RU') === normalized);
    if (!exercise) continue;
    const validSets = (exercise.sets || []).filter(set => Number(set.reps) > 0 || Number(set.weight) > 0);
    const best = validSets.reduce((acc, set) => {
      const score = (Number(set.weight) || 0) * 1000 + (Number(set.reps) || 0);
      return !acc || score > acc.score ? { set, score } : acc;
    }, null);
    return { session, exercise, bestSet: best?.set || null, sets: validSets.length };
  }
  return null;
}

function formatExercisePerformance(performance) {
  if (!performance) return 'Нет прошлых записей';
  const set = performance.bestSet;
  if (!set) return `${formatShortDate(performance.session.dateKey || performance.session.date)} · ${performance.sets} подхода`;
  const weight = Number(set.weight) > 0 ? `${formatGoalValue(set.weight)} кг × ` : '';
  const reps = Number(set.reps) > 0 ? `${formatGoalValue(set.reps)}` : '—';
  return `${formatShortDate(performance.session.dateKey || performance.session.date)} · ${weight}${reps} · ${performance.sets} подх.`;
}

function openExercises() {
  if (!state.activeWorkout) {
    showToast('Сначала начни тренировку');
    return;
  }
  renderExerciseSheet();
  const sheet = document.getElementById('exercise-sheet');
  if (sheet) sheet.hidden = false;
}

function addExercise() {
  if (!state.activeWorkout) return;
  const input = document.getElementById('exercise-name-input');
  const name = input?.value.trim();
  if (!name) {
    showToast('Назови упражнение');
    return;
  }
  const duplicate = (state.activeWorkout.exercises || []).some(item => item.name.toLocaleLowerCase('ru-RU') === name.toLocaleLowerCase('ru-RU'));
  if (duplicate) {
    showToast('Это упражнение уже добавлено');
    return;
  }
  state.activeWorkout.exercises = [...(state.activeWorkout.exercises || []), normalizeExercise({ id: createEntityId('exercise'), name, sets: [], createdAt: new Date().toISOString() })];
  if (input) input.value = '';
  touchActiveWorkout();
  persist();
  renderExerciseSheet();
  renderToday();
}

function removeExercise(exerciseId) {
  if (!state.activeWorkout) return;
  requestConfirm('Удалить упражнение и его подходы из текущей тренировки?', () => {
    state.activeWorkout.exercises = (state.activeWorkout.exercises || []).filter(item => item.id !== exerciseId);
    touchActiveWorkout();
    persist();
    renderExerciseSheet();
    renderToday();
  });
}

function addExerciseSet(exerciseId) {
  const exercise = state.activeWorkout?.exercises?.find(item => item.id === exerciseId);
  if (!exercise) return;
  const previous = exercise.sets?.[exercise.sets.length - 1];
  exercise.sets = [...(exercise.sets || []), normalizeExerciseSet({
    id: createEntityId('set'),
    reps: previous?.reps ?? '',
    weight: previous?.weight ?? '',
    createdAt: new Date().toISOString()
  })];
  touchActiveWorkout();
  persist();
  renderExerciseSheet();
}

function removeExerciseSet(exerciseId, setId) {
  const exercise = state.activeWorkout?.exercises?.find(item => item.id === exerciseId);
  if (!exercise) return;
  exercise.sets = (exercise.sets || []).filter(item => item.id !== setId);
  touchActiveWorkout();
  persist();
  renderExerciseSheet();
}

function updateExerciseSet(exerciseId, setId, field, value) {
  const exercise = state.activeWorkout?.exercises?.find(item => item.id === exerciseId);
  const set = exercise?.sets?.find(item => item.id === setId);
  if (!set || !['reps', 'weight'].includes(field)) return;
  const parsed = parseGoalNumber(value);
  set[field] = parsed === null ? '' : Math.max(0, field === 'reps' ? Math.floor(parsed) : parsed);
  touchActiveWorkout();
  persist();
}

function renderExerciseSheet() {
  const list = document.querySelector('.exercise-list');
  const count = document.querySelector('.exercise-sheet-count');
  if (!list) return;
  const exercises = state.activeWorkout?.exercises || [];
  if (count) count.textContent = `${exercises.length} упр.`;
  list.replaceChildren();
  if (!exercises.length) {
    const empty = document.createElement('div');
    empty.className = 'goal-history-empty';
    empty.textContent = 'Добавь первое упражнение — подходы появятся здесь.';
    list.appendChild(empty);
    return;
  }
  const activeStart = new Date(state.activeWorkout.started).getTime();
  exercises.forEach(exercise => {
    const card = document.createElement('div');
    card.className = 'exercise-card';
    const head = document.createElement('div');
    head.className = 'exercise-card-head';
    const copy = document.createElement('div');
    copy.className = 'exercise-card-copy';
    const title = document.createElement('strong');
    title.textContent = exercise.name;
    const previous = document.createElement('small');
    previous.textContent = `Последний раз: ${formatExercisePerformance(getLastExercisePerformance(exercise.name, activeStart))}`;
    copy.append(title, previous);
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'exercise-remove';
    remove.textContent = '×';
    remove.addEventListener('click', () => removeExercise(exercise.id));
    head.append(copy, remove);
    const sets = document.createElement('div');
    sets.className = 'exercise-sets';
    (exercise.sets || []).forEach((set, index) => {
      const row = document.createElement('div');
      row.className = 'exercise-set-row';
      const number = document.createElement('span');
      number.className = 'exercise-set-index';
      number.textContent = String(index + 1);
      const reps = document.createElement('input');
      reps.inputMode = 'numeric';
      reps.placeholder = 'Повт.';
      reps.value = set.reps === '' ? '' : String(set.reps);
      reps.setAttribute('aria-label', `Повторения, подход ${index + 1}`);
      reps.addEventListener('input', () => updateExerciseSet(exercise.id, set.id, 'reps', reps.value));
      const weight = document.createElement('input');
      weight.inputMode = 'decimal';
      weight.placeholder = 'Вес';
      weight.value = set.weight === '' ? '' : String(set.weight);
      weight.setAttribute('aria-label', `Вес, подход ${index + 1}`);
      weight.addEventListener('input', () => updateExerciseSet(exercise.id, set.id, 'weight', weight.value));
      const del = document.createElement('button');
      del.type = 'button';
      del.className = 'exercise-set-delete';
      del.textContent = '×';
      del.addEventListener('click', () => removeExerciseSet(exercise.id, set.id));
      row.append(number, reps, weight, del);
      sets.appendChild(row);
    });
    const addSet = document.createElement('button');
    addSet.type = 'button';
    addSet.className = 'exercise-add-set';
    addSet.textContent = '+ Подход';
    addSet.addEventListener('click', () => addExerciseSet(exercise.id));
    card.append(head, sets, addSet);
    list.appendChild(card);
  });
}

function renderToday() {
  const box = document.querySelector('.today-widget');
  if (!box) return;
  reconcileStaleActiveWorkout(false);
  const now = new Date();
  const workout = getPlannedWorkoutForDate(now);
  const sessions = getCompletedWorkoutsForDate(now);
  const completed = sessions[sessions.length - 1] || null;
  const title = box.querySelector('h2');
  const eyebrow = box.querySelector('.module-eyebrow');
  const summary = box.querySelector('.workout-summary');
  const timer = box.querySelector('.workout-timer');
  const button = box.querySelector('.start-button');
  const tools = box.querySelector('.active-workout-tools');
  const exerciseCount = box.querySelector('.workout-exercise-count');
  const alternate = box.querySelector('.today-secondary-action');
  const activeNow = Boolean(state.activeWorkout);
  if (eyebrow) eyebrow.textContent = activeNow && state.activeWorkout?.dateKey !== getDateKey(now) ? 'Продолжается' : 'Сегодня';
  box.classList.toggle('is-active', activeNow);
  box.classList.toggle('is-completed', Boolean(completed) && !activeNow);
  clearWorkoutTimer();
  if (tools) tools.hidden = !activeNow;
  if (exerciseCount) exerciseCount.textContent = String(state.activeWorkout?.exercises?.length || 0);
  if (alternate) alternate.hidden = true;

  if (activeNow) {
    title.textContent = state.activeWorkout.workout || 'Тренировка';
    if (summary) {
      summary.hidden = false;
      const sourceLabel = state.activeWorkout.source === 'custom' ? 'Вне плана' : state.activeWorkout.source === 'moved' ? 'Перенесённая тренировка' : 'По плану';
      const started = new Date(state.activeWorkout.started);
      const crossedDay = !Number.isNaN(started.getTime()) && getDateKey(started) !== getDateKey(now);
      summary.textContent = crossedDay ? `${sourceLabel} · начата ${formatShortDate(getDateKey(started))}` : sourceLabel;
    }
    if (button) {
      button.disabled = false;
      button.textContent = 'Завершить тренировку';
      button.onclick = finishWorkout;
    }
    updateWorkoutTimer();
    return;
  }

  if (timer) timer.textContent = '';
  if (completed) {
    const totalSeconds = sessions.reduce((sum, item) => sum + normalizeWorkoutSeconds(item.duration), 0);
    title.textContent = sessions.length > 1 ? `${sessions.length} тренировки` : completed.workout;
    if (summary) {
      summary.hidden = false;
      summary.textContent = sessions.length > 1
        ? `Сегодня · ${formatWorkoutDuration(totalSeconds)}`
        : `${[formatClock(completed.started), formatClock(completed.ended)].filter(Boolean).join('–')} · ${formatWorkoutDuration(completed.duration)}`;
    }
    if (button) {
      button.textContent = 'Тренировка завершена';
      button.onclick = null;
      button.disabled = true;
    }
    if (alternate) {
      alternate.hidden = false;
      alternate.textContent = 'Ещё тренировка';
      alternate.onclick = openCustomWorkout;
    }
    return;
  }

  if (summary) { summary.hidden = true; summary.textContent = ''; }
  if (!button) return;
  button.disabled = false;
  if (workout && workout !== 'Отдых') {
    title.textContent = workout;
    button.textContent = 'Начать тренировку';
    button.onclick = () => startWorkout();
    if (alternate) {
      alternate.hidden = false;
      alternate.textContent = 'Другая тренировка';
      alternate.onclick = openCustomWorkout;
    }
  } else {
    title.textContent = workout === 'Отдых' ? 'День отдыха' : 'Нет запланированной тренировки';
    button.textContent = 'Начать тренировку';
    button.onclick = openCustomWorkout;
  }
}

function getWeekMonday(date = new Date()) {
  const day = startOfDay(date);
  day.setDate(day.getDate() - ((day.getDay() + 6) % 7));
  return day;
}


function formatDashboardDuration(seconds) {
  const total = normalizeWorkoutSeconds(seconds);
  if (!total) return '0м';
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (!hours) return total < 60 ? '<1м' : `${minutes}м`;
  return minutes ? `${hours}ч ${String(minutes).padStart(2, '0')}м` : `${hours}ч`;
}

function getWorkoutCountNoun(count) {
  const n = Math.abs(Number(count) || 0) % 100;
  const last = n % 10;
  if (n >= 11 && n <= 19) return 'тренировок';
  if (last === 1) return 'тренировка';
  if (last >= 2 && last <= 4) return 'тренировки';
  return 'тренировок';
}

function getSessionCountNoun(count) {
  const n = Math.abs(Number(count) || 0) % 100;
  const last = n % 10;
  if (n >= 11 && n <= 19) return 'сессий';
  if (last === 1) return 'сессия';
  if (last >= 2 && last <= 4) return 'сессии';
  return 'сессий';
}

function getTrainingWeekSnapshot(referenceDate = new Date(), nowMs = Date.now()) {
  const monday = getWeekMonday(referenceDate);
  const days = Array.from({ length: 7 }, (_, index) => {
    const date = new Date(monday);
    date.setDate(monday.getDate() + index);
    return date;
  });
  const weekKeys = new Set(days.map(getDateKey));
  const completedSessions = (state.history || []).filter(item => {
    if (item.status !== 'completed') return false;
    if (item.dateKey) return weekKeys.has(item.dateKey);
    const rawDate = item.date || item.ended;
    if (!rawDate) return false;
    const parsed = new Date(rawDate);
    if (Number.isNaN(parsed.getTime())) return false;
    return weekKeys.has(getDateKey(parsed));
  });
  const activeKey = state.activeWorkout?.started
    ? (state.activeWorkout.dateKey || getDateKey(new Date(state.activeWorkout.started)))
    : null;
  const activeInWeek = Boolean(activeKey && weekKeys.has(activeKey));
  const activeSeconds = activeInWeek ? getWorkoutElapsedSeconds(state.activeWorkout, nowMs) : 0;
  const secondsByDay = days.map(date => {
    const key = getDateKey(date);
    let seconds = completedSessions
      .filter(item => {
        if (item.dateKey) return item.dateKey === key;
        const parsed = new Date(item.date || item.ended || 0);
        return !Number.isNaN(parsed.getTime()) && getDateKey(parsed) === key;
      })
      .reduce((sum, item) => sum + normalizeWorkoutSeconds(item.duration), 0);
    if (activeInWeek && activeKey === key) seconds += activeSeconds;
    return seconds;
  });
  const completedSeconds = completedSessions.reduce((sum, item) => sum + normalizeWorkoutSeconds(item.duration), 0);
  const effectiveFrom = getPlanEffectiveDate();
  const plannedDays = days.filter(date => {
    if (effectiveFrom && startOfDay(date) < effectiveFrom) return false;
    const workout = getPlannedWorkoutForDate(date);
    return Boolean(workout && workout !== 'Отдых');
  });
  const completedPlanned = plannedDays.filter(date => getCompletedWorkoutsForDate(date).length > 0).length;
  return {
    monday,
    days,
    completedSessions,
    completedSeconds,
    activeInWeek,
    activeSeconds,
    secondsByDay,
    totalSeconds: completedSeconds + activeSeconds,
    averageSeconds: completedSessions.length ? Math.floor(completedSeconds / completedSessions.length) : 0,
    plannedCount: plannedDays.length,
    completedPlanned
  };
}

function getNextPlannedWorkout(fromDate = new Date(), maxDays = 14) {
  const start = startOfDay(fromDate);
  const effectiveFrom = getPlanEffectiveDate();
  for (let offset = 1; offset <= maxDays; offset++) {
    const date = new Date(start);
    date.setDate(start.getDate() + offset);
    if (effectiveFrom && date < effectiveFrom) continue;
    const workout = getPlannedWorkoutForDate(date);
    if (workout && workout !== 'Отдых') return { date, workout, offset };
  }
  return null;
}

function renderHomeTrainingCard(nowMs = Date.now()) {
  const primary = document.getElementById('home-workout-primary');
  const secondary = document.getElementById('home-workout-secondary');
  if (!primary || !secondary) return;

  const now = new Date(nowMs);
  const todayKey = getDateKey(now);
  const sessions = getCompletedWorkoutsForDate(now);
  const planned = getPlannedWorkoutForDate(now);
  const activeToday = Boolean(state.activeWorkout);

  if (activeToday) {
    primary.textContent = state.activeWorkout.workout || 'Тренировка';
    secondary.textContent = `Идёт сейчас · ${formatWorkoutDuration(getWorkoutElapsedSeconds(state.activeWorkout, nowMs))}`;
    return;
  }

  if (sessions.length) {
    const total = sessions.reduce((sum, item) => sum + normalizeWorkoutSeconds(item.duration), 0);
    if (sessions.length === 1) {
      primary.textContent = sessions[0].workout || 'Тренировка завершена';
      secondary.textContent = `Завершено · ${formatWorkoutDuration(total)}`;
    } else {
      primary.textContent = `${sessions.length} ${getWorkoutCountNoun(sessions.length)} сегодня`;
      secondary.textContent = `Всего · ${formatWorkoutDuration(total)}`;
    }
    return;
  }

  if (planned && planned !== 'Отдых') {
    primary.textContent = planned;
    const override = getPlanOverride(now);
    secondary.textContent = override?.movedFrom ? 'Перенесено на сегодня' : 'По плану на сегодня';
    return;
  }

  const next = getNextPlannedWorkout(now);
  primary.textContent = planned === 'Отдых' ? 'День отдыха' : 'Сегодня без тренировки';
  if (next) {
    const when = next.offset === 1
      ? 'завтра'
      : new Intl.DateTimeFormat('ru-RU', { weekday: 'short', day: 'numeric', month: 'short' }).format(next.date);
    secondary.textContent = `Следующая: ${next.workout} · ${when}`;
  } else {
    secondary.textContent = Object.keys(state.plan || {}).length ? 'Следующая тренировка не запланирована' : 'Настрой план или начни вне плана';
  }
}


function getLocalDateKeyFromValue(value) {
  if (!value) return null;
  if (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : getDateKey(parsed);
}

function getFinanceEntriesForDate(date) {
  const key = getDateKey(date);
  const groups = [financeData.expenses, financeData.mandatoryExpenses, financeData.debts].filter(Array.isArray);
  return groups.flat().filter(item => getLocalDateKeyFromValue(item?.date || item?.createdAt) === key);
}

function getTodaySpent(date = new Date()) {
  const key = getDateKey(date);
  return (financeData.expenses || []).reduce((sum, item) => {
    if (getLocalDateKeyFromValue(item?.date || item?.createdAt) !== key) return sum;
    const amount = Number(item?.amount);
    return sum + (Number.isFinite(amount) && amount > 0 ? amount : 0);
  }, 0);
}

function getScheduleEventDate(event) {
  if (!event || typeof event !== 'object') return null;
  const raw = event.start || event.startAt || event.dateTime || event.datetime || event.date || null;
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
  const key = getDateKey(date);
  return scheduleData.filter(event => {
    const eventDate = getScheduleEventDate(event);
    return eventDate && getDateKey(eventDate) === key && event?.status !== 'cancelled';
  });
}

function getNextScheduleEvent(now = new Date()) {
  const nowMs = now.getTime();
  return scheduleData
    .map(event => ({ event, date: getScheduleEventDate(event) }))
    .filter(item => item.date && item.event?.status !== 'cancelled' && item.event?.status !== 'completed' && item.date.getTime() >= nowMs - 60_000)
    .sort((a, b) => a.date - b.date)[0] || null;
}

function getNotesForDate(date) {
  const key = getDateKey(date);
  return notesData.filter(note => getLocalDateKeyFromValue(note?.updatedAt || note?.createdAt || note?.date) === key && note?.archived !== true);
}

function getTrainingSecondsForDate(date, nowMs = Date.now()) {
  const key = getDateKey(date);
  let seconds = getCompletedWorkoutsForDate(date).reduce((sum, item) => sum + normalizeWorkoutSeconds(item.duration), 0);
  if (state.activeWorkout) {
    const activeKey = state.activeWorkout.dateKey || getLocalDateKeyFromValue(state.activeWorkout.started);
    if (activeKey === key) seconds += getWorkoutElapsedSeconds(state.activeWorkout, nowMs);
  }
  return seconds;
}

function getDashboardActivityBreakdown(date, nowMs = Date.now()) {
  const trainingSeconds = getTrainingSecondsForDate(date, nowMs);
  const financeEntries = getFinanceEntriesForDate(date);
  const scheduleEvents = getScheduleEventsForDate(date);
  const notes = getNotesForDate(date);
  const completedEvents = scheduleEvents.filter(event => event?.completed || event?.status === 'completed').length;
  const isFuture = startOfDay(date) > startOfDay(new Date(nowMs));
  const training = isFuture ? 0 : Math.min(25, (trainingSeconds / (45 * 60)) * 25);
  const finance = isFuture ? 0 : Math.min(25, (financeEntries.length / 3) * 25);
  const schedule = isFuture ? 0 : (scheduleEvents.length ? Math.min(25, 10 + completedEvents * 10 + Math.min(5, Math.max(0, scheduleEvents.length - 1) * 2.5)) : 0);
  const noteScore = isFuture ? 0 : Math.min(25, (notes.length / 2) * 25);
  return {
    training, finance, schedule, notes: noteScore,
    score: Math.round(training + finance + schedule + noteScore),
    trainingSeconds,
    financeCount: financeEntries.length,
    spent: getTodaySpent(date),
    scheduleCount: scheduleEvents.length,
    noteCount: notes.length
  };
}

function getLastCompletedWorkout(before = new Date()) {
  const cutoff = before.getTime();
  return (state.history || [])
    .filter(item => item?.status === 'completed')
    .map(item => ({ item, time: new Date(item.ended || item.date || item.started || 0).getTime() }))
    .filter(entry => Number.isFinite(entry.time) && entry.time <= cutoff)
    .sort((a, b) => b.time - a.time)[0]?.item || null;
}

function formatRubles(value) {
  const amount = Math.max(0, Math.round(Number(value) || 0));
  return `${amount.toLocaleString('ru-RU')} ₽`;
}

function renderHomeDomainCards(now = new Date()) {
  const spent = getTodaySpent(now);
  const financePrimary = document.getElementById('home-finance-primary');
  const financeSecondary = document.getElementById('home-finance-secondary');
  if (financePrimary) financePrimary.textContent = spent ? `−${formatRubles(spent)} сегодня` : '0 ₽ сегодня';
  if (financeSecondary) {
    const entries = getFinanceEntriesForDate(now).length;
    financeSecondary.textContent = entries ? `${entries} ${entries === 1 ? 'операция' : entries < 5 ? 'операции' : 'операций'} за день` : 'Расходов пока нет';
  }

  const next = getNextScheduleEvent(now);
  const schedulePrimary = document.getElementById('home-schedule-primary');
  const scheduleSecondary = document.getElementById('home-schedule-secondary');
  if (next) {
    const isToday = getDateKey(next.date) === getDateKey(now);
    if (schedulePrimary) schedulePrimary.textContent = String(next.event.title || next.event.name || 'Событие');
    if (scheduleSecondary) scheduleSecondary.textContent = `${isToday ? 'Сегодня' : new Intl.DateTimeFormat('ru-RU', { weekday: 'short', day: 'numeric', month: 'short' }).format(next.date)} · ${new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' }).format(next.date)}`;
  } else {
    if (schedulePrimary) schedulePrimary.textContent = 'Событий нет';
    if (scheduleSecondary) scheduleSecondary.textContent = 'Расписание пока пустое';
  }

  const notes = notesData.filter(note => note?.archived !== true);
  const notesPrimary = document.getElementById('home-notes-primary');
  const notesSecondary = document.getElementById('home-notes-secondary');
  if (notesPrimary) notesPrimary.textContent = notes.length ? `${notes.length} ${notes.length === 1 ? 'заметка' : notes.length < 5 ? 'заметки' : 'заметок'}` : 'Заметок нет';
  if (notesSecondary) {
    const last = [...notes].sort((a, b) => new Date(b.updatedAt || b.createdAt || 0) - new Date(a.updatedAt || a.createdAt || 0))[0];
    notesSecondary.textContent = last ? String(last.title || last.text || 'Последняя запись').slice(0, 54) : 'Новые записи появятся здесь';
  }
}

function renderHomeActivity(nowMs = Date.now()) {
  const now = new Date(nowMs);
  const monday = getWeekMonday(now);
  const days = Array.from({ length: 7 }, (_, index) => {
    const day = new Date(monday);
    day.setDate(monday.getDate() + index);
    return day;
  });
  const breakdowns = days.map(day => getDashboardActivityBreakdown(day, nowMs));
  const todayIndex = Math.max(0, Math.min(6, Math.round((startOfDay(now) - startOfDay(monday)) / 86400000)));
  const elapsed = breakdowns.slice(0, todayIndex + 1);
  const weekScore = elapsed.length ? Math.round(elapsed.reduce((sum, item) => sum + item.score, 0) / elapsed.length) : 0;

  const spentEl = document.getElementById('home-finance-spent');
  const workoutEl = document.getElementById('home-last-workout');
  const scheduleEl = document.getElementById('home-next-schedule');
  const scheduleLabel = document.getElementById('home-next-schedule-label');
  const progress = document.getElementById('home-activity-progress');
  const line = document.getElementById('home-activity-line');
  const area = document.getElementById('home-activity-area');
  const points = document.getElementById('home-activity-points');

  const todaySpent = getTodaySpent(now);
  if (spentEl) spentEl.textContent = todaySpent ? `−${formatRubles(todaySpent)}` : '0 ₽';

  const lastWorkout = getLastCompletedWorkout(now);
  if (workoutEl) workoutEl.textContent = lastWorkout ? formatDashboardDuration(normalizeWorkoutSeconds(lastWorkout.duration)) : '—';

  const next = getNextScheduleEvent(now);
  if (scheduleEl) scheduleEl.textContent = next ? new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' }).format(next.date) : '—';
  if (scheduleLabel) scheduleLabel.textContent = next ? String(next.event.title || next.event.name || 'Следующее').slice(0, 22) : 'Следующее';

  if (progress) {
    const value = progress.querySelector('b');
    const label = progress.querySelector('small');
    progress.classList.remove('is-live');
    if (value) value.textContent = `${weekScore}%`;
    if (label) label.textContent = 'общей активности';
  }

  if (!line || !area || !points) return;
  const baselineY = 124;
  const topY = 28;
  const coords = breakdowns.map((item, index) => ({
    x: Number((index * (336 / 6)).toFixed(1)),
    y: Number((baselineY - Math.max(0, Math.min(100, item.score)) / 100 * (baselineY - topY)).toFixed(1))
  }));
  const path = buildSmoothPath(coords);
  line.setAttribute('d', path);
  area.setAttribute('d', `${path} L336 142 L0 142 Z`);
  points.replaceChildren();
  coords.forEach((point, index) => {
    const day = days[index];
    const data = breakdowns[index];
    const group = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    group.setAttribute('tabindex', '0');
    group.setAttribute('role', 'button');
    const hit = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    hit.setAttribute('cx', point.x); hit.setAttribute('cy', point.y); hit.setAttribute('r', '12'); hit.setAttribute('fill', 'transparent');
    const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
    circle.setAttribute('cx', point.x); circle.setAttribute('cy', point.y);
    circle.setAttribute('r', getDateKey(day) === getDateKey(now) ? '3.8' : '3.2');
    const label = new Intl.DateTimeFormat('ru-RU', { weekday: 'short', day: 'numeric', month: 'short' }).format(day);
    const detail = `${data.score}% · тренировки ${formatDashboardDuration(data.trainingSeconds)} · финансы ${data.financeCount} · расписание ${data.scheduleCount} · заметки ${data.noteCount}`;
    group.setAttribute('aria-label', `${label}: ${detail}`);
    const announce = () => showToast(`${label} · ${detail}`);
    group.addEventListener('click', announce);
    group.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); announce(); } });
    group.append(hit, circle);
    points.appendChild(group);
  });
}


function renderHomeTrainingSummary(nowMs = Date.now()) {
  renderHomeTrainingCard(nowMs);
  renderHomeActivity(nowMs);
  renderHomeDomainCards(new Date(nowMs));
  const status = document.querySelector('.subtle-status');
  if (!status) return;
  const today = getDashboardActivityBreakdown(new Date(nowMs), nowMs);
  const activeDomains = [today.training > 0, today.finance > 0, today.schedule > 0, today.notes > 0].filter(Boolean).length;
  if (state.activeWorkout) status.textContent = `Тренировка идёт · ${formatWorkoutDuration(getWorkoutElapsedSeconds(state.activeWorkout, nowMs))}`;
  else if (activeDomains) status.textContent = `Сегодня активны ${activeDomains} из 4 сфер`;
  else status.textContent = 'Сегодня можно начать с любого раздела';
}


function shiftActivityWeek(delta) {
  const currentMonday = getWeekMonday(new Date());
  const base = activityWeekCursor ? new Date(activityWeekCursor) : currentMonday;
  const next = new Date(base);
  next.setDate(next.getDate() + delta * 7);
  if (next > currentMonday) return;
  activityWeekCursor = next;
  renderActivityChart();
}

function renderActivityChart(nowMs = Date.now()) {
  const line = document.querySelector('.chart-line-path');
  const area = document.querySelector('.chart-area-path');
  const points = document.querySelector('.chart-point-group');
  const totalEl = document.querySelector('.activity-total');
  const metaEl = document.querySelector('.activity-meta');
  const labelEl = document.querySelector('.activity-week-label');
  const nextButton = document.querySelector('.activity-next');
  if (!line || !area || !points) return;

  const currentMonday = getWeekMonday(new Date(nowMs));
  const monday = activityWeekCursor ? getWeekMonday(activityWeekCursor) : currentMonday;
  const isCurrentWeek = getDateKey(monday) === getDateKey(currentMonday);
  const days = Array.from({ length: 7 }, (_, index) => {
    const date = new Date(monday);
    date.setDate(monday.getDate() + index);
    return date;
  });
  const sunday = days[6];
  if (labelEl) {
    labelEl.textContent = isCurrentWeek
      ? 'Эта неделя'
      : `${new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short' }).format(monday)}–${new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short' }).format(sunday)}`;
  }
  if (nextButton) nextButton.disabled = isCurrentWeek;
  const weekKeys = new Set(days.map(getDateKey));
  const completedSessions = (state.history || []).filter(item => {
    if (item.status !== 'completed') return false;
    const itemDate = item.dateKey || getDateKey(new Date(item.date));
    return weekKeys.has(itemDate);
  });
  const completedSeconds = completedSessions.reduce((sum, item) => sum + normalizeWorkoutSeconds(item.duration), 0);
  const activeInWeek = isCurrentWeek && state.activeWorkout?.started && weekKeys.has(state.activeWorkout.dateKey || getDateKey(new Date(state.activeWorkout.started)));
  const activeSeconds = activeInWeek ? getWorkoutElapsedSeconds(state.activeWorkout, nowMs) : 0;
  const secondsByDay = days.map(date => {
    const key = getDateKey(date);
    let seconds = completedSessions
      .filter(item => (item.dateKey || getDateKey(new Date(item.date))) === key)
      .reduce((sum, item) => sum + normalizeWorkoutSeconds(item.duration), 0);
    if (activeInWeek && (state.activeWorkout.dateKey || getDateKey(new Date(state.activeWorkout.started))) === key) seconds += activeSeconds;
    return seconds;
  });
  const totalSeconds = completedSeconds + activeSeconds;
  if (totalEl) totalEl.textContent = formatWorkoutDuration(totalSeconds);
  if (metaEl) {
    if (!completedSessions.length && !activeInWeek) metaEl.textContent = isCurrentWeek ? 'пока нет активности за эту неделю' : 'нет тренировок за эту неделю';
    else {
      const average = completedSessions.length ? Math.floor(completedSeconds / completedSessions.length) : 0;
      const count = completedSessions.length;
      const noun = getWorkoutCountNoun(count);
      metaEl.textContent = count ? `${count} ${noun} · в среднем ${formatWorkoutDuration(average)}` : 'тренировка идёт сейчас';
    }
  }
  const baseScale = 4 * 60 * 60;
  const headroom = 60 * 60;
  const peak = Math.max(0, ...secondsByDay);
  const scale = Math.max(baseScale, peak + headroom);
  const coords = secondsByDay.map((seconds, index) => ({
    x: Number((index * (320 / 6)).toFixed(1)),
    y: Number((104 - Math.max(0, Math.min(1, seconds / scale)) * 80).toFixed(1))
  }));
  const path = buildSmoothPath(coords);
  line.setAttribute('d', path);
  area.setAttribute('d', `${path} L320 112 L0 112 Z`);
  points.innerHTML = coords.map((point, index) => `<circle cx="${point.x}" cy="${point.y}" r="3.2" data-day-index="${index}" tabindex="0"><title>${formatWorkoutDuration(secondsByDay[index])}</title></circle>`).join('');
  points.querySelectorAll('circle').forEach(circle => {
    const index = Number(circle.dataset.dayIndex);
    const dayLabel = new Intl.DateTimeFormat('ru-RU', { weekday: 'short', day: 'numeric', month: 'short' }).format(days[index]);
    const announce = () => showToast(`${dayLabel} · ${formatWorkoutDuration(secondsByDay[index] || 0)}`);
    circle.addEventListener('click', announce);
    circle.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); announce(); }
    });
  });
}

function getActiveGoals() {
  return (state.goals || []).filter(goal => !goal.archivedAt);
}

function getArchivedGoals() {
  return (state.goals || []).filter(goal => Boolean(goal.archivedAt));
}

function ensureSelectedGoal() {
  const active = getActiveGoals();
  if (selectedGoalId && active.some(goal => goal.id === selectedGoalId)) return selectedGoalId;
  selectedGoalId = active[0]?.id || null;
  return selectedGoalId;
}

function renderGoals() {
  const box = document.querySelector('.goals-widget');
  if (!box) return;
  state.goals = (state.goals || []).map(normalizeGoal).filter(Boolean);
  ensureSelectedGoal();
  const activeGoals = getActiveGoals();
  const archivedGoals = getArchivedGoals();
  const selected = getGoalById(selectedGoalId);
  const switcher = box.querySelector('.goal-switcher');
  const title = box.querySelector('h3');
  const values = box.querySelector('.goal-values');
  if (switcher) {
    switcher.replaceChildren();
    activeGoals.forEach((goal, index) => {
      const item = document.createElement('div');
      item.className = `goal-switcher-item${goal.id === selectedGoalId ? ' is-selected' : ''}`;

      const colorButton = document.createElement('button');
      colorButton.type = 'button';
      colorButton.className = 'goal-color-button';
      colorButton.style.setProperty('--goal-color', getGoalColor(goal, index));
      colorButton.setAttribute('aria-label', `Действия цели «${goal.name}»`);
      colorButton.addEventListener('click', () => openGoalActions(goal.id));

      const labelButton = document.createElement('button');
      labelButton.type = 'button';
      labelButton.className = 'goal-select-button';
      labelButton.textContent = goal.name;
      labelButton.setAttribute('aria-pressed', goal.id === selectedGoalId ? 'true' : 'false');
      labelButton.addEventListener('click', () => selectGoal(goal.id));

      item.append(colorButton, labelButton);
      switcher.appendChild(item);
    });
  }
  box.classList.toggle('is-text-goal', Boolean(selected?.type === 'text'));
  box.classList.toggle('is-goal-completed', Boolean(selected?.status === 'completed'));
  if (!selected) {
    title.textContent = archivedGoals.length ? 'Активных целей нет' : 'Создай первую цель';
    values.textContent = archivedGoals.length ? 'Завершённые цели сохранены в архиве' : 'Добавь цель, чтобы отслеживать прогресс';
    values.removeAttribute('data-status');
  } else if (selected.type === 'numeric') {
    title.textContent = selected.name;
    const unit = selected.unit ? ` ${selected.unit}` : '';
    values.textContent = `${formatGoalValue(selected.current)}${unit} → ${formatGoalValue(selected.target)}${unit} · ${getGoalProgress(selected)}%`;
    if (selected.status === 'completed') values.dataset.status = 'Выполнено'; else values.removeAttribute('data-status');
  } else {
    title.textContent = selected.name;
    const targetText = selected.text || (selected.status === 'completed' ? 'Цель выполнена' : 'В процессе');
    values.textContent = selected.lastResult ? `${targetText} · Сейчас: ${selected.lastResult}` : targetText;
    values.dataset.status = selected.status === 'completed' ? 'Выполнено' : 'В процессе';
  }
  const textHistory = box.querySelector('.goal-text-history');
  if (textHistory) {
    if (selected?.type === 'text' && selected.history?.length) {
      const recent = [...selected.history].reverse().slice(0, 3);
      textHistory.hidden = false;
      textHistory.replaceChildren(...recent.map(item => {
        const row = document.createElement('div'); row.className = 'goal-text-history-row';
        const date = document.createElement('small'); date.textContent = formatGoalHistoryDate(item.recordedAt || item.date);
        const copy = document.createElement('span'); copy.textContent = String(item.note || '').trim() || (item.status === 'completed' ? 'Цель выполнена' : 'Статус обновлён');
        row.append(date, copy); return row;
      }));
    } else { textHistory.hidden = true; textHistory.replaceChildren(); }
  }
  const summary = box.querySelector('.goal-current-summary');
  if (summary) {
    summary.onclick = selected ? () => editGoal(selected.id) : addGoal;
    summary.onkeydown = event => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selected ? editGoal(selected.id) : addGoal(); }
    };
  }
  const archiveListButton = box.querySelector('.goal-archive-list-button');
  const goalTools = box.querySelector('.goal-tools');
  if (archiveListButton) {
    archiveListButton.hidden = !archivedGoals.length;
    archiveListButton.textContent = `Архив · ${archivedGoals.length}`;
  }
  if (goalTools) goalTools.hidden = !archivedGoals.length;
  renderGoalsChart(activeGoals);
}

function openGoalActions(goalId) {
  const goal = normalizeGoal(getGoalById(goalId));
  if (!goal || goal.archivedAt) return;
  selectedGoalId = goal.id;
  renderGoals();

  const sheet = document.getElementById('goal-actions-sheet');
  if (!sheet) return;
  const title = sheet.querySelector('.goal-actions-title');
  const subtitle = sheet.querySelector('.goal-actions-subtitle');
  const color = sheet.querySelector('.goal-actions-color');
  const record = sheet.querySelector('.goal-action-primary');
  if (title) title.textContent = goal.name;
  if (color) { const goalColor = getGoalDisplayColor(goal); color.style.background = goalColor; color.style.setProperty('--goal-color', goalColor); }
  if (record) record.textContent = goal.type === 'text' ? 'Записать прогресс' : 'Записать результат';
  if (subtitle) {
    if (goal.type === 'numeric') {
      const unit = goal.unit ? ` ${goal.unit}` : '';
      subtitle.textContent = `${formatGoalValue(goal.current)}${unit} из ${formatGoalValue(goal.target)}${unit} · ${getGoalProgress(goal)}%`;
    } else {
      subtitle.textContent = goal.status === 'completed' ? 'Выполнено' : 'В процессе';
    }
  }
  sheet.hidden = false;
}

function goalActionRecord() {
  const goalId = selectedGoalId;
  closeSheets();
  openGoalResult(goalId);
}

function goalActionHistory() {
  const goalId = selectedGoalId;
  closeSheets();
  openGoalHistory(goalId);
}

function goalActionEdit() {
  const goalId = selectedGoalId;
  closeSheets();
  editGoal(goalId);
}

function goalActionArchive() {
  archiveSelectedGoal();
}

function goalActionDelete() {
  deleteGoal(selectedGoalId);
}

function openGoalResult(goalId = selectedGoalId, entryId = null) {
  const goal = normalizeGoal(getGoalById(goalId));
  if (!goal) { addGoal(); return; }
  selectedGoalId = goal.id;
  editingGoalHistoryEntryId = entryId;
  const entry = entryId ? goal.history?.find(item => item.id === entryId) : null;
  const numericWrap = document.getElementById('goal-result-numeric');
  const textWrap = document.getElementById('goal-result-text');
  const title = document.getElementById('goal-result-title');
  const subtitle = document.getElementById('goal-result-subtitle');
  if (goal.type === 'numeric') {
    numericWrap.hidden = false; textWrap.hidden = true;
    const input = document.getElementById('goal-result-value');
    if (input) input.value = entry ? String(entry.value ?? '') : String(goal.current ?? '');
    if (title) title.textContent = entry ? `Изменить запись · ${goal.name}` : `Результат · ${goal.name}`;
    if (subtitle) subtitle.textContent = entry ? 'Исправь ошибочную запись. График пересчитается автоматически.' : 'Добавь текущее значение — дата и время сохранятся автоматически.';
  } else {
    numericWrap.hidden = true; textWrap.hidden = false;
    const note = document.getElementById('goal-result-note');
    const status = document.getElementById('goal-result-status');
    if (note) note.value = entry ? String(entry.note || '') : '';
    if (status) status.value = entry?.status === 'completed' || (!entry && goal.status === 'completed') ? 'completed' : 'active';
    if (title) title.textContent = entry ? `Изменить прогресс · ${goal.name}` : `Прогресс · ${goal.name}`;
    if (subtitle) subtitle.textContent = entry ? 'Исправь этап или статус этой записи.' : 'Запиши текущий этап или изменение. Дата и время сохранятся автоматически.';
  }
  document.getElementById('goal-result-sheet').hidden = false;
}

function saveGoalResult() {
  const goal = normalizeGoal(getGoalById(selectedGoalId));
  if (!goal) { closeSheets(); addGoal(); return; }
  const now = new Date().toISOString();
  const today = getDateKey(new Date());
  const previousStatus = goal.status;
  let history = [...(goal.history || [])];
  let updatedGoal;
  if (goal.type === 'numeric') {
    const value = parseGoalNumber(document.getElementById('goal-result-value').value);
    if (value === null) { showToast('Укажи текущий результат'); return; }
    if (editingGoalHistoryEntryId) {
      const entry = history.find(item => item.id === editingGoalHistoryEntryId);
      if (!entry) { showToast('Запись не найдена'); return; }
      entry.value = value;
      entry.recordedAt = entry.recordedAt || now;
      entry.updatedAt = now;
      entry.date = normalizeGoalDate(entry.date || entry.recordedAt);
    } else {
      const latest = history[history.length - 1];
      if (latest && normalizeGoalDate(latest.date || latest.recordedAt) === today && parseGoalNumber(latest.value) === value) {
        closeSheets(); showToast('Этот результат уже записан сегодня'); return;
      }
      history = appendGoalHistoryEntry(history, { id: createEntityId('goalentry'), date: today, value, kind: 'result', recordedAt: now, updatedAt: now });
    }
    updatedGoal = normalizeGoal({ ...goal, current: value, history, updatedAt: now });
  } else {
    const note = document.getElementById('goal-result-note').value.trim();
    const status = document.getElementById('goal-result-status').value === 'completed' ? 'completed' : 'active';
    if (editingGoalHistoryEntryId) {
      const entry = history.find(item => item.id === editingGoalHistoryEntryId);
      if (!entry) { showToast('Запись не найдена'); return; }
      entry.note = note; entry.status = status; entry.kind = 'result'; entry.updatedAt = now;
    } else {
      if (!note && status === goal.status) { showToast('Запиши изменение или поменяй статус'); return; }
      history = appendGoalHistoryEntry(history, { id: createEntityId('goalentry'), date: today, note, status, kind: 'result', recordedAt: now, updatedAt: now });
    }
    updatedGoal = normalizeGoal({ ...goal, status, history, updatedAt: now });
  }
  state.goals = state.goals.map(item => item.id === goal.id ? updatedGoal : item);
  selectedGoalId = updatedGoal.id;
  editingGoalHistoryEntryId = null;
  persist();
  closeSheets();
  renderFitness();
  if (previousStatus !== 'completed' && updatedGoal.status === 'completed') showToast('Цель достигнута');
  else showToast('Результат сохранён');
}

function formatGoalHistoryValue(goal, entry) {
  if (goal.type === 'numeric') return `${formatGoalValue(entry.value)}${goal.unit ? ` ${goal.unit}` : ''}`;
  return String(entry.note || '').trim() || (entry.status === 'completed' ? 'Цель выполнена' : 'Статус обновлён');
}

function openGoalHistory(goalId = selectedGoalId, archiveOnly = false) {
  if (goalId && getGoalById(goalId)) selectedGoalId = goalId;
  renderGoalHistorySheet(archiveOnly);
  const sheet = document.getElementById('goal-history-sheet');
  if (sheet) sheet.hidden = false;
}

function renderGoalHistorySheet(archiveOnly = false) {
  const goal = getGoalById(selectedGoalId);
  const list = document.querySelector('.goal-history-list');
  const subtitle = document.querySelector('.goal-history-subtitle');
  const archive = document.querySelector('.goal-archive-list');
  const archiveSection = document.querySelector('.goal-archive-section');
  if (!list || !archive) return;
  list.replaceChildren();
  if (!archiveOnly && goal && !goal.archivedAt) {
    if (subtitle) subtitle.textContent = `${goal.name} · ${goal.history?.length || 0} записей`;
    const entries = [...(goal.history || [])].reverse();
    if (!entries.length) {
      const empty = document.createElement('div'); empty.className = 'goal-history-empty'; empty.textContent = 'Записей пока нет.'; list.appendChild(empty);
    }
    entries.forEach(entry => {
      const row = document.createElement('div'); row.className = `goal-history-row${entry.kind === 'baseline' ? ' is-baseline' : ''}`;
      const copy = document.createElement('span');
      const strong = document.createElement('strong'); strong.textContent = formatGoalHistoryValue(goal, entry);
      const small = document.createElement('small'); small.textContent = `${formatGoalHistoryDate(entry.recordedAt || entry.date, true)}${entry.kind === 'baseline' ? ' · старт' : ''}`;
      copy.append(strong, small);
      const actions = document.createElement('div'); actions.className = 'row-actions';
      if (entry.kind !== 'baseline') {
        const edit = document.createElement('button'); edit.type = 'button'; edit.textContent = 'Изм.'; edit.addEventListener('click', () => { closeSheets(); openGoalResult(goal.id, entry.id); });
        const del = document.createElement('button'); del.type = 'button'; del.dataset.action = 'delete'; del.textContent = '×'; del.addEventListener('click', () => deleteGoalHistoryEntry(goal.id, entry.id));
        actions.append(edit, del);
      }
      row.append(copy, actions); list.appendChild(row);
    });
  } else {
    if (subtitle) subtitle.textContent = 'Завершённые цели остаются доступными и их можно вернуть.';
    const empty = document.createElement('div'); empty.className = 'goal-history-empty'; empty.textContent = archiveOnly ? 'Архив целей' : 'Выбери активную цель, чтобы посмотреть записи.'; list.appendChild(empty);
  }
  const archived = getArchivedGoals().sort((a, b) => compareIso(b.archivedAt, a.archivedAt));
  archiveSection?.classList.toggle('is-empty', !archived.length);
  archived.forEach(item => {
    const row = document.createElement('div'); row.className = 'goal-archive-row';
    const copy = document.createElement('span'); const strong = document.createElement('strong'); strong.textContent = item.name;
    const small = document.createElement('small'); small.textContent = item.archivedAt ? `В архиве с ${formatGoalHistoryDate(item.archivedAt)}` : 'В архиве'; copy.append(strong, small);
    const actions = document.createElement('div'); actions.className = 'row-actions';
    const restore = document.createElement('button'); restore.type = 'button'; restore.textContent = 'Вернуть'; restore.addEventListener('click', () => restoreGoal(item.id));
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'is-danger'; remove.textContent = '×'; remove.setAttribute('aria-label', `Удалить цель «${item.name}»`); remove.addEventListener('click', () => deleteGoal(item.id));
    actions.append(restore, remove);
    row.append(copy, actions); archive.appendChild(row);
  });
}

function deleteGoalHistoryEntry(goalId, entryId) {
  const goal = normalizeGoal(getGoalById(goalId));
  const entry = goal?.history?.find(item => item.id === entryId);
  if (!goal || !entry || entry.kind === 'baseline') return;
  requestConfirm('Удалить эту запись прогресса?', () => {
    recordSyncTombstone('goalEntries', entryId);
    const history = goal.history.filter(item => item.id !== entryId);
    const updated = normalizeGoal({ ...goal, history, updatedAt: new Date().toISOString() });
    state.goals = state.goals.map(item => item.id === goalId ? updated : item);
    persist();
    renderFitness();
    renderGoalHistorySheet(false);
    showToast('Запись удалена');
  });
}

function archiveSelectedGoal() {
  const goal = normalizeGoal(getGoalById(selectedGoalId));
  if (!goal || goal.archivedAt) return;
  const commitArchive = () => {
    const now = new Date().toISOString();
    state.goals = state.goals.map(item => item.id === goal.id ? normalizeGoal({ ...goal, archivedAt: now, updatedAt: now }) : item);
    selectedGoalId = null;
    persist();
    closeSheets();
    renderFitness();
    showToast('Цель перемещена в архив');
  };
  if (goal.status === 'completed') {
    commitArchive();
  } else {
    requestConfirm('Цель ещё не выполнена. Всё равно поместить её в архив?', commitArchive);
  }
}

function restoreGoal(goalId) {
  const goal = normalizeGoal(getGoalById(goalId));
  if (!goal) return;
  const restored = normalizeGoal({ ...goal, archivedAt: null, updatedAt: new Date().toISOString() });
  state.goals = state.goals.map(item => item.id === goalId ? restored : item);
  selectedGoalId = goalId;
  persist();
  closeSheets();
  renderFitness();
  showToast('Цель возвращена');
}

function deleteGoal(goalId = editingGoalId || selectedGoalId) {
  const goal = getGoalById(goalId);
  if (!goal) { closeSheets(); return; }
  requestConfirm(`Удалить цель «${goal.name}» и всю её историю?`, () => {
    recordSyncTombstone('goals', goalId);
    (goal.history || []).forEach(entry => {
      if (entry?.id) recordSyncTombstone('goalEntries', entry.id);
    });
    state.goals = state.goals.filter(item => item.id !== goalId);
    editingGoalId = null;
    selectedGoalId = getActiveGoals()[0]?.id || null;
    persist();
    closeSheets();
    renderFitness();
    showToast('Цель удалена');
  });
}

function openTraining() {
  const screen = document.getElementById('training-screen');
  if (!screen) return;
  screen.hidden = false;
  document.body.classList.add('training-open');
  calendarCursor = new Date(new Date().getFullYear(), new Date().getMonth(), 1);
  activityWeekCursor = getWeekMonday(new Date());
  renderFitness();
  syncTrainingWithServer();
}

function renderFitness() {
  if (reconcileStaleActiveWorkout(false)) persist();
  renderGoals();
  renderPlan();
  renderToday();
  renderCalendar();
  renderActivityChart();
  renderHomeTrainingSummary();
}

/* v10 history identity hardening: keep stable record IDs through edits/sync. */
function normalizeNumericGoalHistory(goal, safeCurrent) {
  const rawHistory = Array.isArray(goal.history) ? goal.history : [];
  const history = [];
  const goalId = getStableGoalId(goal, goal.updatedAt || goal.createdAt || 'goal');
  rawHistory.forEach((item, index) => {
    const value = parseGoalNumber(item?.value);
    if (value === null) return;
    const date = normalizeGoalDate(item?.date || item?.recordedAt || goal.updatedAt);
    const normalized = {
      id: String(item?.id || '') || createStableGoalEntryId(goalId, item, index),
      date,
      value,
      kind: item?.kind || (index === 0 ? 'baseline' : 'result'),
      recordedAt: item?.recordedAt || (typeof item?.date === 'string' && item.date.includes('T') ? item.date : goal.updatedAt || new Date().toISOString()),
      updatedAt: item?.updatedAt || item?.recordedAt || goal.updatedAt || new Date().toISOString()
    };
    history.push(normalized);
  });
  if (!history.length) {
    const recordedAt = goal.updatedAt || new Date().toISOString();
    history.push({
      id: createEntityId('goalentry'),
      date: normalizeGoalDate(goal.updatedAt),
      value: safeCurrent,
      kind: 'baseline',
      recordedAt,
      updatedAt: recordedAt
    });
  }
  const sorted = sortGoalHistory(history);
  if (sorted[0]) sorted[0].kind = 'baseline';
  return sorted;
}

function normalizeTextGoalHistory(goal, targetText, fallbackStatus) {
  const rawHistory = Array.isArray(goal.history) ? goal.history : [];
  const history = [];
  const goalId = getStableGoalId(goal, goal.updatedAt || goal.createdAt || 'goal');
  rawHistory.forEach((item, index) => {
    const rawText = String(item?.note ?? item?.text ?? '').trim();
    const isLegacyDefinition = !item?.kind && rawText && rawText === targetText;
    const note = isLegacyDefinition ? '' : rawText;
    const status = item?.status === 'completed' ? 'completed' : 'active';
    if (!note && !item?.status) return;
    const recordedAt = item?.recordedAt || (typeof item?.date === 'string' && item.date.includes('T') ? item.date : goal.updatedAt || new Date().toISOString());
    history.push({
      id: String(item?.id || '') || createStableGoalEntryId(goalId, item, index),
      date: normalizeGoalDate(item?.date || item?.recordedAt || goal.updatedAt),
      note,
      status,
      kind: item?.kind === 'result' || note ? 'result' : 'status',
      recordedAt,
      updatedAt: item?.updatedAt || recordedAt
    });
  });
  return sortGoalHistory(history);
}

/* === Dashboard profile & social layer v14 ============================== */
function telegramApiHeaders(json = false) {
  if (!tg?.initData) return null;
  return {
    ...(json ? { 'Content-Type': 'application/json' } : {}),
    'X-Telegram-Init-Data': tg.initData
  };
}

function getApiErrorMessage(payload, fallback = 'Не удалось выполнить действие') {
  const detail = payload?.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  return fallback;
}

function profileDisplayName(person) {
  const full = [person?.first_name, person?.last_name].filter(Boolean).join(' ').trim();
  return full || (person?.username ? `@${person.username}` : 'Пользователь SVGTracker');
}

function renderProfileState() {
  const count = document.getElementById('profile-friends-count');
  const toggle = document.getElementById('profile-notifications-toggle');
  const status = document.getElementById('profile-drawer-status');
  const username = document.getElementById('profile-drawer-username');
  if (count) count.textContent = String(profileState.friends?.length || 0);
  if (toggle) {
    toggle.checked = profileState.notifications_enabled !== false;
    toggle.disabled = !tg?.initData;
  }
  const user = window.SVG_TELEGRAM_USER;
  if (username) username.textContent = user?.username ? `@${user.username}` : (user ? 'Аккаунт Telegram' : 'Открой SVGTracker в Telegram');
  if (status) {
    const incoming = profileState.incoming?.length || 0;
    status.textContent = !tg?.initData
      ? 'Социальные функции доступны внутри Telegram'
      : incoming
        ? `${incoming} ${incoming === 1 ? 'новый запрос в друзья' : 'новых запроса в друзья'}`
        : 'Настройки синхронизируются с аккаунтом Telegram';
  }
  renderFriendsSheet();
}

async function loadProfileData(force = false) {
  if (!tg?.initData || profileLoading || (profileLoaded && !force)) {
    renderProfileState();
    return profileState;
  }
  profileLoading = true;
  try {
    const response = await fetch('/api/profile', { headers: telegramApiHeaders(false) });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getApiErrorMessage(payload, 'Профиль временно недоступен'));
    profileState = {
      notifications_enabled: payload.notifications_enabled !== false,
      friends: Array.isArray(payload.friends) ? payload.friends : [],
      incoming: Array.isArray(payload.incoming) ? payload.incoming : [],
      outgoing: Array.isArray(payload.outgoing) ? payload.outgoing : []
    };
    profileLoaded = true;
    renderProfileState();
    return profileState;
  } catch (error) {
    const status = document.getElementById('profile-drawer-status');
    if (status) status.textContent = error?.message || 'Профиль временно недоступен';
    return profileState;
  } finally {
    profileLoading = false;
  }
}

function openProfileDrawer() {
  const drawer = document.getElementById('profile-drawer');
  if (!drawer) return;
  drawer.hidden = false;
  document.body.classList.add('profile-drawer-open');
  requestAnimationFrame(() => drawer.classList.add('is-open'));
  renderProfileState();
  loadProfileData(true);
}

function closeProfileDrawer(immediate = false) {
  const drawer = document.getElementById('profile-drawer');
  if (!drawer || drawer.hidden) return;
  drawer.classList.remove('is-open');
  document.body.classList.remove('profile-drawer-open');
  if (immediate) drawer.hidden = true;
  else setTimeout(() => { if (!drawer.classList.contains('is-open')) drawer.hidden = true; }, 220);
}

async function toggleBotNotifications(enabled) {
  const toggle = document.getElementById('profile-notifications-toggle');
  const previous = profileState.notifications_enabled !== false;
  profileState.notifications_enabled = Boolean(enabled);
  renderProfileState();
  if (!tg?.initData) {
    profileState.notifications_enabled = previous;
    renderProfileState();
    showToast('Открой приложение внутри Telegram');
    return;
  }
  if (toggle) toggle.disabled = true;
  try {
    const response = await fetch('/api/profile/notifications', {
      method: 'PUT',
      headers: telegramApiHeaders(true),
      body: JSON.stringify({ enabled: Boolean(enabled) })
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getApiErrorMessage(payload));
    profileState.notifications_enabled = payload.enabled !== false;
    showToast(profileState.notifications_enabled ? 'Уведомления включены' : 'Уведомления выключены');
  } catch (error) {
    profileState.notifications_enabled = previous;
    showToast(error?.message || 'Не удалось изменить уведомления');
  } finally {
    if (toggle) toggle.disabled = false;
    renderProfileState();
  }
}

function createSocialAvatar(person) {
  const avatar = document.createElement('span');
  avatar.className = 'social-avatar';
  const letter = (person?.first_name || person?.username || '?').trim().charAt(0).toUpperCase() || '?';
  if (person?.photo_url) {
    const image = document.createElement('img');
    image.src = person.photo_url;
    image.alt = '';
    image.onerror = () => { image.remove(); avatar.textContent = letter; };
    avatar.appendChild(image);
  } else avatar.textContent = letter;
  return avatar;
}

function createSocialPersonCopy(person, secondaryText = '') {
  const copy = document.createElement('span');
  copy.className = 'social-person-copy';
  const name = document.createElement('strong');
  name.textContent = profileDisplayName(person);
  const meta = document.createElement('small');
  meta.textContent = secondaryText || (person?.username ? `@${person.username}` : 'SVGTracker');
  copy.append(name, meta);
  return copy;
}

function renderSocialEmpty(container, text) {
  const empty = document.createElement('p');
  empty.className = 'social-empty';
  empty.textContent = text;
  container.replaceChildren(empty);
}

function renderFriendsSheet() {
  const friendsList = document.getElementById('friends-list');
  const incomingList = document.getElementById('friends-incoming-list');
  const outgoingList = document.getElementById('friends-outgoing-list');
  const incomingSection = document.getElementById('friends-incoming-section');
  const outgoingSection = document.getElementById('friends-outgoing-section');
  if (!friendsList || !incomingList || !outgoingList) return;

  const friends = profileState.friends || [];
  if (!friends.length) renderSocialEmpty(friendsList, 'Пока никого. Добавь друга по Telegram username.');
  else {
    friendsList.replaceChildren(...friends.map(person => {
      const row = document.createElement('div'); row.className = 'social-person-row';
      row.append(createSocialAvatar(person), createSocialPersonCopy(person));
      const action = document.createElement('button'); action.type = 'button'; action.className = 'social-small-action is-danger'; action.textContent = 'Удалить';
      action.addEventListener('click', () => requestConfirm(`Удалить ${profileDisplayName(person)} из друзей?`, () => removeFriendById(person.id)));
      row.appendChild(action); return row;
    }));
  }

  const incoming = profileState.incoming || [];
  if (incomingSection) incomingSection.hidden = !incoming.length;
  incomingList.replaceChildren(...incoming.map(person => {
    const row = document.createElement('div'); row.className = 'social-person-row';
    row.append(createSocialAvatar(person), createSocialPersonCopy(person, person?.username ? `@${person.username}` : 'Хочет добавить тебя'));
    const actions = document.createElement('span'); actions.className = 'social-inline-actions';
    const accept = document.createElement('button'); accept.type = 'button'; accept.className = 'social-small-action'; accept.textContent = 'Принять'; accept.addEventListener('click', () => resolveFriendRequestFromUi(person.request_id, true));
    const reject = document.createElement('button'); reject.type = 'button'; reject.className = 'social-small-action is-muted'; reject.textContent = 'Отклонить'; reject.addEventListener('click', () => resolveFriendRequestFromUi(person.request_id, false));
    actions.append(accept, reject); row.appendChild(actions); return row;
  }));

  const outgoing = profileState.outgoing || [];
  if (outgoingSection) outgoingSection.hidden = !outgoing.length;
  outgoingList.replaceChildren(...outgoing.map(person => {
    const row = document.createElement('div'); row.className = 'social-person-row';
    row.append(createSocialAvatar(person), createSocialPersonCopy(person, 'Запрос отправлен'));
    return row;
  }));
}

function openFriendsSheet() {
  closeProfileDrawer(true);
  const sheet = document.getElementById('friends-sheet');
  if (!sheet) return;
  sheet.hidden = false;
  renderFriendsSheet();
  loadProfileData(true);
}

function openAddFriendSheet() {
  closeProfileDrawer(true);
  const sheet = document.getElementById('add-friend-sheet');
  if (!sheet) return;
  sheet.hidden = false;
  const input = document.getElementById('friend-username-input');
  if (input) { input.value = ''; setTimeout(() => input.focus(), 120); }
}

async function submitFriendRequest() {
  const input = document.getElementById('friend-username-input');
  const button = document.getElementById('friend-request-button');
  const username = String(input?.value || '').trim();
  if (!username) { showToast('Укажи Telegram username'); return; }
  if (!tg?.initData) { showToast('Открой приложение внутри Telegram'); return; }
  if (button) button.disabled = true;
  try {
    const response = await fetch('/api/friends/request', {
      method: 'POST', headers: telegramApiHeaders(true), body: JSON.stringify({ username })
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getApiErrorMessage(payload, 'Не удалось отправить запрос'));
    closeSheets();
    const result = payload.result;
    showToast(result === 'accepted' ? 'Теперь вы друзья' : result === 'already_friends' ? 'Вы уже друзья' : result === 'pending' ? 'Запрос уже отправлен' : 'Запрос отправлен');
    await loadProfileData(true);
  } catch (error) {
    showToast(error?.message || 'Не удалось отправить запрос');
  } finally {
    if (button) button.disabled = false;
  }
}

async function resolveFriendRequestFromUi(requestId, accept) {
  if (!tg?.initData) return;
  try {
    const response = await fetch(`/api/friends/requests/${encodeURIComponent(requestId)}/${accept ? 'accept' : 'reject'}`, {
      method: 'POST', headers: telegramApiHeaders(false)
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getApiErrorMessage(payload));
    showToast(accept ? 'Запрос принят' : 'Запрос отклонён');
    await loadProfileData(true);
  } catch (error) {
    showToast(error?.message || 'Не удалось обработать запрос');
  }
}

async function removeFriendById(friendId) {
  if (!tg?.initData) return;
  try {
    const response = await fetch(`/api/friends/${encodeURIComponent(friendId)}`, {
      method: 'DELETE', headers: telegramApiHeaders(false)
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(getApiErrorMessage(payload));
    showToast('Удалено из друзей');
    await loadProfileData(true);
  } catch (error) {
    showToast(error?.message || 'Не удалось удалить друга');
  }
}

window.addEventListener('online', () => loadProfileData(true));
document.addEventListener('DOMContentLoaded', () => {
  renderProfileState();
  renderHomeTrainingSummary();
  if (tg?.initData) loadProfileData(false);
});
