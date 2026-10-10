# SVGTracker V37 — Telegram Mini App startup / SQLite race fix

Base: **V36 Home & Security**. Full replacement ZIP for the existing Telegram-admin deploy flow.
No reset or recreation of production `svgtracker.db` is required. ZIP contains **no** production database, `config.py`, `.env` or uploaded files.

## Incident reproduced

On 10 October 2026, the following requests failed during app startup:

- `GET /api/training/state` — `IntegrityError: UNIQUE constraint failed: users.telegram_id`
- `GET /api/finance/state` — same error

Root cause: `finance_db.get_or_create_user()` performed `SELECT` and then an unconditional `INSERT`, each on parallel connections. Several initial Mini App API requests raced to create the same Telegram account. Both saw a missing user, and one request failed on the unique `users.telegram_id` index.

The race was reproduced locally on the original V36 code: **12 duplicate-key errors across eight rounds of 16 concurrent calls**. The exact number is timing-dependent.

## Fixes

1. **Atomic SQLite UPSERT** on the unique Telegram ID replaces the unsafe separate `SELECT`/`INSERT` logic. Conflicting concurrent registrations resolve to the same `users.id` without producing a duplicate-key error.
2. **Fast read-only path** for existing accounts with unchanged Telegram metadata and existing settings. Routine authenticated GET requests no longer write the `users` record or compete for SQLite's write lock.
3. **Preserve existing account data**: `COALESCE` keeps existing name, username, surname, and photo when Telegram supplies nulls. `ON CONFLICT DO NOTHING` never resets existing notification settings. Creation and settings initialization stay within one transaction.
4. **Readiness check enhanced**: `GET /api/test` now checks access to `users`, `user_settings`, `training_state`, `finance_state`, `schedule_events`, `notes`, and `automation_rules`. When database schema is unavailable, it returns `503 Database is not ready` instead of returning a misleading OK. The current Telegram ZIP deploy uses this URL for its post-restart health-check and rollback decision.
5. **Version/cache update**: API and frontend assets updated to V37 (`?v=37`) to prevent use of outdated cached assets.

No database schema change, no data deletion, no changes to finance/training serialization, auth signatures or Telegram bot configuration. Existing V36 features remain intact.

## Tests performed in isolated environment

- All previous regression suites V33–V36 plus new V37 tests: **25 passing**.
- 5 rounds of 20 concurrent direct database registrations for new Telegram accounts.
- 20 concurrent real FastAPI `GET /api/training/state` / `GET /api/finance/state` calls with valid, correctly signed Telegram initData. All returned HTTP 200 and only one account/settings row was created.
- Repeated sign-in with unchanged/null/changing user metadata; user settings and profile fields preserved where expected.
- Recovery of missing settings for existing users, and verified preservation of existing finance/training records after re-login.
- Two separate Telegram IDs are always scoped to separate database IDs.
- Readiness check succeeds against an initialized database and returns 503 when an essential table is missing.
- Python compile and frontend syntax via Node, HTML handler/ID validation, ZIP integrity and deploy ZIP preflight.

## Install

1. Ideally create a database-inclusive backup using the existing admin `/backup` command, then upload this **full ZIP** using the Telegram admin deploy flow. Do not delete `svgtracker.db` or clear browser storage.
2. Deploy checks `GET /api/test`, now including database readiness. The deploy mechanism should roll back code changes if the check fails. It does **not** restore database changes for code-only deploys (none are introduced by V37).
3. Open the Mini App and verify finance and training state load, perform a normal save, reopen, and check data persist. Check `/api/test` returns `status=ok` with `version=37` locally via curl.
4. If opening still fails, send the **new** timestamps and `/logs_backend` / `/logs_frontend` output; the actual VPS and Telegram client cannot be inspected from the ZIP alone.

## Limits

Passed tests demonstrate the specific concurrency error is fixed. The live VPS, Nginx config, database state, real Telegram runtime and network connectivity were not accessible in the sandbox, so unconditional success of the real deployment cannot be guaranteed. The existing admin deploy's GitHub sync prerequisite still applies.
