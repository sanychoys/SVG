"""Isolated browser smoke: no VPS/Telegram calls. Execute directly.

Uses the shipped HTML, JS and CSS in Chromium under an opaque origin, with a
synthetic Telegram SDK and mocked API. Does not claim to verify iOS WebView.
"""
import asyncio
import re
from pathlib import Path
from playwright.async_api import async_playwright

ROOT=Path(__file__).resolve().parents[1]
STORAGE_STUB=r"""
Object.defineProperty(window,'localStorage',{value:{_d:{},getItem(k){return this._d[k]??null},setItem(k,v){this._d[k]=String(v)},removeItem(k){delete this._d[k]}}});
Object.defineProperty(window,'sessionStorage',{value:{_d:{},getItem(k){return this._d[k]??null},setItem(k,v){this._d[k]=String(v)},removeItem(k){delete this._d[k]}}});
window.TelegramWebviewProxy={};
window.Telegram={WebApp:{initData:'test-signed-in-browser-simulation',initDataUnsafe:{user:{id:8430,first_name:'Тест'}},ready(){},expand(){},colorScheme:'dark',HapticFeedback:{},MainButton:{}}};
"""
MOCK_API=r"""
window.fetch=async function(url,init={}){
 let value={status:'ok'}, u=String(url);
 if(u.includes('/api/training/state')||u.includes('/api/finance/state')) value={status:'ok',exists:false,state:null};
 else if(u.includes('/api/profile')) value={status:'ok',friends:[],incoming:[],outgoing:[],blocked:[],bot_notifications_enabled:true};
 else if(u.includes('/api/schedule/events')) value={status:'ok',events:[]};
 else if(u.includes('/api/notes')) value={status:'ok',notes:[]};
 else if(u.includes('/api/analytics/activity')) value={status:'ok',analytics:{active_days:0,totals:{workouts:0,training_minutes:0,events:0,notes_updated:0,expenses:0,income:0},days:[]}};
 else if(u.includes('/api/analytics/finance')) {let blank={total_spent:0,paid_mandatory:0,income:0,budget:0,available:0,expense_count:0,categories:[]};value={status:'ok',analytics:{current:blank,previous:blank,trend:[]}};}
 else if(u.includes('/api/automations')) value={status:'ok',rules:[]};
 else if(u.includes('/api/notifications/preferences')) value={status:'ok',preferences:{}};
 else if(u.includes('/api/search'))value={status:'ok',results:[]};
 else if(u.includes('/api/reports')) value={status:'ok',report:{start:'2026-10-22',end:'2026-10-22',expenses:0,income:0,workouts:0,training_minutes:0,events:0,notes_updated:0}};
 else if(u.includes('/api/assistant'))value={status:'ok',answer:'Данные загружены'};
 else if(u.includes('/api/shortcut/setup'))value={status:'ok',active:false};
 return {ok:true,status:200,json:async()=>value};
};
"""

def bundled_html():
 html=(ROOT/'index.html').read_text()
 for name in ('style.css','product.css'):
  pattern=r'<link rel="stylesheet" href="/'+re.escape(name)+r'\?v=46">'
  html,n=re.subn(pattern,lambda _:'<style>'+ (ROOT/name).read_text()+'</style>',html)
  assert n==1,name
 for name in ('product.js','script.js'):
  pattern=r'<script src="/'+re.escape(name)+r'\?v=46"[^>]*></script>'
  html,n=re.subn(pattern,lambda _:'<script>'+ (ROOT/name).read_text()+'</script>',html)
  assert n==1,name
 html=html.replace('<head>','<head><script>'+MOCK_API+'</script>',1)
 html=html.replace('https://telegram.org/js/telegram-web-app.js','data:text/javascript,')
 return html

async def run():
 html=bundled_html()
 async with async_playwright() as p:
  browser=await p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
  try:
   for width in (320,375,390,430):
    context=await browser.new_context(viewport={'width':width,'height':844})
    await context.add_init_script(STORAGE_STUB)
    page=await context.new_page()
    errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    await page.goto('about:blank')
    await page.set_content(html,wait_until='domcontentloaded',timeout=20000)
    await page.evaluate('''()=>{document.documentElement.classList.remove('svg-web');document.documentElement.classList.add('svg-miniapp');applyTelegramIdentity();}''')
    await page.wait_for_timeout(800)
    state=await page.evaluate('''()=>({ready:window.SVG_APP_DATA_READY,overflow:document.documentElement.scrollWidth>window.innerWidth})''')
    assert state['ready'],f'V44 failed Mini App bootstrap: {state}'
    assert not state['overflow'],f'V44 horizontal overflow: {state}'
    # Nav functions are exercised, not merely existence-checked.
    for open_name,screen_name in [
     ('openSchedule','schedule-screen'),('openNotes','notes-screen'),
     ('openGlobalSearch','global-search-screen'),('openActivityAnalytics','activity-analytics-screen'),
     ('openFinancialAnalytics','finance-analytics-screen'),('openAutomations','automations-screen'),
     ('openSVGAssistant','svg-assistant-screen')
    ]:
     await page.evaluate('''fn=>window[fn]()''',open_name)
     assert not await page.locator('#'+screen_name).evaluate('(e)=>e.hidden'), (width,open_name)
    await page.evaluate("closeV35Screen('svg-assistant-screen')")
    # End-to-end regression: first GET is stale, forced refresh must read again.
    race=await page.evaluate('''async()=>{
      let current=0, noteReads=0;
      const former=window.fetch;
      window.fetch=async (url,opts)=>{
        let p=String(url);
        if(p.includes('/api/schedule/events'))return {ok:true,status:200,json:async()=>({status:'ok',events:[]})};
        if(p.includes('/api/notes')){
          const version=current;noteReads++;
          await new Promise(resolve=>setTimeout(resolve,60));
          return {ok:true,status:200,json:async()=>({status:'ok',notes:version?[{id:'n-new',title:'Новое',body:'Есть'}]:[]})};
        }
        return former(url,opts);
      };
      const first=syncScheduleNotesWithServer();
      await new Promise(resolve=>setTimeout(resolve,10));
      current=1;
      const second=syncScheduleNotesWithServer(true);
      await Promise.all([first,second]);
      const result={noteReads,found:notesData.some(n=>n.id==='n-new')};
      window.fetch=former;
      return result;
    }''')
    assert race['noteReads']>=2 and race['found'],(width,race)
    if width == 390:
     wallet=await page.evaluate('''async()=>{
       const prior=window.fetch;
       let count=0, saved=null;
       window.fetch=async(url,opts={})=>{
         if(String(url).includes('/api/finance/state')&&opts.method==='PUT'){
           const body=JSON.parse(opts.body);count++;
           if(count===1)await new Promise(r=>setTimeout(r,80));
           saved=body.state;
           return {ok:true,status:200,json:async()=>({status:'ok',updated_at:'2026-10-22T11:00:'+count+'Z'})};
         }
         if(String(url).includes('/api/finance/state'))return {ok:true,status:200,json:async()=>({status:'ok',exists:true,state:saved,updated_at:'2026-10-22T11:00:01Z'})};
         return prior(url,opts);
       };
       financeData=normalizeFinanceDataV15({expenses:[{id:'a',title:'Первый',amount:10,date:'2026-10-22'}]});
       financeServerUpdatedAtV15=null;
       const first=saveFinanceToServerV15();
       await new Promise(r=>setTimeout(r,10));
       financeData.expenses.push({id:'b',title:'Второй',amount:20,date:'2026-10-22',updatedAt:new Date().toISOString(),categoryId:financeData.categories[0].id});
       financeTouchV15();localStorage.setItem(FINANCE_DIRTY_KEY_V15,'1');
       scheduleFinanceSyncV15();
       await first;
       await new Promise(r=>setTimeout(r,1250));
       window.fetch=prior;
       return {count,items:saved?.expenses?.map(x=>x.id),dirty:localStorage.getItem(FINANCE_DIRTY_KEY_V15)};
     }''')
     assert wallet['count']>=2 and 'b' in (wallet['items'] or []),wallet
     print('Finance concurrent-edit smoke OK',wallet)
     workouts=await page.evaluate('''async()=>{
       const prior=window.fetch;let count=0,saved=null;
       window.fetch=async(url,opts={})=>{
        if(String(url).includes('/api/training/state')&&opts.method==='PUT'){
          const body=JSON.parse(opts.body);count++;
          if(count===1)await new Promise(r=>setTimeout(r,80));
          saved=body.state;
          return {ok:true,status:200,json:async()=>({status:'ok',updated_at:'2026-10-22T12:00:'+count+'Z'})};
        }
        return prior(url,opts);
       };
       state.goals=[];trainingServerUpdatedAt=null;
       const first=saveTrainingToServer();
       await new Promise(r=>setTimeout(r,10));
       state.goals.push({id:'g-new',type:'numeric',name:'Вторая цель',current:0,target:10});
       touchTrainingState();localStorage.setItem(STORAGE.trainingDirty,'1');
       await first;
       await new Promise(r=>setTimeout(r,850));
       window.fetch=prior;
       return {count,ids:saved?.goals?.map(g=>g.id),dirty:localStorage.getItem(STORAGE.trainingDirty)};
     }''')
     assert workouts['count']>=2 and 'g-new' in (workouts['ids'] or []),workouts
     print('Training concurrent-edit smoke OK',workouts)
    assert errors==[],(width,errors)
    print('Browser smoke OK',width,state,'notes race',race)
    await context.close()
  finally:
   await browser.close()

if __name__=='__main__': asyncio.run(run())
