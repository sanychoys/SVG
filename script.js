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
  finance: 'finance_budget_v2'
};

let selectedDay = null;

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
  attendance: JSON.parse(localStorage.getItem(STORAGE.attendance) || '{}')
};

function persist() {
  localStorage.setItem(STORAGE.goals, JSON.stringify(state.goals));
  localStorage.setItem(STORAGE.plan, JSON.stringify(state.plan));
  localStorage.setItem(STORAGE.history, JSON.stringify(state.history));
  localStorage.setItem(STORAGE.attendance, JSON.stringify(state.attendance));
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
    button.textContent = 'Начать тренировку';
    button.onclick = startWorkout;
  } else {
    button.textContent = 'Настроить план';
    button.onclick = openPlan;
  }
}

function renderCalendar() {
  const grid = document.querySelector('.attendance-grid');
  if (!grid) return;

  const days = ['Пн','Вт','Ср','Чт','Пт','Сб','Вс'];
  grid.innerHTML = days.map(day => {
    const status = state.attendance[day] || '';
    return `<button class="calendar-day ${status}" onclick="toggleAttendance('${day}')">${day}</button>`;
  }).join('');
}

function toggleAttendance(day) {
  const order = ['', 'done', 'missed', 'rest'];
  const current = state.attendance[day] || '';
  const next = order[(order.indexOf(current)+1)%order.length];
  if (next) state.attendance[day] = next;
  else delete state.attendance[day];
  persist();
  renderCalendar();
  renderActivityChart();
}

function renderActivityChart(){
  const line = document.querySelector('.chart-line-path');
  const area = document.querySelector('.chart-area-path');
  const points = document.querySelector('.chart-point-group');
  if(!line || !points) return;

  const days=['Пн','Вт','Ср','Чт','Пт','Сб','Вс'];
  const values = days.map(d => state.attendance[d]==='done' ? 90 : state.attendance[d]==='missed' ? 25 : state.attendance[d]==='rest' ? 10 : 45);
  const coords = values.map((v,i)=>`${i*53.3} ${108-(v/100*85)}`);
  const d = 'M'+coords.join(' C');
  line.setAttribute('d', d);
  area.setAttribute('d', d+' V116H0Z');
  points.innerHTML = coords.map(c=>{const [x,y]=c.split(' '); return `<circle cx="${x}" cy="${y}" r="3.2"/>`;}).join('');
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
  persist();
  closeSheets();
  renderFitness();
  showToast('План сохранён');
}

function startWorkout() {
  state.history.push(new Date().toISOString());
  persist();
  showToast('Тренировка начата');
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
