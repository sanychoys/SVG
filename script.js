// Telegram WebApp diagnostics and user binding
document.addEventListener('DOMContentLoaded', function initTelegramDebug(){
  console.log("[SVGTracker] start");

  const telegramApp = window.Telegram?.WebApp;
  console.log("[SVGTracker] Telegram SDK:", telegramApp);

  if (!telegramApp) {
    console.warn("[SVGTracker] Opened outside Telegram");
    window.SVG_TELEGRAM_USER = null;
    return;
  }

  telegramApp.ready();
  telegramApp.expand();

  console.log("[SVGTracker] initData:", telegramApp.initData);
  console.log("[SVGTracker] initDataUnsafe:", telegramApp.initDataUnsafe);

  const tgUser = telegramApp.initDataUnsafe?.user || null;
  window.SVG_TELEGRAM_USER = tgUser;

  console.log("[SVGTracker] User:", tgUser);

  if (!tgUser) {
    console.warn("[SVGTracker] Telegram user missing");
    return;
  }

  const name = document.getElementById("name");
  if (name && tgUser.first_name) {
    name.textContent = tgUser.first_name;
  }

  const avatar = document.getElementById("avatar");
  const fallback = document.getElementById("avatar-fallback");

  console.log("[SVGTracker] avatar element:", avatar);
  console.log("[SVGTracker] photo_url:", tgUser.photo_url);

  if (avatar && tgUser.photo_url) {
    avatar.onload = () => {
      console.log("[SVGTracker] Avatar loaded");
      avatar.style.display = "block";
      if (fallback) fallback.style.display = "none";
    };

    avatar.onerror = (e) => {
      console.error("[SVGTracker] Avatar load error", e);
    };

    avatar.src = tgUser.photo_url;
  } else {
    console.warn("[SVGTracker] No photo_url");
  }
});

const tg = window.Telegram?.WebApp;

const STORAGE = {
  goals: 'fitness_goals',
  plan: 'fitness_plan',
  history: 'fitness_history',
  attendance: 'fitness_attendance',
  planMeta: 'fitness_plan_meta_v2',
  finance: 'finance_budget_v2'
};

let selectedDay = null;
let calendarCursor = new Date(new Date().getFullYear(), new Date().getMonth(), 1);

let financeData = JSON.parse(localStorage.getItem(STORAGE.finance) || 'null') || {
  monthlyIncome:0,
  mandatoryExpenses:[],
  expenses:[],
  debts:[],
  categories:{},
  budgetHistory:[]
};



const state = {
  goals: JSON.parse(localStorage.getItem(STORAGE.goals) || '[]'),
  plan: JSON.parse(localStorage.getItem(STORAGE.plan) || '{}'),
  history: JSON.parse(localStorage.getItem(STORAGE.history) || '[]'),
  attendance: JSON.parse(localStorage.getItem(STORAGE.attendance) || '{}'),
  planMeta: JSON.parse(localStorage.getItem(STORAGE.planMeta) || 'null') || { effectiveFrom: null },
  activeWorkout: JSON.parse(localStorage.getItem('active_workout') || 'null')
};

if (!state.planMeta.effectiveFrom && Object.keys(state.plan).length) {
  // Older builds stored attendance only by weekday, so their exact historical dates
  // cannot be reconstructed safely. Start date-based tracking from this build onward.
  state.planMeta.effectiveFrom = getDateKey(new Date());
}

function persist() {
  localStorage.setItem(STORAGE.goals, JSON.stringify(state.goals));
  localStorage.setItem(STORAGE.plan, JSON.stringify(state.plan));
  localStorage.setItem(STORAGE.history, JSON.stringify(state.history));
  localStorage.setItem(STORAGE.attendance, JSON.stringify(state.attendance));
  localStorage.setItem(STORAGE.planMeta, JSON.stringify(state.planMeta));
  localStorage.setItem('active_workout', JSON.stringify(state.activeWorkout));
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
  renderGoals();
  renderPlan();
  renderToday();
  renderCalendar();
  renderActivityChart();
}

function renderGoals() {
  const box = document.querySelector('.goals-widget');
  if (!box) return;

  const goal = state.goals[0];
  const title = box.querySelector('h3');
  const values = box.querySelector('.goal-values');
  const chart = box.querySelector('.goal-line-path');

  if (goal) {
    title.textContent = goal.name;
    values.textContent = `${goal.current} → ${goal.target}`;
    chart?.setAttribute('d', 'M0 70 C60 60 110 62 160 45 C220 38 270 25 320 18');
  } else {
    title.textContent = 'Создай первую цель';
    values.textContent = 'Добавь цель, чтобы отслеживать прогресс';
    chart?.setAttribute('d', 'M0 70 C80 70 160 70 320 70');
  }

  const button = box.querySelector('.widget-action');
  if (button) button.onclick = addGoal;
}

function renderPlan() {
  const plan = document.querySelector('.week-widget');
  if (!plan) return;

  plan.querySelectorAll('.dynamic-plan-row, .empty-plan').forEach(e => e.remove());

  const entries = Object.entries(state.plan);
  if (!entries.length) {
    plan.insertAdjacentHTML('beforeend', `
      <div class="empty-plan">
        <p>Настрой свой тренировочный график</p>
        <button onclick="openPlan()">Создать план</button>
      </div>`);
  } else {
    entries.forEach(([day, workout]) => {
      plan.insertAdjacentHTML('beforeend',
        `<div class="workout-line dynamic-plan-row"><small>${day}</small><strong>${workout}</strong></div>`
      );
    });
  }

  const button = plan.querySelector('.widget-action');
  if (button) button.onclick = editPlan;
}

function renderToday() {
  const box = document.querySelector('.today-widget');
  if (!box) return;

  const days = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];
  const today = days[new Date().getDay()];
  const workout = state.plan[today];

  const title = box.querySelector('h2');
  const eyebrow = box.querySelector('.module-eyebrow');
  if (eyebrow) eyebrow.textContent = 'Сегодня';

  if (workout && workout !== 'Отдых') {
    title.textContent = workout;
  } else if (workout === 'Отдых') {
    title.textContent = 'День отдыха';
  } else {
    title.textContent = 'Нет запланированной тренировки';
  }

  const button = box.querySelector('.start-button');
  if (!button) return;

  if (workout && workout !== 'Отдых') {
    if (state.activeWorkout) {
      button.textContent = 'Идёт тренировка';
      button.onclick = () => {
        if(confirm('Завершить тренировку?')) finishWorkout();
      };
      updateWorkoutTimer();
    } else {
      button.textContent = 'Начать тренировку';
      button.onclick = startWorkout;
    }
  } else {
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
  const workout = state.plan?.[getDayKey(current)];
  const effectiveFrom = getPlanEffectiveDate();

  if (isWorkoutCompletedOnDate(current) || state.attendance[key] === 'done') return 'done';

  if (state.activeWorkout) {
    const started = new Date(state.activeWorkout.started);
    if (!Number.isNaN(started.getTime()) && getDateKey(started) === key) return 'active';
  }

  if (current > today) return workout && workout !== 'Отдых' ? 'future scheduled' : 'future';

  if (current.getTime() === today.getTime()) {
    return workout && workout !== 'Отдых' ? 'scheduled' : 'rest';
  }

  // We cannot infer exact attendance for dates that predate date-based tracking.
  if (!effectiveFrom || current < effectiveFrom) return 'untracked';

  if (!workout || workout === 'Отдых') return 'rest';
  return state.attendance[key] === 'missed' ? 'missed' : 'missed';
}

function getCalendarStatusLabel(status) {
  if (status.includes('done')) return 'тренировка выполнена';
  if (status.includes('active')) return 'тренировка идёт';
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
      month: 'long',
      year: 'numeric'
    }).format(calendarCursor).replace(' г.', '');
  }

  if (nextButton) {
    const currentMonth = new Date(now.getFullYear(), now.getMonth(), 1);
    nextButton.disabled = isSameMonth(calendarCursor, currentMonth);
  }

  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const firstDay = (new Date(year, month, 1).getDay() + 6) % 7; // Monday = 0
  const totalSlots = Math.ceil((firstDay + daysInMonth) / 7) * 7; // Complete rows only; no stretched empty sixth row.
  let html = '';

  for (let i = 0; i < firstDay; i++) {
    html += '<span class="calendar-day empty" aria-hidden="true"></span>';
  }

  for (let day = 1; day <= daysInMonth; day++) {
    const date = new Date(year, month, day);
    const status = getCalendarStatus(date);
    const isToday = getDateKey(date) === getDateKey(now);
    const label = getCalendarStatusLabel(status);
    const monthName = new Intl.DateTimeFormat('ru-RU', { month: 'long' }).format(date);

    html += `<span class="calendar-day ${status}${isToday ? ' today' : ''}" aria-label="${day} ${monthName}: ${label}">${day}</span>`;
  }

  const usedSlots = firstDay + daysInMonth;
  for (let i = usedSlots; i < totalSlots; i++) {
    html += '<span class="calendar-day empty" aria-hidden="true"></span>';
  }

  grid.innerHTML = html;
}

function renderActivityChart(){
  const line = document.querySelector('.chart-line-path');
  const area = document.querySelector('.chart-area-path');
  const points = document.querySelector('.chart-point-group');
  if(!line || !area || !points) return;

  const today = startOfDay(new Date());
  const monday = new Date(today);
  monday.setDate(today.getDate() - ((today.getDay() + 6) % 7));

  const values = Array.from({length: 7}, (_, index) => {
    const date = new Date(monday);
    date.setDate(monday.getDate() + index);
    const status = getCalendarStatus(date);
    if (status.includes('done')) return 92;
    if (status.includes('active')) return 76;
    if (status.includes('missed')) return 24;
    if (status.includes('scheduled')) return 56;
    if (status.includes('rest')) return 18;
    return 38;
  });

  const coords = values.map((value, index) => ({
    x: Number((index * (320 / 6)).toFixed(1)),
    y: Number((108 - (value / 100 * 85)).toFixed(1))
  }));

  const path = coords.map((point, index) => `${index ? 'L' : 'M'}${point.x} ${point.y}`).join(' ');
  line.setAttribute('d', path);
  area.setAttribute('d', `${path} V116 H0 Z`);
  points.innerHTML = coords.map(point => `<circle cx="${point.x}" cy="${point.y}" r="3.2"/>`).join('');
}


function addGoal() {
  const goal = state.goals[0];
  document.getElementById('goal-name').value = goal?.name || '';
  document.getElementById('goal-current').value = goal?.current || '';
  document.getElementById('goal-target').value = goal?.target || '';
  document.getElementById('goal-sheet').hidden = false;
}

function editPlan() {
  openPlan();
}

function openGoal() {
  addGoal();
}

function saveGoal() {
  state.goals = [{
    name: document.getElementById('goal-name').value || 'Новая цель',
    current: document.getElementById('goal-current').value || 0,
    target: document.getElementById('goal-target').value || 0
  }];

  persist();
  closeSheets();
  renderFitness();
  showToast('Цель сохранена');
}

function deleteGoal() {
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

  if (!workout) {
    showToast('Сегодня тренировка не запланирована');
    return;
  }

  if (workout === 'Отдых') {
    showToast('Сегодня день отдыха');
    return;
  }

  state.activeWorkout={started:now.toISOString(), workout, day};

  // Starting a workout is not the same as completing it. Track it by exact date.
  state.attendance[getDateKey(now)] = 'active';

  persist();
  renderCalendar();
  renderFitness();
  showToast('Тренировка начата');
}


function finishWorkout(){
  if(!state.activeWorkout) return;

  const elapsed = Math.floor((Date.now()-new Date(state.activeWorkout.started).getTime())/1000);
  const started = state.activeWorkout.started;
  const finished = new Date().toISOString();

  const workoutDate = new Date(started);
  const workoutDateKey = getDateKey(workoutDate);

  state.history = state.history.filter(h => {
    if (h.dateKey) return h.dateKey !== workoutDateKey;
    const historyDate = new Date(h.date);
    return Number.isNaN(historyDate.getTime()) || getDateKey(historyDate) !== workoutDateKey;
  });
  state.history.push({
    date: finished,
    dateKey: workoutDateKey,
    started,
    ended: finished,
    day: state.activeWorkout.day,
    workout: state.activeWorkout.workout,
    duration: elapsed,
    status:'completed'
  });

  state.attendance[workoutDateKey] = 'done';
  state.activeWorkout=null;
  persist();
  renderFitness();
  showToast('Тренировка завершена');
}

function formatWorkoutTime(seconds){
  const h=Math.floor(seconds/3600);
  const m=Math.floor((seconds%3600)/60);
  const s=seconds%60;
  return h ? `${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}` : `${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
}

function updateWorkoutTimer(){
  const el=document.querySelector('.workout-timer');
  if(!el || !state.activeWorkout) return;
  const sec=Math.floor((Date.now()-new Date(state.activeWorkout.started).getTime())/1000);
  el.textContent=formatWorkoutTime(sec);
  setTimeout(updateWorkoutTimer,1000);
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
  renderFitness();
});
