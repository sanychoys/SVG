"""V46 main user journeys: Chromium+mocked API, not a live Telegram/iPhone certification."""
import asyncio
from playwright.async_api import async_playwright
from smoke_browser_v43 import STORAGE_STUB, bundled_html

async def run():
 html=bundled_html()
 async with async_playwright() as p:
  browser=await p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
  try:
   for width in (320,375,390,430):
    context=await browser.new_context(viewport={'width':width,'height':844})
    await context.add_init_script(STORAGE_STUB)
    page=await context.new_page(); errors=[]
    page.on('pageerror',lambda err:errors.append(str(err)))
    await page.goto('about:blank');await page.set_content(html,wait_until='domcontentloaded',timeout=30000)
    await page.wait_for_timeout(750)
    # Main information architecture: primary spheres > quick actions > advanced tools.
    nav=await page.evaluate('''()=>[...document.querySelectorAll('.feature-grid,.v46-fast-section,.home-toolbox')].map(x=>x.className)''')
    assert nav==['feature-grid','v46-fast-section','home-toolbox'],(width,nav)
    assert await page.locator('.feature-card[role="button"][tabindex="0"]').count()==4
    assert await page.locator('.v46-fast-grid button').count()==4
    # Keyboard navigation works for main cards.
    await page.locator('.feature-card.notes').focus();await page.keyboard.press('Enter')
    assert not await page.locator('#notes-screen').evaluate('(el)=>el.hidden')
    # Empty notes CTA and filter reset; no disappearing content after filtering.
    assert await page.locator('#notes-list .v46-empty button').inner_text()=='Создать заметку'
    await page.locator('[data-notes-filter="favorites"]').click()
    assert not await page.locator('#v46-notes-reset').evaluate('(el)=>el.hidden')
    await page.locator('#notes-list .v46-empty button').click()
    assert await page.locator('#v46-notes-reset').evaluate('(el)=>el.hidden')
    await page.locator('#notes-list .v46-empty button').click()
    assert not await page.locator('#note-editor-sheet').evaluate('(el)=>el.hidden')
    # Cancel protects edits; no API writes.
    await page.locator('#note-title-input').fill('Незаконченная идея')
    await page.evaluate('window.confirm=()=>false;closeNoteEditor()')
    assert not await page.locator('#note-editor-sheet').evaluate('(el)=>el.hidden')
    await page.evaluate('window.confirm=()=>true;closeNoteEditor()')
    assert await page.locator('#note-editor-sheet').evaluate('(el)=>el.hidden')
    # Mock CRUD for notes and schedule; double save creates exactly one.
    await page.evaluate('''()=>{
      window.v46Data={notes:[],events:[],noteSaves:0,eventSaves:0};
      const parent=window.fetch;
      window.fetch=async function(url,options={}){
        const u=String(url),method=(options.method||'GET').toUpperCase(), d=window.v46Data;
        const response=(x)=>({ok:true,status:200,json:async()=>x});
        if(u==='/api/notes'&&method==='POST'){
           d.noteSaves++;await new Promise(r=>setTimeout(r,45));const note={...JSON.parse(options.body),id:'note-'+d.noteSaves};d.notes.push(note);return response({status:'ok',note});
        }
        if(u==='/api/notes'&&method==='GET')return response({status:'ok',notes:d.notes});
        if(u==='/api/schedule/events'&&method==='POST'){
          d.eventSaves++;await new Promise(r=>setTimeout(r,45));const event={...JSON.parse(options.body),id:'event-'+d.eventSaves,is_owner:true};d.events.push(event);return response({status:'ok',event});
        }
        if(u==='/api/schedule/events'&&method==='GET')return response({status:'ok',events:d.events});
        return parent(url,options);
      }
    }''')
    await page.evaluate('openNoteEditor()')
    await page.locator('#note-title-input').fill('Первая идея')
    await page.evaluate('''()=>{saveNote();saveNote()}''')
    await page.wait_for_timeout(160)
    created=await page.evaluate('window.v46Data.noteSaves')
    assert created==1,(width,created)
    assert await page.locator('#note-editor-sheet').evaluate('(el)=>el.hidden')
    await page.evaluate('closeNotes()')
    # Quick finance action visible without long scrolling. Changes guarded.
    await page.locator('.v46-fast-grid button').filter(has_text='Расход').click()
    assert not await page.locator('#finance-entry-sheet').evaluate('(el)=>el.hidden')
    await page.locator('#finance-entry-title').fill('Кофе')
    await page.evaluate('window.confirm=()=>false;closeSheets()')
    assert not await page.locator('#finance-entry-sheet').evaluate('(el)=>el.hidden')
    await page.evaluate('window.confirm=()=>true;closeSheets()')
    assert await page.locator('#finance-entry-sheet').evaluate('(el)=>el.hidden')
    assert await page.locator('.finance-shell>.v46-finance-add').count()==1
    await page.evaluate('closeFinance()')
    # Day has actionable empty schedule; validate time, guard unsaved content, double-save.
    await page.locator('.v46-fast-grid button').filter(has_text='Событие').click()
    assert not await page.locator('#schedule-editor-sheet').evaluate('(el)=>el.hidden')
    await page.locator('#schedule-event-title').fill('Математика')
    await page.evaluate('window.confirm=()=>false;closeScheduleEditor()')
    assert not await page.locator('#schedule-editor-sheet').evaluate('(el)=>el.hidden')
    await page.evaluate('''()=>{let a=document.getElementById('schedule-event-time');document.getElementById('schedule-event-end-time').value=a.value;}''')
    await page.evaluate('saveScheduleEvent()');await page.wait_for_timeout(100)
    assert await page.evaluate('window.v46Data.eventSaves')==0
    await page.evaluate('''()=>{const s=document.getElementById('schedule-event-time').value;const [h,m]=s.split(':').map(Number);document.getElementById('schedule-event-end-time').value=`${String((h+1)%24).padStart(2,'0')}:${String(m).padStart(2,'0')}`;}''')
    await page.evaluate('''()=>{saveScheduleEvent();saveScheduleEvent();}''')
    await page.wait_for_timeout(170)
    assert await page.evaluate('window.v46Data.eventSaves')==1
    assert await page.locator('#schedule-editor-sheet').evaluate('(el)=>el.hidden')
    await page.evaluate('closeSchedule()')
    # Profile and nested settings/shortcuts screens.
    await page.evaluate('openProfileDrawer()')
    assert not await page.locator('#profile-drawer').evaluate('(el)=>el.hidden')
    await page.evaluate("showProfileView('settings')")
    assert not await page.locator('#profile-settings-view').evaluate('(el)=>el.hidden')
    await page.evaluate("showProfileView('shortcuts')")
    assert not await page.locator('#profile-shortcuts-view').evaluate('(el)=>el.hidden')
    await page.evaluate("showProfileView('security')")
    assert not await page.locator('#profile-security-view').evaluate('(el)=>el.hidden')
    await page.evaluate('closeProfileDrawer()');await page.wait_for_timeout(280)
    assert await page.locator('#profile-drawer').evaluate('(el)=>el.hidden')
    # Other user-facing modules render and close.
    for fn,screen in [('openGlobalSearch','global-search-screen'),('openActivityAnalytics','activity-analytics-screen'),('openFinancialAnalytics','finance-analytics-screen'),('openAutomations','automations-screen'),('openSVGAssistant','svg-assistant-screen')]:
      await page.evaluate('''name=>window[name]()''',fn)
      assert not await page.locator('#'+screen).evaluate('(el)=>el.hidden'),(width,fn)
    await page.wait_for_timeout(150)
    assert not await page.evaluate('document.documentElement.scrollWidth>window.innerWidth'),(width,'overflow')
    assert not errors,(width,errors)
    if width==390:
      await page.evaluate('closeV35Screen("svg-assistant-screen")')
      await page.screenshot(path='/mnt/data/svg_v46/V46_Home_390.png',full_page=True)
    print(f'V46: {width}px primary flows, notes/schedule saves, dirty guards, finance, profile, tools, no JS errors')
    await context.close()
  finally:await browser.close()
if __name__=='__main__':asyncio.run(run())
