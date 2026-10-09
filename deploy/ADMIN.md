# SVGTracker Admin

Admin Telegram ID is read from `SVGTRACKER_ADMIN_ID` and defaults to the project owner ID configured in `main.py`.

## Bot commands

- `/admin` — admin panel
- `/deploy` — instructions; the admin can also send a `.zip` directly
- `/deploy_status` — last deployment result
- `/server_status` — service/API/watchdog/Git/disk/RAM status
- `/backup` — code backup + consistent SQLite snapshot
- `/backups` — recent backups
- `/rollback` — restore code from the latest backup (database is intentionally not rolled back)
- `/errors` — recent errors/warnings from journald
- `/logs` — общие service logs
- `/logs_frontend` — ошибки/этапы загрузки Safari и Telegram WebView
- `/logs_backend` — FastAPI/Uvicorn/API
- `/logs_bot` — aiogram/Telegram polling
- `/logs_system` — systemd/watchdog
- `/logs_deploy` — ZIP deploy + GitHub publish
- `/diagnostics` — сводный снимок сервера + последние frontend events
- `/restart` — safe systemd restart
- `/github_setup` — generate the repository-scoped GitHub Deploy Key and show its public half
- `/github_test` — verify GitHub write access and switch `origin` to SSH
- `/github_status` — compare production with `origin/main`
- `/github_sync` — one-time/bootstrap commit + push of the current production tree

## One-time GitHub setup from a phone

1. Run `/github_setup` in Telegram.
2. Copy the public key returned by the bot.
3. Open the repository in GitHub → Settings → Deploy keys → Add deploy key.
4. Paste the public key and enable **Allow write access**.
5. Run `/github_test`.
6. Run `/github_sync` once if this release was installed by the older direct ZIP deployer.

The private key is generated on the VPS at `/root/.ssh/svgtracker_github` and is never sent to Telegram or stored in the repository.

## Transactional ZIP deploy

V21+ treats GitHub as the source of truth. Before a ZIP is applied, the worker verifies that the VPS working tree is clean and that `HEAD` exactly matches `origin/<branch>`.

The deployment transaction is:

1. Validate ZIP and Git/GitHub state.
2. Create a production backup.
3. Apply only permitted project files.
4. Restart SVGTracker and wait for the API health check.
5. Commit the applied project files.
6. Push the commit to GitHub.
7. Report success to Telegram.

If the API health check fails, production is restored from backup. If GitHub commit/push fails, production is also restored and Git is reset to the pre-deploy commit, so GitHub and the VPS do not silently diverge.

Protected production data is never overwritten by ZIP deploy: `config.py`, `.env`, `svgtracker.db`, SQLite WAL/SHM, `.git`, `venv`, uploads and logs.

## V22 frontend resilience
- Telegram WebApp SDK is loaded asynchronously and can no longer block the whole UI.
- `script.js` and `style.css` are cache-busted with `?v=22`.
- ZIP deploy validation rejects missing frontend assets and a blocking Telegram SDK tag.
- `/server_status` reports a separate Frontend status.

## V23 diagnostics
- Browser bootstrap starts logging before `script.js` is loaded.
- Captures resource failures, JS errors, unhandled promises, API/network errors and startup stages.
- Frontend telemetry is stored in `/var/lib/svgtracker-admin/logs/frontend.log`; credentials and Telegram init data are not logged.
- Deploy lifecycle is stored in `/var/lib/svgtracker-admin/logs/deploy.log`.
- `/server_status` shows whether frontend telemetry is arriving and the last startup stage.
- The diagnostic endpoint is rate-limited and intentionally works without Telegram auth so it can report failures of the Telegram SDK itself.

## Local time / timezone

V24 stores the user's device timezone when the Telegram Mini App opens. Admin log commands (`/logs*`, `/errors`, `/diagnostics`, backup timestamps and deploy notifications) render timestamps in the admin's saved device timezone while the database continues to store UTC internally. `/timezone` shows the active timezone and can override it manually, e.g. `/timezone +04:00` or `/timezone Europe/Moscow`.


## Memory / RAM

- `/memory` — current SVGTracker RSS/PSS/peak, VPS memory and top processes.
- `/memory_gc` — prune expired runtime caches, run Python GC and request glibc `malloc_trim`.
- `svgtracker.service` uses `MALLOC_ARENA_MAX=2`, `MemoryHigh=450M`, `MemoryMax=700M`, and `TasksMax=64` to avoid runaway memory on a small VPS.
- Runtime maintenance prunes expired deploy metadata, error cooldowns and frontend rate-limit buckets every 10 minutes.

## V26 website authentication

- Telegram Mini App auth is unchanged and continues to use signed WebApp `initData`.
- Normal browser visits use a separate Telegram-confirmed web session.
- The website creates a one-time `webauth_...` deep link, the user confirms it in `@SVGTrackerbot`, and the backend issues an HttpOnly + Secure + SameSite=Lax session cookie.
- Web login requests expire after 10 minutes; website sessions expire after 30 days and can be ended from the profile drawer.
- `web_auth_requests` and `web_sessions` are created automatically in SQLite.
- `/resetdata` and `/resetdb` are admin-only; they are no longer published in the common user command menu.

## V28 product modules

Расписание и заметки вынесены из монолитного frontend/backend слоя:

- `product.js` / `product.css` — UI расписания и заметок;
- `product_api.py` — HTTP API этих модулей;
- `product_db.py` — нормализованные SQLite-таблицы, совместные события и reminder log.

Напоминания расписания, заметок, обязательных платежей и долгов используют общий
переключатель «Уведомления от бота» в профиле. Запросы в друзья по-прежнему
управляются отдельным переключателем.

## V28 compatibility fix: legacy ZIP validation

When deploying V28 over V27, the *old* `admin_deploy.py` validates inline
HTML event handlers using declarations in `script.js` alone. V28 moved sixteen
handlers into `product.js`, causing a false `Missing JS handlers referenced by
HTML` rejection **before** the new deploy helper can be installed.

This release maintains the complete implementations in `product.js`, captures
them in `window.SVGTrackerProductHandlers`, and adds forwarding declarations
in `script.js` for the old validator. `product.js` must be included **before**
`script.js` in `index.html` (`defer` preserves document order). The new
validation helper continues to check both files. No runtime data, credentials,
or SQLite databases are included in the update ZIP.

## V29: расписание и уведомления

- В календаре появились три режима: **Моё** (события только пользователя), **Общее** (события с участниками) и **Свободное время** (получение только интервалов занятости участников).
- Информация о занятости отдаётся только подтверждённым друзьям, если владелец включил опцию «Делиться занятостью» в профиле. По умолчанию она **выключена**. API не отдаёт названия, описание, ID событий или заметки других людей. Запрос требует авторизации.
- В профиле в пункте «Настроить уведомления» можно независимо включать и выключать напоминания личного и общего расписаний, заметок, платежей и долгов, уведомления об изменениях совместных событий и о принятии заявки в друзья. Запросы в друзья управляются отдельным переключателем в профиле.
- Общий переключатель уведомлений бота остаётся главным: когда он выключен, Telegram-уведомления не отправляются, независимо от включённых категорий. Разрешение на просмотр занятости действует независимо от бота.
- `init_product_db()` автоматически создаёт `notification_preferences` при запуске. Предыдущие SQLite-данные сохраняются, новые предпочтения дополняются значениями по умолчанию.
- Контроль: тесты `/api/notifications/preferences`, `/api/schedule/availability`, проверки прав доступа, дифференцированных Telegram-уведомлений и проверки JavaScript-сборки.

## V30 · Settings, weekly templates and private note attachments

- Profile → **Настройки** → **Уведомления / Настроить** opens all master,
  friend and per-category notification/privacy switches. The existing API is unchanged.
- Calendar → **Расписание на каждую неделю** lets users choose multiple
  weekdays for an event. Recurrence is stored as weekday offsets relative to
  the selected start day, plus the event owner's IANA timezone so the local
  time stays stable across daylight saving time changes. The existing V29
  schedule database is migrated automatically (no data loss).
- Notes accept private file attachments (images, audio, video, text, PDF,
  and other files). The authenticated binary upload uses 512 KiB chunks to
  stay under the common default Nginx 1 MiB request-body limit.
- Limits: 50 MiB per file, 25 files per note, 500 MiB completed files per
  account, and at most three concurrent incomplete upload sessions.
- Attachments live outside the public web root by default:
  `PROJECT_ROOT.parent / '.svgtracker-private-note-files'`. Set
  `SVGTRACKER_ATTACHMENT_DIR` in the systemd environment **before the first
  upload** to use another persistent directory. Ensure the service account
  can create/write to this folder; the app restricts it to mode 0700.
- V30 manual `/backup` snapshots include both the SQLite DB and its private
  note attachment files. Rollback of code **does not** restore the database
  or delete private media. Back up the private media directory before manual
  server migrations, and plan disk space for media backups.
- Note attachments require authentication for download and are served only
  through FastAPI, never through a public static URL.
