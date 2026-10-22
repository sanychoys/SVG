"""Isolated end-to-end browser tests for starting unscheduled sessions and date editing."""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright
from smoke_browser_v43 import STORAGE_STUB, MOCK_API, bundled_html

async def run():
    html = bundled_html()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
        try:
            for width in (320,390,430):
                context = await browser.new_context(viewport={'width':width,'height':844})
                await context.add_init_script(STORAGE_STUB)
                page = await context.new_page()
                errors = []
                page.on('pageerror', lambda err: errors.append(str(err)))
                await page.goto('about:blank')
                await page.set_content(html, wait_until='domcontentloaded', timeout=30000)
                await page.wait_for_timeout(280)
                await page.evaluate('openTraining()')
                # No week plan: primary button must start a real timer immediately.
                await page.locator('.today-widget .start-button').click()
                started = await page.evaluate('''()=>({session:state.activeWorkout, today:getDateKey(new Date()),label:document.querySelector('.today-widget .start-button').textContent})''')
                assert started['session'] and started['session']['workout']=='Свободная тренировка',started
                assert started['session']['dateKey']==started['today'],started
                assert started['label']=='Завершить тренировку',started
                await page.evaluate('confirmFinishWorkout()')
                completed = await page.evaluate('''()=>({count:state.history.length, active:state.activeWorkout, label:document.querySelector('.today-widget .start-button').textContent,disabled:document.querySelector('.today-widget .start-button').disabled})''')
                assert completed['count']==1 and completed['active'] is None,completed
                assert completed['label']=='Начать ещё' and not completed['disabled'],completed
                # Calendar day opens an editable form, not a passive toast.
                await page.locator('.attendance-grid .calendar-day.today').click()
                assert not await page.locator('#calendar-workout-sheet').evaluate('(el)=>el.hidden')
                await page.locator('[data-duration="90"]').click()
                await page.locator('#calendar-workout-name').fill('Плавание')
                await page.locator('#calendar-workout-save').click()
                after = await page.evaluate('''()=>{let d=new Date(),manual=getCalendarManualRecord(getDateKey(d));return {count:state.history.length,manual,seconds:getTrainingSecondsForDate(d),label:document.querySelector('.activity-total').textContent,dirty:localStorage.getItem(STORAGE.trainingDirty)}}''')
                assert after['count']==2 and after['manual']['duration']==5400 and after['manual']['workout']=='Плавание',after
                assert after['seconds']>=5400 and after['dirty']=='1',after
                await page.evaluate('''()=>openCalendarWorkoutEditor(new Date())''')
                await page.locator('[data-duration="120"]').click()
                await page.evaluate('saveCalendarWorkout()')
                edited=await page.evaluate('''()=>({count:state.history.length,seconds:getCalendarManualRecord(getDateKey(new Date())).duration})''')
                assert edited == {'count':2,'seconds':7200},edited
                # Delete via modal while preserving the genuine timer workout.
                await page.evaluate('''()=>{openCalendarWorkoutEditor(new Date());window.confirm=()=>true;}''')
                await page.evaluate('deleteCalendarWorkout()')
                deleted=await page.evaluate('''()=>({count:state.history.length,deleted:!!state.sync.tombstones.workouts[calendarManualId(getDateKey(new Date()))],status:getCalendarStatus(new Date()),label:document.querySelector('.activity-total').textContent})''')
                assert deleted['count']==1 and deleted['deleted'],deleted
                # Cross-device conflict: manual edit is upserted by updatedAt, not by midnight ended time.
                merge=await page.evaluate('''()=>{const key=getDateKey(new Date()),id=calendarManualId(key);const earlier={id,source:'manual',workout:'Ранее',status:'completed',dateKey:key,duration:600,started:'2026-01-01T00:00:00Z',date:'2026-01-01T00:00:00Z',updatedAt:'2026-10-01T00:00:00Z'};const later={...earlier,workout:'Позже',duration:2400,updatedAt:'2026-10-02T00:00:00Z'};const out=mergeWorkoutHistory([later],[earlier]);const trash=mergeWorkoutHistory([later],[earlier],{workouts:{[id]:'2026-10-03T00:00:00Z'}});return {count:out.length,minutes:out[0]?.duration/60,deleted:trash.length===0}}''')
                assert merge=={'count':1,'minutes':40,'deleted':True},merge
                overflow=await page.evaluate('document.documentElement.scrollWidth > innerWidth')
                assert not overflow,(width,'overflow')
                assert not errors,(width,errors)
                print(f'V44 browser {width}px: start/finish, manual add/edit/delete, history merge, layout OK')
                if width==390:
                    await page.screenshot(path='/mnt/data/svg_v46/V45_Training_390.png')
                await context.close()
        finally:
            await browser.close()

if __name__=='__main__':
    asyncio.run(run())
