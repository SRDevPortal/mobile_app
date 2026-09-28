# Progressive calendar loading

For the confirmed dev query backlog, database deadlines and recovery commands, see [DEV_CALENDAR_RECOVERY.md](DEV_CALENDAR_RECOVERY.md).

The old eight-second warning could appear during a full-range background reload even when appointments were already visible. This change removes that slow-load banner and reduces repeated work; actual request failures remain visible and retryable.

## Behavior

- Load only the chosen date range, in sections of up to seven days. Fetch the section containing the selected date first and display each response immediately.
- Keep existing appointments visible while loading. Merge by booking ID and reconcile calendar events in place. A successful response removes records that moved out of a section or are no longer authorized; a failed response never pretends to be empty data.
- Check a compact, authenticated change manifest every 15 seconds while the calendar is visible. Fetch only changed or missing sections. A new booking normally appears at the next check plus request time; this is polling, not instant WebSocket delivery.
- Change tokens cover new/changed/deleted/moved bookings, workflow state, linked patient/doctor modifications, roster and caller roles. Appointment payloads still pass the existing permission checks.
- Keep one request chain active. Navigation and completed mutations supersede obsolete reads; stale responses are ignored. Manual Refresh reloads the displayed sections.
- Do not scan 62 days outside the selected range or automatically move the calendar to a different period.
- Use indexed appointment-date predicates, including encounter-date fallback. Batch doctor lookups instead of reading each practitioner separately.

## Deployment

Deploy the backend and calendar JavaScript together from mobile_app develop. In the dev bench, using its actual site name:

```bash
bench --site <dev-site-name> execute mobile_app.calendar_setup.setup_calendar_indexes
bench --site <dev-site-name> clear-cache
```

Restart web processes using the deployment's normal restart procedure, then reload the browser. The idempotent index setup also runs during normal migration. Indexes are additive; no appointment or patient records are changed. Creating indexes on a large table may take time, so the deployment owner should use the normal deployment window.

This does not establish that the separate dev server/API timeout has been resolved. Verify real requests and server logs after deployment; a server/authentication/worker hang cannot be fixed by browser lazy loading alone.

## Verification

- 36 local backend tests passed, including section-token consistency, insert/move/delete detection, workflow updates, linked patient changes, role changes, receptionist scope and guest rejection. Test database writes rolled back.
- Local Chromium checked progressive rendering while another section was delayed, unchanged checks with no appointment payload calls, automatic new bookings, status updates, timeout/HTTP 500/invalid response preservation, retry, removal reconciliation, week navigation and hard reload. Slow/update/error scenarios used controlled network responses; no dev records were changed.
- Local EXPLAIN uses the appointment_calendar_dates index with range access. This is not a production benchmark.
- Browser artifact: /home/jagmohan/.codex-work/appointment-calendar/calendar-lazy-loading.png. Browser regression script is stored beside it as browser-lazy-calendar.cjs.
