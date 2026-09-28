# Dev calendar database recovery

## Confirmed on 2026-09-28

The authenticated System Console process-list endpoint exposed long-running calendar SELECT queries, including one running for over 1,000 seconds in `Creating sort index`. They continued after browser/HTTP request deadlines. New queries were accumulating about once a minute. These were the old `COALESCE` date-filter/sort queries, not the indexed predicates in the pending local update.

Patient Encounter has approximately 446,538 rows (database estimate). Its live index list did not contain `appointment_calendar_dates`. Authentication, practitioner reads and Mobile App Appointment reads responded promptly. A Patient Encounter list limited to one record, its metadata load, and calendar reads timed out. Other long CRM reads also consume shared database resources; this recovery deliberately does not cancel them.

The new `get_calendar_changes` method was still absent from the running dev backend. These findings establish that the old unbounded calendar reads are accumulating; they do not establish that every source of server/database pressure is resolved.

Read-only diagnostics used the standard authenticated System Console. That API creates Console Log audit entries. No patient/appointment records, dev indexes or running queries were changed by these checks.

## Code changes

The progressive-loading/index change must be deployed along with this follow-up:

- Heavy MariaDB calendar reads use an eight-second statement-local deadline, independent of the browser or HTTP connection. It does not change global/session database limits or affect writes.
- Timeout code 1969 is normalized to Frappe QueryTimeoutError, including versions that do not classify it themselves.
- Failed automatic refreshes back off (30, 60, 120, 240, then 300 seconds). Manual Refresh remains available; a successful refresh resets backoff. Normal change checks remain every 15 seconds.
- Index setup precedes encounter-status synchronization during migration.
- A bench-only recovery helper previews stale old calendar queries, or cancels them on explicit `cancel=1`. It matches the full old SELECT shape, this site's database, and age >=120 seconds. It rechecks each query immediately before `KILL QUERY`. It does not kill connections, patient writes, other query shapes or other sites. No HTTP endpoint is exposed.

## Deployment manager commands

Use the normal deployment window and close calendar tabs to stop old clients from issuing reads. Use the actual bench path and site name. Preserve any deployment-side edits and deploy mobile_app develop containing both the progressive-loading commit and this follow-up; do not force-reset local work.

From the dev bench:

```bash
# Preview first; output contains only IDs, ages and states.
bench --site <dev-site-name> execute mobile_app.calendar_maintenance.recover_stale_reads

# Stop only the matching stale calendar SELECTs.
bench --site <dev-site-name> execute mobile_app.calendar_maintenance.recover_stale_reads --kwargs '{"cancel": 1}'

# Add the missing date indexes, then refresh running code and page cache.
bench --site <dev-site-name> execute mobile_app.calendar_setup.setup_calendar_indexes
bench --site <dev-site-name> clear-cache
bench restart

# Verify timings, index columns, doctor/appointment counts and stale-read count.
bench --site <dev-site-name> execute mobile_app.calendar_maintenance.verify
```

If the environment uses containers or a process manager rather than `bench restart`, use its normal web-process restart command. If the preview finds no queries but the server is still overloaded, inspect current database processes rather than killing unrelated queries. `KILL QUERY` may need the deployment's database privileges. Index creation may take time on a large table; let it finish and capture any error.

The verify output must include `query_timeout_seconds: 8`, index columns `sr_encounter_type`, `pe_appointment_date`, `encounter_date`, and ideally `stale_calendar_reads: 0`. It reports timings and counts without patient data. It uses today's next seven days by default. To verify another period:

```bash
bench --site <dev-site-name> execute mobile_app.calendar_maintenance.verify --kwargs '{"start": "2026-09-07", "end": "2026-09-14"}'
```

Return both verification outputs for remote HTTP rechecking. Reload the browser after deployment. Confirm initial loading, background changes and a saved appointment's status in the actual dev calendar.

## Local validation

39 backend tests passed. A real MariaDB `SELECT SLEEP(2)` was interrupted with a 0.05-second test deadline, and the connection's session timeout remained unchanged. Recovery tests reject unrelated reads, writes, locking queries and extra appended statements, and cover preview/recheck behavior. Normal appointment workflow and role tests still pass.

Chromium verified progressive loading, unchanged checks, automatic booking/status changes, cross-section rescheduling without duplicates, SQL-timeout messaging, retry backoff, manual recovery, HTTP failures, obsolete navigation responses and hard reload. These error/update cases used controlled network responses. No dev recovery or deployment has been performed by the local agent.
