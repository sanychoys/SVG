# SVGTracker V34 · Search, Reports, Notes 2.0

Base release: V33 Account Center. This is a full replacement archive for the existing SVGTracker admin-deploy workflow. No production database, note uploads, credentials or server configuration are included or overwritten.

## New features

- **Unified search** (`GET /api/search?q=`): account-scoped, case-insensitive search in notes (including tags, folders and attachment filenames), visible schedule events, saved finance entries and recorded workouts/goals. Results are generated server-side; only the authenticated owner's data is used. Notes and events open in their editor; finance and training entries open their corresponding sections.
- **Daily report** (`GET /api/reports?period=daily`): live in-app preview of the current local day, including workouts, minutes, calendar events, changed notes, recorded income and expenses.
- **Weekly report** (`GET /api/reports?period=weekly`): current local Monday–Sunday calendar week summary. The Telegram bot can send both digest types automatically at or after **20:00 account-local time** (daily every evening; weekly on Sundays). The bot must be online for delivery; events after the send time are not retrospectively included. A delivery log prevents repeat sends when the bot restarts or retries. Both report categories are **disabled by default** and require a user opt-in under Profile → Notifications. Disabling the master Telegram notification switch also disables both.
- **Notes 2.0**: personal folders (free-text with suggestions), up to 12 tags, favorites, an archive toggle, category and folder filters, extra search by tags and archived-note synchronization, plus 4 optional editor templates: checklist, journal, project, meeting. Old note records and existing attachments remain intact.
- **Notification controls** now include separate daily and weekly report toggles, alongside existing reminders for notes, schedules, debts, payments, friendship and shared calendar changes.

## New/changed files

- Added `insights.py` for the account-scoped search index, report generation, and schedule of opt-in Telegram digests.
- Extended `product_db.py` with additive SQLite migration of `notes`: `folder`, `tags_json`, `favorite`, and preferences `daily_report`, `weekly_report`.
- Extended `product_api.py` with search/reports routes and validated note metadata.
- Extended `main.py` bot reminder loop to send reports using its existing SQLite reminder log.
- Updated `product.js`, `product.css`, `index.html` for app screens and notes controls. Frontend asset query version changed to `v=34`.
- Added `tests/test_v34.py` including HTTP endpoints, isolation, data persistence, and no-duplicate report checks.

## Deploy / compatibility

1. Deploy this ZIP through the project's admin-deploy (or replace source files while service stopped) with standard pre-deploy backup enabled.
2. Restart the SVGTracker service. `init_product_db()` applies additive database migrations on service start; existing records are not recreated. **Do not delete** `svgtracker.db` or the hidden private note attachment directory.
3. Reload Telegram Mini App/site to refresh cached JS/CSS. Open Profile → Notifications, enable desired report categories and confirm the main bot notification switch is active.
4. Set the user's timezone by opening an authenticated client (existing timezone synchronization in V33). Report send time is based on `user_settings.timezone_name`/offset.
5. Verify once in the real Mini App: note edit/archive/unarchive, search, preview reports, and one scheduled Telegram message. Production delivery requires a running bot and a Telegram chat that has not blocked the bot.

## Verified and limitations

- Python compilation, Node JS syntax check, SVGTracker HTML handler/id validator, admin-deploy release validation, and automated unit/API tests. No tests used or modified a real user database.
- Browser screenshot automation was not available in the execution environment due to its browser navigation policy. Actual WebView behavior and Telegram sends on VPS still need a post-deploy smoke test.
- Search opens the finance/training section, not an individual transaction or workout form. Reports summarize *recorded* data rather than inferring any unrecorded actions, and the weekly preview includes events scheduled later in the week.
