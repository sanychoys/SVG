"""V47 isolated Chromium tests; mocked Telegram/API. Not proof of real iOS/VPS."""
import asyncio, re
from pathlib import Path
from playwright.async_api import async_playwright
from smoke_browser_v43 import STORAGE_STUB,MOCK_API
ROOT=Path(__file__).resolve().parents[1]

def html_bundle():
    html=(ROOT/'index.html').read_text()
    for name in ('style.css','product.css'):
        html,n=re.subn(r'<link rel="stylesheet" href="/'+re.escape(name)+r'\?v=47">', lambda m:'<style>'+(ROOT/name).read_text()+'</style>',html)
        assert n==1,name
    for name in ('product.js','script.js'):
        html,n=re.subn(r'<script src="/'+re.escape(name)+r'\?v=47"[^>]*></script>',lambda m:'<script>'+(ROOT/name).read_text()+'</script>',html)
        assert n==1,name
    html=html.replace('<head>','<head><script>'+MOCK_API+'</script>',1)
    return html.replace('https://telegram.org/js/telegram-web-app.js','data:text/javascript,')

async def run():
  html=html_bundle()
  async with async_playwright() as p:
    browser=await p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    try:
      for width in (320,375,390,430):
        context=await browser.new_context(viewport={'width':width,'height':844})
        await context.add_init_script(STORAGE_STUB)
        page=await context.new_page()
        errors=[]
        page.on('pageerror',lambda err:errors.append(str(err)))
        await page.goto('about:blank')
        await page.set_content(html,wait_until='domcontentloaded',timeout=25000)
        await page.wait_for_timeout(280)
        assert await page.locator('.v46-fast-section').count()==0
        assert await page.locator('.feature-card').count()==4
        await page.evaluate('openTraining()')
        days=page.locator('.attendance-grid button.calendar-day')
        assert await days.count()>=28
        assert await days.first.inner_text()==''
        assert 'октя' in (await page.locator('.attendance-grid button.today').get_attribute('aria-label') or '').lower() or await days.first.get_attribute('aria-label')
        geometry=await page.evaluate('''()=>{
            const cal=document.querySelector('.attendance-widget').getBoundingClientRect(),
                  week=document.querySelector('.week-widget').getBoundingClientRect(),
                  d=document.querySelector('.attendance-grid .calendar-day:not(.empty)').getBoundingClientRect();
            return {cal:cal.toJSON(),week:week.toJSON(),dot:d.toJSON()};
        }''')
        assert geometry['cal']['right']<=geometry['week']['left']+1,(width,geometry)
        assert geometry['week']['right']<=width+1,(width,geometry)
        assert geometry['dot']['width']<=25,(width,geometry)
        await page.locator('.attendance-grid .calendar-day.today').click()
        assert not await page.locator('#calendar-workout-sheet').evaluate('(e)=>e.hidden')
        await page.evaluate('requestCloseCalendarWorkout()')
        await page.evaluate('closeTraining()')
        # Schedule-specific custom repeat configuration, no persistence to server in browser mock.
        await page.evaluate('openSchedule();openScheduleEditor()')
        await page.locator('#schedule-event-reminder').select_option('10')
        assert not await page.locator('#schedule-reminder-repeat-controls').evaluate('(el)=>el.hidden')
        await page.locator('#schedule-event-repeat-count').select_option('4')
        await page.locator('#schedule-event-repeat-interval').select_option('5')
        await page.locator('#schedule-event-reminder').select_option('-1')
        assert await page.locator('#schedule-reminder-repeat-controls').evaluate('(el)=>el.hidden')
        await page.evaluate('closeScheduleEditor(true);closeSchedule()')
        await page.evaluate('openNotes();openNoteEditor()')
        await page.evaluate("document.getElementById('note-reminder-enabled').click()")
        assert not await page.locator('#note-reminder-field').evaluate('(el)=>el.hidden')
        await page.locator('#note-repeat-count').select_option('3')
        await page.locator('#note-repeat-interval').select_option('5')
        await page.evaluate('closeNoteEditor(true);closeNotes()')
        # API defaults and switches: mock response must expose new typed preferences.
        await page.evaluate('''()=>{const old=window.fetch;window.fetch=async function(url,opts={}){
           if(String(url)==='/api/notifications/preferences'){
               if(opts.method==='PUT'){
                 const patch=JSON.parse(opts.body).preferences;window.v47Updated=patch;
                 return {ok:true,status:200,json:async()=>({status:'ok',preferences:{default_reminder_count:3,default_reminder_interval:10,...patch}})};
               }
               return {ok:true,status:200,json:async()=>({status:'ok',preferences:{default_reminder_count:1,default_reminder_interval:5,personal_reminders:true,note_reminders:true}})};
           }
           return old(url,opts);
        }}''')
        await page.evaluate('openNotificationSettings()')
        await page.wait_for_timeout(70)
        assert await page.locator('#notification-repeat-count').input_value()=='1'
        await page.locator('#notification-repeat-count').select_option('3')
        await page.wait_for_timeout(90)
        assert (await page.evaluate('window.v47Updated')).get('default_reminder_count')==3
        await page.locator('#notification-repeat-interval').select_option('10')
        await page.wait_for_timeout(90)
        assert (await page.evaluate('window.v47Updated')).get('default_reminder_interval')==10
        assert not await page.evaluate('document.documentElement.scrollWidth>window.innerWidth'),(width,'overflow')
        assert not errors,(width,errors)
        if width in (320,390):
            await page.evaluate('closeNotificationSettings();openTraining()')
            await page.screenshot(path=f'/mnt/data/svg_v47/training_{width}.png',full_page=False)
        print('PASS',width,'calendar compact side-by-side; repeat forms, notifications, no JS errors')
        await context.close()
    finally:await browser.close()
if __name__=='__main__':asyncio.run(run())
