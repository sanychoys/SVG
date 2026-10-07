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

function normalizeNumericGoalHistory(goal, safeCurrent) {
  const rawHistory = Array.isArray(goal.history) ? goal.history : [];
  const history = [];

  rawHistory.forEach(item => {
    const value = parseGoalNumber(item?.value);
    if (value === null) return;
    const date = normalizeGoalDate(item?.date || item?.recordedAt || goal.updatedAt);
    history.push({
      date,
      value,
      recordedAt: item?.recordedAt || (typeof item?.date === 'string' && item.date.includes('T') ? item.date : goal.updatedAt || new Date().toISOString())
    });
  });

  if (!history.length) {
    history.push({
      date: normalizeGoalDate(goal.updatedAt),
      value: safeCurrent,
      recordedAt: goal.updatedAt || new Date().toISOString()
    });
  }

  return sortGoalHistory(history);
}

function normalizeTextGoalHistory(goal, targetText, fallbackStatus) {
  const rawHistory = Array.isArray(goal.history) ? goal.history : [];
  const history = [];

  rawHistory.forEach(item => {
    const rawText = String(item?.note ?? item?.text ?? '').trim();
    const isLegacyDefinition = !item?.kind && rawText && rawText === targetText;
    const note = isLegacyDefinition ? '' : rawText;
    const status = item?.status === 'completed' ? 'completed' : 'active';
    if (!note && !item?.status) return;
    const date = normalizeGoalDate(item?.date || item?.recordedAt || goal.updatedAt);
    history.push({
      date,
      note,
      status,
      kind: item?.kind === 'result' || note ? 'result' : 'status',
      recordedAt: item?.recordedAt || (typeof item?.date === 'string' && item.date.includes('T') ? item.date : goal.updatedAt || new Date().toISOString())
    });
  });

  return sortGoalHistory(history);
}

function normalizeGoal(goal) {
  if (!goal || typeof goal !== 'object') return null;
  const name = String(goal.name || '').trim() || 'Моя цель';
  const declaredType = goal.type === 'text' ? 'text' : goal.type === 'numeric' ? 'numeric' : null;
  const current = parseGoalNumber(goal.current);
  const target = parseGoalNumber(goal.target);
  const type = declaredType || (current !== null && target !== null ? 'numeric' : 'text');
  const fallbackTimestamp = goal.updatedAt || new Date().toISOString();
  const updatedAt = goal.updatedAt || fallbackTimestamp;

  if (type === 'text') {
    const targetText = String(goal.targetText || goal.text || [goal.current, goal.target]
      .filter(value => value !== undefined && value !== null && String(value).trim())
      .map(String)
      .join(' → ') || '').trim();
    const fallbackStatus = goal.status === 'completed' ? 'completed' : 'active';
    const history = normalizeTextGoalHistory(goal, targetText, fallbackStatus);
    const latest = history[history.length - 1];
    return {
      type: 'text',
      name,
      text: targetText,
      status: latest?.status === 'completed' ? 'completed' : fallbackStatus,
      lastResult: String(latest?.note || '').trim(),
      history,
      createdAt: goal.createdAt || history[0]?.recordedAt || fallbackTimestamp,
      updatedAt
    };
  }

  const safeCurrent = current ?? 0;
  const safeTarget = target ?? safeCurrent;
  const history = normalizeNumericGoalHistory(goal, safeCurrent);
  const latest = history[history.length - 1];
  const first = history[0];
  return {
    type: 'numeric',
    name,
    current: latest?.value ?? safeCurrent,
    target: safeTarget,
    start: parseGoalNumber(goal.start) ?? first?.value ?? safeCurrent,
    unit: String(goal.unit || '').trim(),
    history,
    createdAt: goal.createdAt || history[0]?.recordedAt || fallbackTimestamp,
    updatedAt
  };
}
function normalizeTrainingState(raw = {}) {
  const goals = Array.isArray(raw.goals) ? raw.goals.map(normalizeGoal).filter(Boolean).slice(0, 1) : [];
  return {
    goals,
    plan: raw.plan && typeof raw.plan === 'object' && !Array.isArray(raw.plan) ? raw.plan : {},
    history: Array.isArray(raw.history) ? raw.history : [],
    attendance: raw.attendance && typeof raw.attendance === 'object' && !Array.isArray(raw.attendance) ? raw.attendance : {},
    planMeta: raw.planMeta && typeof raw.planMeta === 'object' ? raw.planMeta : { effectiveFrom: null },
    activeWorkout: raw.activeWorkout && typeof raw.activeWorkout === 'object' ? raw.activeWorkout : null
  };
}

Object.assign(state, normalizeTrainingState(state));

if (!state.planMeta.effectiveFrom && Object.keys(state.plan).length) {
  state.planMeta.effectiveFrom = getDateKey(new Date());
}

function getTrainingPayload() {
  return {
    goals: state.goals,
    plan: state.plan,
    history: state.history,
    attendance: state.attendance,
    planMeta: state.planMeta,
    activeWorkout: state.activeWorkout
  };
}

function persistLocal() {
  localStorage.setItem(STORAGE.goals, JSON.stringify(state.goals));
  localStorage.setItem(STORAGE.plan, JSON.stringify(state.plan));
  localStorage.setItem(STORAGE.history, JSON.stringify(state.history));
  localStorage.setItem(STORAGE.attendance, JSON.stringify(state.attendance));
  localStorage.setItem(STORAGE.planMeta, JSON.stringify(state.planMeta));
  localStorage.setItem(STORAGE.activeWorkout, JSON.stringify(state.activeWorkout));
}

async function saveTrainingToServer() {
  if (!trainingServerReady || !tg?.initData) return false;
  try {
    const response = await fetch('/api/training/state', {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        'X-Telegram-Init-Data': tg.initData
      },
      body: JSON.stringify(getTrainingPayload())
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    localStorage.removeItem(STORAGE.trainingDirty);
    return true;
  } catch (_) {
    localStorage.setItem(STORAGE.trainingDirty, '1');
    return false;
  }
}

function scheduleTrainingSave() {
  if (!trainingServerReady || !tg?.initData) return;
  clearTimeout(trainingSaveTimer);
  trainingSaveTimer = setTimeout(() => saveTrainingToServer(), 250);
}

function persist(options = {}) {
  persistLocal();
  if (options.remote !== false) {
    localStorage.setItem(STORAGE.trainingDirty, '1');
    scheduleTrainingSave();
  }
}

async function syncTrainingWithServer() {
  if (!tg?.initData || trainingSyncInFlight) return false;
  trainingSyncInFlight = true;
  try {
    const response = await fetch('/api/training/state', {
      headers: { 'X-Telegram-Init-Data': tg.initData }
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    const localDirty = localStorage.getItem(STORAGE.trainingDirty) === '1';
    let staleWorkoutChanged = false;

    if (payload.exists && payload.state && !localDirty) {
      Object.assign(state, normalizeTrainingState(payload.state));
      staleWorkoutChanged = reconcileStaleActiveWorkout(false);
      persistLocal();
      localStorage.removeItem(STORAGE.trainingDirty);
    }

    trainingServerReady = true;

    if (!payload.exists || localDirty || staleWorkoutChanged) {
      await saveTrainingToServer();
    }

    renderFitness();
    return true;
  } catch (_) {
    trainingServerReady = false;
    return false;
  } finally {
    trainingSyncInFlight = false;
  }
}

function closeSheets() {
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
  el.textContent = text;
  el.classList.add('is-visible');
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => el.classList.remove('is-visible'), 1800);
}

function openTraining() {
  const screen = document.getElementById('training-screen');
  if (!screen) return;
  screen.hidden = false;
  document.body.classList.add('training-open');
  calendarCursor = new Date(new Date().getFullYear(), new Date().getMonth(), 1);
  renderFitness();
  syncTrainingWithServer();
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

function renderFitness() {
  if (reconcileStaleActiveWorkout(false)) persist();
  renderGoals();
  renderPlan();
  renderToday();
  renderCalendar();
  renderActivityChart();
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

function formatGoalHistoryDate(dateKey) {
  const time = goalDateToTime(dateKey);
  if (!Number.isFinite(time)) return String(dateKey || '');
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short' }).format(new Date(time));
}

function renderGoalChart(goal) {
  const chartWrap = document.querySelector('.goals-widget .goal-chart');
  const line = document.querySelector('.goals-widget .goal-line-path');
  const points = document.querySelector('.goals-widget .goal-point-group');
  if (!chartWrap || !line || !points) return;

  if (!goal || goal.type !== 'numeric') {
    chartWrap.hidden = true;
    return;
  }

  const history = getGoalDailyHistory(goal.history || [])
    .map(item => ({
      date: normalizeGoalDate(item?.date),
      value: parseGoalNumber(item?.value)
    }))
    .filter(item => item.value !== null && Number.isFinite(goalDateToTime(item.date)));

  if (!history.length) {
    chartWrap.hidden = true;
    return;
  }

  chartWrap.hidden = false;
  const target = Number(goal.target);
  const values = history.map(item => item.value);
  const scaleValues = Number.isFinite(target) ? [...values, target] : values;
  let min = Math.min(...scaleValues);
  let max = Math.max(...scaleValues);
  if (min === max) {
    const padding = Math.max(Math.abs(min) * 0.05, 1);
    min -= padding;
    max += padding;
  } else {
    const padding = (max - min) * 0.08;
    min -= padding;
    max += padding;
  }
  const span = max - min;

  const firstTime = goalDateToTime(history[0].date);
  const lastHistoryTime = goalDateToTime(history[history.length - 1].date);
  const minimumWindowMs = 6 * 24 * 60 * 60 * 1000;
  const lastTime = Math.max(lastHistoryTime, firstTime + minimumWindowMs);
  const timeSpan = Math.max(1, lastTime - firstTime);

  const coords = history.map(item => {
    const itemTime = goalDateToTime(item.date);
    return {
      x: Number((((itemTime - firstTime) / timeSpan) * 320).toFixed(1)),
      y: Number((72 - ((item.value - min) / span) * 50).toFixed(1)),
      date: item.date,
      value: item.value
    };
  });

  line.setAttribute('d', buildSmoothPath(coords));
  points.innerHTML = coords.map(point => {
    const unit = goal.unit ? ` ${goal.unit}` : '';
    return `<circle cx="${point.x}" cy="${point.y}" r="3.2"><title>${formatGoalHistoryDate(point.date)} · ${formatGoalValue(point.value)}${unit}</title></circle>`;
  }).join('');
}
function renderGoals() {
  const box = document.querySelector('.goals-widget');
  if (!box) return;

  const goal = normalizeGoal(state.goals[0]);
  if (goal) state.goals = [goal];
  const title = box.querySelector('h3');
  const values = box.querySelector('.goal-values');

  box.classList.toggle('is-text-goal', Boolean(goal?.type === 'text'));
  box.classList.toggle('is-goal-completed', Boolean(goal?.type === 'text' && goal.status === 'completed'));

  if (!goal) {
    title.textContent = 'Создай первую цель';
    values.textContent = 'Добавь цель, чтобы отслеживать прогресс';
    renderGoalChart(null);
  } else if (goal.type === 'numeric') {
    title.textContent = goal.name;
    const unit = goal.unit ? ` ${goal.unit}` : '';
    values.textContent = `${formatGoalValue(goal.current)}${unit} → ${formatGoalValue(goal.target)}${unit} · ${getGoalProgress(goal)}%`;
    renderGoalChart(goal);
  } else {
    title.textContent = goal.name;
    const targetText = goal.text || (goal.status === 'completed' ? 'Цель выполнена' : 'В процессе');
    values.textContent = goal.lastResult ? `${targetText} · Сейчас: ${goal.lastResult}` : targetText;
    values.dataset.status = goal.status === 'completed' ? 'Выполнено' : 'В процессе';
    renderGoalChart(goal);
  }

  const button = box.querySelector('.widget-action');
  if (button) {
    button.onclick = addGoal;
    button.setAttribute('aria-label', goal ? 'Редактировать цель' : 'Добавить цель');
  }

  const recordButton = box.querySelector('.goal-record-button');
  if (recordButton) {
    recordButton.hidden = !goal;
    recordButton.textContent = goal?.type === 'text' ? 'Записать прогресс' : 'Записать результат';
  }
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

function getCompletedWorkoutForDate(date = new Date()) {
  const key = getDateKey(date);
  return [...(state.history || [])]
    .reverse()
    .find(item => {
      if (item.status !== 'completed') return false;
      if (item.dateKey) return item.dateKey === key;
      const historyDate = new Date(item.date);
      return !Number.isNaN(historyDate.getTime()) && getDateKey(historyDate) === key;
    }) || null;
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
    state.activeWorkout = null;
    if (shouldPersist) persist();
    return true;
  }
  const startedKey = state.activeWorkout.dateKey || getDateKey(started);
  if (startedKey === getDateKey(new Date())) return false;

  // The user did press “Start”, so the historical day remains purple even if
  // the session was never explicitly finished. It is not counted in minutes.
  state.attendance[startedKey] = 'started';
  state.activeWorkout = null;
  if (shouldPersist) persist();
  return true;
}

function renderToday() {
  const box = document.querySelector('.today-widget');
  if (!box) return;

  reconcileStaleActiveWorkout(false);
  const now = new Date();
  const today = getDayKey(now);
  const todayKey = getDateKey(now);
  const workout = state.plan[today];
  const completed = getCompletedWorkoutForDate(now);

  const title = box.querySelector('h2');
  const eyebrow = box.querySelector('.module-eyebrow');
  const summary = box.querySelector('.workout-summary');
  const timer = box.querySelector('.workout-timer');
  const button = box.querySelector('.start-button');

  if (eyebrow) eyebrow.textContent = 'Сегодня';
  const activeToday = Boolean(state.activeWorkout && getDateKey(new Date(state.activeWorkout.started)) === todayKey);
  box.classList.toggle('is-active', activeToday);
  box.classList.toggle('is-completed', Boolean(completed));
  clearWorkoutTimer();

  if (completed) {
    title.textContent = completed.workout || workout || 'Тренировка';
    if (summary) {
      summary.hidden = false;
      const range = [formatClock(completed.started), formatClock(completed.ended)].filter(Boolean).join('–');
      summary.textContent = `${range ? `${range} · ` : ''}${formatWorkoutDuration(completed.duration)}`;
    }
    if (timer) timer.textContent = '';
    if (button) {
      button.textContent = 'Тренировка завершена';
      button.onclick = null;
      button.disabled = true;
    }
    return;
  }

  if (summary) {
    summary.hidden = true;
    summary.textContent = '';
  }

  if (workout && workout !== 'Отдых') title.textContent = workout;
  else if (workout === 'Отдых') title.textContent = 'День отдыха';
  else title.textContent = 'Нет запланированной тренировки';

  if (!button) return;
  button.disabled = false;

  if (activeToday) {
    button.textContent = 'Завершить тренировку';
    button.onclick = finishWorkout;
    updateWorkoutTimer();
  } else if (workout && workout !== 'Отдых') {
    if (timer) timer.textContent = '';
    button.textContent = 'Начать тренировку';
    button.onclick = startWorkout;
  } else {
    if (timer) timer.textContent = '';
    button.textContent = 'Настроить план';
    button.onclick = openPlan;
  }
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
    const workout = state.plan?.[getDayKey(cursor)];

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
  const workout = state.plan?.[getDayKey(current)];
  const effectiveFrom = getPlanEffectiveDate();

  // An explicit date result always wins over a later edit of the weekly plan.
  if (isWorkoutCompletedOnDate(current) || recordedStatus === 'done') return 'done';
  if (recordedStatus === 'missed' && current < today) return 'missed';
  if (recordedStatus === 'started') return 'started';

  if (state.activeWorkout) {
    const started = new Date(state.activeWorkout.started);
    if (!Number.isNaN(started.getTime()) && getDateKey(started) === key) return 'active';
  }

  if (current > today) return workout && workout !== 'Отдых' ? 'future scheduled' : 'future';

  if (current.getTime() === today.getTime()) {
    return workout && workout !== 'Отдых' ? 'scheduled' : 'rest';
  }

  // Exact attendance before date-based tracking cannot be reconstructed safely.
  if (!effectiveFrom || current < effectiveFrom) return recordedStatus || 'untracked';
  if (!workout || workout === 'Отдых') return 'rest';
  return 'missed';
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

function renderActivityChart(nowMs = Date.now()){
  const line = document.querySelector('.chart-line-path');
  const area = document.querySelector('.chart-area-path');
  const points = document.querySelector('.chart-point-group');
  const totalEl = document.querySelector('.activity-total');
  const metaEl = document.querySelector('.activity-meta');
  if(!line || !area || !points) return;

  const today = startOfDay(new Date(nowMs));
  const monday = new Date(today);
  monday.setDate(today.getDate() - ((today.getDay() + 6) % 7));

  const days = Array.from({length: 7}, (_, index) => {
    const date = new Date(monday);
    date.setDate(monday.getDate() + index);
    return date;
  });
  const weekKeys = new Set(days.map(getDateKey));

  const completedSessions = (state.history || []).filter(item => {
    if (item.status !== 'completed') return false;
    const itemDate = item.dateKey || getDateKey(new Date(item.date));
    return weekKeys.has(itemDate);
  });

  const completedSeconds = completedSessions.reduce(
    (sum, item) => sum + normalizeWorkoutSeconds(item.duration),
    0
  );
  const activeSeconds = state.activeWorkout?.started
    ? getWorkoutElapsedSeconds(state.activeWorkout, nowMs)
    : 0;

  const secondsByDay = days.map(date => {
    const key = getDateKey(date);
    let seconds = completedSessions
      .filter(item => (item.dateKey || getDateKey(new Date(item.date))) === key)
      .reduce((sum, item) => sum + normalizeWorkoutSeconds(item.duration), 0);

    if (state.activeWorkout?.started) {
      const started = new Date(state.activeWorkout.started);
      if (!Number.isNaN(started.getTime()) && getDateKey(started) === key) {
        seconds += activeSeconds;
      }
    }
    return seconds;
  });

  const completedThisWeek = completedSessions.length;
  const totalSeconds = completedSeconds + activeSeconds;

  if (totalEl) totalEl.textContent = formatWorkoutDuration(totalSeconds);
  if (metaEl) {
    if (!completedThisWeek && !state.activeWorkout) {
      metaEl.textContent = 'пока нет активности за эту неделю';
    } else if (!completedThisWeek && state.activeWorkout) {
      metaEl.textContent = 'тренировка идёт сейчас';
    } else {
      const averageSeconds = Math.floor(completedSeconds / completedThisWeek);
      const label = completedThisWeek === 1
        ? 'тренировка'
        : completedThisWeek < 5 ? 'тренировки' : 'тренировок';
      metaEl.textContent = `${completedThisWeek} ${label} · в среднем ${formatWorkoutDuration(averageSeconds)}`;
    }
  }

  // Keep a stable absolute vertical scale for normal workouts.
  // Previously the weekly maximum became the chart maximum, so a 60-minute
  // and a 180-minute session could both be drawn at exactly the same height.
  // Up to 3 hours the reference range stays fixed at 4 hours:
  // 1h = 25%, 1.5h = 37.5%, 2h = 50%, 3h = 75%.
  // Above 3 hours the scale grows continuously with one hour of headroom,
  // so very long sessions are never clipped and the live graph does not jump.
  const chartBaseScaleSeconds = 4 * 60 * 60;
  const chartHeadroomSeconds = 60 * 60;
  const peakSeconds = Math.max(0, ...secondsByDay);
  const chartScaleSeconds = Math.max(
    chartBaseScaleSeconds,
    peakSeconds + chartHeadroomSeconds
  );
  const chartBottomY = 104;
  const chartHeight = 80;

  const coords = secondsByDay.map((seconds, index) => {
    const ratio = Math.max(0, Math.min(1, seconds / chartScaleSeconds));
    return {
      x: Number((index * (320 / 6)).toFixed(1)),
      y: Number((chartBottomY - ratio * chartHeight).toFixed(1))
    };
  });

  const path = buildSmoothPath(coords);
  line.setAttribute('d', path);
  area.setAttribute('d', `${path} L320 112 L0 112 Z`);
  points.innerHTML = coords.map((point, index) => (
    `<circle cx="${point.x}" cy="${point.y}" r="3.2"><title>${formatWorkoutDuration(secondsByDay[index])}</title></circle>`
  )).join('');
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

function addGoal() {
  const goal = normalizeGoal(state.goals[0]);
  const currentInput = document.getElementById('goal-current');
  document.getElementById('goal-name').value = goal?.name || '';
  if (currentInput) {
    currentInput.value = goal?.type === 'numeric' ? goal.start : '';
    currentInput.readOnly = Boolean(goal?.type === 'numeric');
    currentInput.placeholder = goal?.type === 'numeric' ? 'Стартовое значение' : 'Стартовое значение';
  }
  document.getElementById('goal-target').value = goal?.type === 'numeric' ? goal.target : '';
  document.getElementById('goal-unit').value = goal?.type === 'numeric' ? goal.unit || '' : '';
  document.getElementById('goal-text').value = goal?.type === 'text' ? goal.text || '' : '';
  setGoalType(goal?.type || 'numeric');
  document.getElementById('goal-sheet').hidden = false;
}

function openGoalResult() {
  const goal = normalizeGoal(state.goals[0]);
  if (!goal) {
    addGoal();
    return;
  }

  const numeric = document.getElementById('goal-result-numeric');
  const text = document.getElementById('goal-result-text');
  const title = document.getElementById('goal-result-title');
  const subtitle = document.getElementById('goal-result-subtitle');

  if (goal.type === 'numeric') {
    if (numeric) numeric.hidden = false;
    if (text) text.hidden = true;
    const input = document.getElementById('goal-result-value');
    if (input) {
      input.value = '';
      input.placeholder = goal.unit ? `Текущее значение, ${goal.unit}` : 'Текущее значение';
    }
    if (title) title.textContent = 'Записать результат';
    if (subtitle) subtitle.textContent = `Сейчас: ${formatGoalValue(goal.current)}${goal.unit ? ` ${goal.unit}` : ''}. Дата и время сохранятся автоматически.`;
  } else {
    if (numeric) numeric.hidden = true;
    if (text) text.hidden = false;
    const note = document.getElementById('goal-result-note');
    const status = document.getElementById('goal-result-status');
    if (note) note.value = '';
    if (status) status.value = goal.status === 'completed' ? 'completed' : 'active';
    if (title) title.textContent = 'Записать прогресс';
    if (subtitle) subtitle.textContent = 'Запиши текущий этап или изменение. Дата и время сохранятся автоматически.';
  }

  document.getElementById('goal-result-sheet').hidden = false;
}

function editPlan() {
  openPlan();
}

function openGoal() {
  addGoal();
}

function saveGoal() {
  const name = document.getElementById('goal-name').value.trim();
  if (!name) {
    showToast('Добавь название цели');
    return;
  }

  const previous = normalizeGoal(state.goals[0]);
  const now = new Date().toISOString();

  if (selectedGoalType === 'text') {
    const text = document.getElementById('goal-text').value.trim();
    if (!text) {
      showToast('Опиши желаемый результат');
      return;
    }

    const previousText = previous?.type === 'text' ? previous : null;
    state.goals = [{
      type: 'text',
      name,
      text,
      status: previousText?.status || 'active',
      history: previousText?.history ? [...previousText.history] : [],
      createdAt: previousText?.createdAt || now,
      updatedAt: now
    }];
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

    state.goals = [{
      type: 'numeric',
      name,
      current: previousNumeric ? Number(previousNumeric.current) : initialValue,
      target,
      start: initialValue,
      unit: document.getElementById('goal-unit').value.trim(),
      history,
      createdAt: previousNumeric?.createdAt || now,
      updatedAt: now
    }];
  }

  state.goals = state.goals.map(normalizeGoal).filter(Boolean).slice(0, 1);
  persist();
  closeSheets();
  renderFitness();
  showToast(previous ? 'Цель обновлена' : 'Цель создана');
}

function saveGoalResult() {
  const goal = normalizeGoal(state.goals[0]);
  if (!goal) {
    closeSheets();
    addGoal();
    return;
  }

  const now = new Date().toISOString();
  const today = getDateKey(new Date());

  if (goal.type === 'numeric') {
    const value = parseGoalNumber(document.getElementById('goal-result-value').value);
    if (value === null) {
      showToast('Укажи текущий результат');
      return;
    }

    const history = [...(goal.history || [])];
    const latest = history[history.length - 1];
    const latestDate = latest ? normalizeGoalDate(latest.date || latest.recordedAt) : null;
    const latestValue = latest ? parseGoalNumber(latest.value) : null;

    if (latestDate === today && latestValue === value) {
      closeSheets();
      showToast('Этот результат уже записан сегодня');
      return;
    }

    const nextHistory = appendGoalHistoryEntry(history, {
      date: today,
      value,
      recordedAt: now
    });

    state.goals = [normalizeGoal({
      ...goal,
      current: value,
      history: nextHistory,
      updatedAt: now
    })];
  } else {
    const note = document.getElementById('goal-result-note').value.trim();
    const status = document.getElementById('goal-result-status').value === 'completed' ? 'completed' : 'active';
    const latest = goal.history?.[goal.history.length - 1];

    if (!note && status === goal.status) {
      showToast('Запиши изменение или поменяй статус');
      return;
    }

    if (latest && normalizeGoalDate(latest.date || latest.recordedAt) === today &&
        String(latest.note || '').trim() === note && latest.status === status) {
      closeSheets();
      showToast('Этот прогресс уже записан сегодня');
      return;
    }

    const nextHistory = appendGoalHistoryEntry(goal.history || [], {
      date: today,
      note,
      status,
      kind: 'result',
      recordedAt: now
    });

    state.goals = [normalizeGoal({
      ...goal,
      status,
      history: nextHistory,
      updatedAt: now
    })];
  }

  persist();
  closeSheets();
  renderFitness();
  showToast('Результат записан');
}

function deleteGoal() {
  if (!state.goals.length) {
    closeSheets();
    return;
  }
  state.goals = [];
  persist();
  closeSheets();
  renderFitness();
  showToast('Цель удалена');
}

function openPlan() {
  const sheet = document.getElementById('plan-sheet');
  if (!sheet) return;
  document.querySelectorAll('.day-picker').forEach(row => {
    const day = row.dataset.day;
    const input = row.querySelector('.day-input');
    if (input) input.value = state.plan[day] || '';
  });
  sheet.hidden = false;
}

function savePlan(){
  // Freeze all past results against the old plan before replacing it.
  syncAttendanceByDate();
  const newPlan = {};
  document.querySelectorAll('.day-picker').forEach(row => {
    const day = row.dataset.day;
    const input = row.querySelector('.day-input');
    const value = input ? input.value.trim() : '';
    if (value) newPlan[day] = value;
  });
  state.plan = newPlan;
  state.planMeta = { effectiveFrom: getDateKey(new Date()) };
  persist();
  closeSheets();
  renderFitness();
  showToast('План сохранён');
}

function startWorkout() {
  const now = new Date();
  const day = getDayKey(now);
  const workout = state.plan[day];
  const todayKey = getDateKey(now);

  if (getCompletedWorkoutForDate(now)) {
    showToast('Сегодняшняя тренировка уже завершена');
    return;
  }

  if (state.activeWorkout) {
    const activeDate = new Date(state.activeWorkout.started);
    if (!Number.isNaN(activeDate.getTime()) && getDateKey(activeDate) === todayKey) {
      showToast('Тренировка уже идёт');
      return;
    }
  }

  if (!workout) {
    showToast('Сегодня тренировка не запланирована');
    return;
  }

  if (workout === 'Отдых') {
    showToast('Сегодня день отдыха');
    return;
  }

  state.activeWorkout = {
    started: now.toISOString(),
    workout,
    day,
    dateKey: todayKey
  };

  // Purple means the planned workout was actually started.
  state.attendance[todayKey] = 'active';

  persist();
  renderFitness();
  showToast('Тренировка начата');
}

function finishWorkout() {
  if (!state.activeWorkout) return;

  const startedDate = new Date(state.activeWorkout.started);
  if (Number.isNaN(startedDate.getTime())) {
    state.activeWorkout = null;
    persist();
    renderFitness();
    return;
  }

  const finishedDate = new Date();
  const elapsed = getWorkoutElapsedSeconds(state.activeWorkout, finishedDate.getTime());
  const workoutDateKey = state.activeWorkout.dateKey || getDateKey(startedDate);

  // Keep one canonical completed session for a calendar date.
  state.history = state.history.filter(h => {
    if (h.dateKey) return h.dateKey !== workoutDateKey;
    const historyDate = new Date(h.date);
    return Number.isNaN(historyDate.getTime()) || getDateKey(historyDate) !== workoutDateKey;
  });

  state.history.push({
    date: finishedDate.toISOString(),
    dateKey: workoutDateKey,
    started: startedDate.toISOString(),
    ended: finishedDate.toISOString(),
    day: state.activeWorkout.day,
    workout: state.activeWorkout.workout,
    duration: elapsed,
    status: 'completed'
  });

  // A completed session stays purple in the compact calendar.
  state.attendance[workoutDateKey] = 'done';
  state.activeWorkout = null;
  persist();
  renderFitness();
  showToast('Тренировка завершена');
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
    el.textContent = formatWorkoutDuration(sec);

    // The timer, weekly total and graph use the same timestamp snapshot,
    // so the same workout can never show two different elapsed values.
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
  reconcileStaleActiveWorkout(false);
  persistLocal();
  renderFitness();
  syncTrainingWithServer();
});
