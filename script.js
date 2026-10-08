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
  finance: 'finance_budget_v2'
};

let calendarCursor = new Date(new Date().getFullYear(), new Date().getMonth(), 1);
let workoutTimerHandle = null;
let trainingSaveTimer = null;
let trainingServerReady = false;
let trainingSyncInFlight = false;
let selectedGoalType = 'numeric';
let selectedGoalId = null;
let editingGoalId = null;
const MAX_ACTIVE_WORKOUT_SECONDS = 18 * 60 * 60;

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
 saveFinance();closeSheets();renderFinance();
}
function deleteFinance(type,id){
 financeData[type]=financeData[type].filter(x=>x.id!==id);
 saveFinance();renderFinance();
}
function renderFinance(){
 const income=Number(financeData.monthlyIncome || financeData.income?.amount || 0);
 const mandatory=financeData.mandatoryExpenses.reduce((a,b)=>a+Number(b.amount),0);
 const spent=financeData.expenses.reduce((a,b)=>a+Number(b.amount),0);
 const activeDebt=financeData.debts.filter(d=>d.kind==='Я должен' && d.status!=='Закрыт').reduce((a,b)=>a+Number(b.amount),0);
 const available=Math.max(0,income-mandatory-spent-activeDebt);
 const percent=income?Math.round(available/income*100):0;
 financeData.budgetHistory.push({date:new Date().toISOString(), value:available}); financeData.budgetHistory=financeData.budgetHistory.slice(-60);
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

const GOAL_COLORS = ['#9b83ff', '#45d6ff', '#ff8bb7', '#73e6a3', '#ffb45e', '#5f9cff', '#f2df68', '#c78cff'];

function getGoalColor(goal, fallbackIndex = 0) {
  // The index comes from the stable active-goal order. This intentionally
  // guarantees different colors for adjacent goals instead of relying on a
  // hash that can collide inside the small palette.
  const index = Math.abs(Number(fallbackIndex) || 0) % GOAL_COLORS.length;
  return GOAL_COLORS[index];
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
  const seriesGroup = document.querySelector('.goals-widget .goal-series-group');
  const range = document.querySelector('.goals-widget .goal-chart-range');
  const rangeStart = document.querySelector('.goals-widget .goal-range-start');
  const rangeEnd = document.querySelector('.goals-widget .goal-range-end');
  if (!chartWrap || !seriesGroup) return;

  const series = getNumericGoalSeries(goals);
  if (!series.length) {
    chartWrap.hidden = true;
    if (range) range.hidden = true;
    seriesGroup.replaceChildren();
    return;
  }

  chartWrap.hidden = false;
  const allEntries = series.flatMap(item => item.entries);
  const minTime = Math.min(...allEntries.map(item => item.time));
  const maxTime = Math.max(...allEntries.map(item => item.time));
  const timeSpan = Math.max(1, maxTime - minTime);
  const singleInstant = maxTime === minTime;
  const left = 8, right = 312, bottom = 72, height = 50;
  const fragment = document.createDocumentFragment();

  series.forEach(item => {
    const coords = item.entries.map(entry => {
      const progress = getGoalRawProgress(item.goal, entry.value);
      const visibleProgress = Math.max(0, Math.min(100, progress));
      const x = singleInstant ? 160 : left + ((entry.time - minTime) / timeSpan) * (right - left);
      const y = bottom - (visibleProgress / 100) * height;
      return { ...entry, x: Number(x.toFixed(1)), y: Number(y.toFixed(1)), progress };
    });

    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('class', 'goal-series-line');
    path.setAttribute('d', buildSmoothPath(coords));
    path.setAttribute('stroke', item.color);
    path.setAttribute('data-goal-id', item.goal.id);
    fragment.appendChild(path);

    coords.forEach(point => {
      const circle = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      circle.setAttribute('class', 'goal-series-point');
      circle.setAttribute('cx', point.x);
      circle.setAttribute('cy', point.y);
      circle.setAttribute('r', '3.4');
      circle.setAttribute('fill', item.color);
      circle.setAttribute('data-goal-id', item.goal.id);
      circle.setAttribute('tabindex', '0');
      circle.setAttribute('role', 'button');
      const unit = item.goal.unit ? ` ${item.goal.unit}` : '';
      const title = document.createElementNS('http://www.w3.org/2000/svg', 'title');
      title.textContent = `${item.goal.name} · ${formatGoalHistoryDate(point.recordedAt, true)} · ${formatGoalValue(point.value)}${unit} · ${Math.round(point.progress)}%`;
      circle.appendChild(title);
      const announce = () => {
        selectGoal(item.goal.id);
        showToast(`${item.goal.name}: ${formatGoalValue(point.value)}${unit} · ${Math.round(point.progress)}%`);
      };
      circle.addEventListener('click', announce);
      circle.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); announce(); }
      });
      fragment.appendChild(circle);
    });
  });

  seriesGroup.replaceChildren(fragment);
  if (range && rangeStart && rangeEnd) {
    range.hidden = false;
    rangeStart.textContent = formatGoalHistoryDate(new Date(minTime).toISOString());
    if (singleInstant) {
      const count = allEntries.length;
      rangeEnd.textContent = `${count} ${count === 1 ? 'запись' : count < 5 ? 'записи' : 'записей'}`;
    } else {
      rangeEnd.textContent = formatGoalHistoryDate(new Date(maxTime).toISOString());
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
  const todayKey = getDateKey(now);
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
      const noun = count === 1 ? 'тренировка' : count > 1 && count < 5 ? 'тренировки' : 'тренировок';
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
      const button = document.createElement('button');
      button.type = 'button';
      button.className = `goal-switcher-item${goal.id === selectedGoalId ? ' is-selected' : ''}`;
      button.addEventListener('click', () => selectGoal(goal.id));
      const dot = document.createElement('span');
      dot.className = 'goal-color-dot';
      if (goal.type === 'numeric') dot.style.background = getGoalColor(goal, index); else dot.classList.add('is-text');
      const label = document.createElement('span');
      label.textContent = goal.name;
      button.append(dot, label);
      switcher.appendChild(button);
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
  const record = box.querySelector('.goal-record-button');
  if (record) {
    record.hidden = !selected;
    record.onclick = selected ? () => openGoalResult(selected.id) : null;
    record.textContent = selected?.type === 'text' ? 'Записать прогресс' : 'Записать результат';
  }
  const historyButton = box.querySelector('.goal-history-button');
  if (historyButton) historyButton.hidden = !selected && !archivedGoals.length;
  const archiveButton = box.querySelector('.goal-archive-button');
  if (archiveButton) archiveButton.hidden = !selected || selected.status !== 'completed';
  const archiveListButton = box.querySelector('.goal-archive-list-button');
  if (archiveListButton) {
    archiveListButton.hidden = !archivedGoals.length;
    archiveListButton.textContent = `Архив · ${archivedGoals.length}`;
  }
  renderGoalsChart(activeGoals);
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
    const restore = document.createElement('button'); restore.type = 'button'; restore.textContent = 'Вернуть'; restore.addEventListener('click', () => restoreGoal(item.id));
    row.append(copy, restore); archive.appendChild(row);
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
  if (!goal || goal.status !== 'completed') return;
  state.goals = state.goals.map(item => item.id === goal.id ? normalizeGoal({ ...goal, archivedAt: new Date().toISOString(), updatedAt: new Date().toISOString() }) : item);
  selectedGoalId = null;
  persist();
  renderFitness();
  showToast('Цель перемещена в архив');
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

function deleteGoal() {
  const goalId = editingGoalId || selectedGoalId;
  const goal = getGoalById(goalId);
  if (!goal) { closeSheets(); return; }
  requestConfirm('Удалить цель и всю её историю?', () => {
    recordSyncTombstone('goals', goalId);
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
