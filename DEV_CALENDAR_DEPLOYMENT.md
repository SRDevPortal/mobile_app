# Dev calendar request failure: deployment and diagnostics

Latest follow-up: see [CALENDAR_LAZY_LOADING.md](CALENDAR_LAZY_LOADING.md) for progressive loading, change checks and the required date-index deployment step. The earlier investigation below remains historical evidence, not confirmation that dev is healthy.

## Confirmed findings

The dev page renders the new calendar but does not load its roster/appointments. Authenticated calendar reads timed out from both WSL/Python and Windows/Node, including a future date with no appointments. An unauthenticated `/api/method/ping` returned HTTP 200 in 0.27 seconds, while authenticated ping reads timed out. Earlier authenticated resource reads confirmed nine active practitioners, Mobile Appointment Workflow, and Patient Encounter-custom_appointment_status. These checks do not establish the exact server-side cause; error-log reads also timed out.

No dev records, configuration or services were changed during investigation. No dev SSH/deployment access is configured in the local workspace; the user confirmed another person manages deployment.

## Calendar fix ready for deployment

Previously, a 30-second poll invalidated an already-running same-range request. Sustained slow responses could therefore leave the calendar blank indefinitely. The calendar now keeps one read per selected range, cancels superseded reads on navigation, and aborts an unresponsive read after 45 seconds. It retains loaded bookings on failure, distinguishes loading/failure from a genuinely empty calendar, and offers retry. Mutation completion forces a fresh read. A malformed success response is treated as a failure rather than empty data.

This fixes the frontend race and makes failures visible. It does not claim to repair the separate authenticated API timeout on dev.

## For the deployment manager

1. Deploy the latest mobile_app develop commit containing the calendar timeout fix through the normal deployment process. Preserve any server-local changes before pulling. No new database migration is required by this frontend-only fix.
2. Clear the site's cache and restart/redeploy web workers using the hosting platform's normal process. For a bench-managed deployment, from its bench directory (use the actual site name if different):

```sh
bench --site dev-sr.butest.tech clear-cache
bench restart
```

3. Test the endpoint directly inside the dev bench, without sending any patient data back in the report:

```sh
bench --site dev-sr.butest.tech console
```

```python
import time
frappe.set_user("Administrator")
from mobile_app.api.appointment_calendar import get_calendar
started = time.perf_counter()
result = get_calendar("2026-09-28", "2026-10-05")
print({"seconds": round(time.perf_counter() - started, 2),
       "appointments": len(result["appointments"]),
       "doctors": len(result["doctors"])})
```

If this errors, provide the traceback. If this completes but the authenticated HTTP request hangs, inspect authentication hooks, web-worker state, database/Redis waits and reverse-proxy logs for the same request. Provide the HTTP status and corresponding log exception, without credentials or patient records. Do not rerun migrations or change appointment data blindly.

## Local verification

Chromium tests passed: delayed response beyond the poll interval, 45-second timeout, preserving records on HTTP 500/invalid responses, first-load failure message, retry recovery, forced fresh reads, custom ranges, filters/counts, reload, encounter Back and mobile layout. No appointments were changed. Test artifacts: /home/jagmohan/.codex-work/appointment-calendar/browser-slow-calendar.cjs and browser-date-range.cjs.
