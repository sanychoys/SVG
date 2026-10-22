"""V45 UX paths with mobile browser interaction; backend calls mocked (not iPhone WebView)."""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright
from smoke_browser_v43 import STORAGE_STUB, bundled_html

async def run():
    html = bundled_html()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
        try:
            for width in (320, 375, 390, 430):
                context = await browser.new_context(viewport={'width': width, 'height': 844})
                await context.add_init_script(STORAGE_STUB)
                page = await context.new_page()
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))
                await page.goto('about:blank')
                await page.set_content(html, wait_until='domcontentloaded', timeout=30000)
                await page.wait_for_timeout(350)
                await page.evaluate('openTraining()')
                buttons = page.locator('.attendance-grid button.calendar-day')
                assert await buttons.count() >= 28, width
                targets = await page.evaluate('''()=>[...document.querySelectorAll('.attendance-grid button.calendar-day')].map(b=>({label:b.textContent,rect:b.getBoundingClientRect().toJSON()}))''')
                assert all(t['label'].isdigit() and t['rect']['width'] >= 32 and t['rect']['height'] >= 32 for t in targets), (width, targets)
                await page.locator('.calendar-add-today').click()
                assert not await page.locator('#calendar-workout-sheet').evaluate('(s)=>s.hidden')
                assert await page.locator('#calendar-workout-save').is_disabled()
                await page.locator('#calendar-workout-minutes-total').fill('0')
                assert await page.locator('#calendar-workout-save').is_disabled()
                await page.locator('#calendar-workout-minutes-total').fill('99')
                assert await page.locator('#calendar-workout-save').is_enabled()
                await page.locator('#calendar-workout-minutes-total').fill('1500')
                assert await page.locator('#calendar-workout-save').is_disabled()
                await page.locator('[data-duration="45"]').click()
                assert await page.locator('#calendar-workout-minutes-total').input_value() == '45'
                await page.locator('#calendar-workout-name').fill('Йога')
                await page.locator('#calendar-workout-save').click()
                stored = await page.evaluate('''()=>({record:getCalendarManualRecord(getDateKey(new Date())),day:getTrainingSecondsForDate(new Date()),week:document.querySelector('.activity-total').textContent})''')
                assert stored['record'] and stored['record']['duration'] == 2700 and stored['record']['workout']=='Йога', (width,stored)
                assert stored['day']>=2700
                await page.locator('.attendance-grid .calendar-day.today').click()
                assert await page.locator('.calendar-day-total').inner_text() != '0 мин'
                assert 'Изменить' in await page.locator('.calendar-day-edit-title').inner_text()
                assert await page.locator('#calendar-workout-save').is_disabled()
                await page.locator('[data-duration="60"]').click()
                await page.locator('#calendar-workout-save').click()
                assert await page.evaluate('getCalendarManualRecord(getDateKey(new Date())).duration') == 3600
                # Unsaved modifications must prompt confirmation on closing.
                await page.locator('.calendar-add-today').click()
                await page.locator('[data-duration="90"]').click()
                await page.evaluate('''()=>{window.confirm=()=>false;requestCloseCalendarWorkout();}''')
                assert not await page.locator('#calendar-workout-sheet').evaluate('(s)=>s.hidden')
                await page.evaluate('''()=>{window.confirm=()=>true;requestCloseCalendarWorkout();}''')
                assert await page.locator('#calendar-workout-sheet').evaluate('(s)=>s.hidden')
                await page.evaluate('editPlan()')
                assert not await page.locator('#plan-sheet').evaluate('(s)=>s.hidden')
                assert not await page.locator('#plan-exceptions-details').evaluate('(d)=>d.open')
                await page.locator('.day-picker[data-day="Пн"] input').fill('Зал')
                await page.locator('.plan-editor-actions button').filter(has_text='Сохранить').click()
                assert await page.evaluate('state.plan["Пн"]') == 'Зал'
                # Calendar navigation, including a return shortcut.
                await page.evaluate('shiftCalendarMonth(-1)')
                assert not await page.locator('.calendar-today-jump').evaluate('(e)=>e.hidden')
                await page.locator('.calendar-today-jump').click()
                assert await page.locator('.calendar-today-jump').evaluate('(e)=>e.hidden')
                # Schedule selected day persists when switching weeks.
                await page.evaluate('openSchedule()')
                result = await page.evaluate('''()=>{
                    const d=new Date();d.setDate(d.getDate()+2);selectScheduleDate(getDateKey(d));
                    const before=getDateKey(scheduleSelectedDate);shiftScheduleWeek(1);
                    const after=getDateKey(scheduleSelectedDate);
                    openScheduleEditor();
                    const editorDate=document.getElementById('schedule-event-date').value;
                    return {before,after,editorDate};
                }''')
                a = __import__('datetime').date.fromisoformat(result['before'])
                b = __import__('datetime').date.fromisoformat(result['after'])
                assert (b-a).days==7 and result['after']==result['editorDate'], (width,result)
                # Layout checks all screens.
                overflow = await page.evaluate('document.documentElement.scrollWidth > innerWidth')
                assert not overflow,(width,'horizontal overflow')
                assert not errors,(width,errors)
                print(f'V45 browser {width}px: calendar UX, duration presets, CRUD, unsaved protection, plan & schedule navigation OK')
                if width == 390:
                    await page.evaluate('closeScheduleEditor();closeSchedule();openTraining();openCalendarWorkoutEditor(new Date())')
                    await page.screenshot(path='/mnt/data/svg_v46/V45_Editor_390.png')
                await context.close()
        finally:
            await browser.close()

if __name__=='__main__':
    asyncio.run(run())
