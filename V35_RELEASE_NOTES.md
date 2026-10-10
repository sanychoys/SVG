# SVGTracker V35 — Analytics, Automations, SVG Assistant

Base: full V34 release. ZIP is a **full code-replacement** package for the established SVGTracker VPS admin-deploy process (root directory `TRC/`). User databases, Telegram tokens, server config and private note uploads must remain on the VPS; none are present in this archive.

## Navigation and design

- Removed **both** home-screen quick buttons: global search and reports. Dashboard returns to the activity summary and the four domain cards.
- Kept global search and reports. They are now located under **Profile → Settings → Tools** (search) and **Profile → Settings → Reports**, respectively; the Analytics Center also links to both daily/weekly reports.
- Replaced mismatched profile glyphs and navigation chevrons with uniform 24×24 line SVG icons, including finance month arrows and schedule repeat action. Refined narrow-screen row alignment and text wrapping. No profile editor is introduced.

## Analytics Center

**Profile → Settings → Analytics Center**. Secure `GET /api/analytics/activity` aggregates persisted account data over the past seven local calendar days, including recorded workouts and minutes, notes updated, income/expenses and scheduled event occurrences. Daily bars show **count of recorded actions and scheduled occurrences**, not a made-up productivity score. Future scheduled events are not described as completed tasks. Links to reports and financial analytics are provided.

## Financial analytics

**Finance → Financial analytics**, also accessible from Analytics Center. Secure `GET /api/analytics/finance?month=YYYY-MM`.

- Month picker, monthly spending, number of entries, recorded income, paid mandatory expenses, budget, plan-based remaining amount and comparison with previous month.
- Spending allocation by category and six-month expense trend.
- **Important:** unpaid mandatory payments are **not** counted as completed spending; planned monthly budget is **not** counted as actual recorded income. The calculated remaining amount is budget + recorded income − expenses − paid mandatory expenses (not bank account balance). Only persisted/synchronized state is used.

## Smart automations

**Profile → Settings → Automations**. Server-side per-account SQLite rules (table `automation_rules`) with validated settings and account scoping:

1. Budget usage reaches an adjustable percentage of monthly plan (50–100%; once per calendar month).
2. Recorded daily spending reaches an adjustable amount (from 1 ₽; once per local day).
3. Tomorrow has calendar events (evening reminder; once per local day, with no event titles disclosed).

All rules start **disabled** and require explicit enablement. Telegram delivery runs from the existing bot loop in the account's timezone between 19:00 and 22:59 local time, subject to the account's master bot notification switch. Successful sends are deduplicated via the existing `bot_reminder_log`. Running VPS bot required; if offline during that period, a missed notification is not backfilled. Rules do not call any external AI or automation service.

## SVG Assistant, without neural networks

**Profile → Settings → SVG Assistant**. Secure `POST /api/assistant` uses deterministic, read-only rules over the signed-in user's persisted reports and financial metrics. Supported examples: "Что сегодня?", "Итоги недели", "Сколько потратил?", "Что по бюджету?", "Куда уходят деньги?", "Помощь". An unknown query gets a clear list of known requests instead of speculative output. No machine-learning model, third-party AI endpoint, remote message processing or persistent conversation log.

## Files changed

- New: `intelligence.py` (analytics, rules engine and assistant), `tests/test_v35.py`.
- Updated: `main.py`, `product_api.py`, `product_db.py`, `insights.py`, `product.js`, `product.css`, `index.html`.
- Assets cache-busted to `?v=35`.
- Preserved all V33/V34 features, note attachments, account protections, reports and Telegram notifications.

## Deploy and post-deploy verification

1. Back up project code and `svgtracker.db` and the private note-attachment directory.
2. Upload this ZIP using the existing admin-deploy flow. It installs the extra `intelligence.py` and keeps the production DB and `config.py` intact.
3. Restart SVGTracker. After `init_product_db()`, `init_intelligence_db()` creates the new rules table additively. Existing data remains unchanged.
4. Reload the Telegram Mini App or website. Check: profile settings tools; 7-day analytics; finance month picker; enable and save one automation; rule-based assistant; old search, reports, notes and workouts.
5. Set/check account timezone and master bot notifications before testing Telegram automation delivery. Automatic dispatch can only be verified end-to-end in a running Telegram-connected deployment.

## Checks performed

- Python compilation and Node syntax checking, deployment frontend HTML handler/ID validation and archive validation.
- 12 isolated unit/API tests total across V33, V34 and V35 (incl. cross-user isolation, settings validation, report consistency and no-duplicate rule delivery).
- Isolated Chromium layout/screenshot checks at widths **320, 390 and 430 px** for four new screens, with **no horizontal overflow or page errors**. These used mocked display data (not a live backend).
- HTTP navigation to localhost in the browser was blocked by the execution environment, so full Telegram WebView interactions and production bot messages have **not** been verified here.
