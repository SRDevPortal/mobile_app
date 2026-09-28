# Dev Error Log review - 2026-09-28

Reviewed the latest 15 entries and today's non-reconciliation entries through authenticated, read-only Error Log API requests. This is a review of recorded failures, not confirmation that all services are healthy. No dev logs or records were deleted or changed.

## Mobile login sync: fixed locally, deployment required

Two failures at 16:30:21 report MariaDB error 1292 when users_sync saves ISO UTC strings such as `2026-09-28T10:53:37.040Z` directly to Mobile App User.last_login_at. The document controller now parses login timestamps before database persistence, converts timezone-aware values to the site's timezone, keeps local naive values unchanged, accepts blank values as null, and rejects invalid timestamps with a validation error.

33 local tests passed, including real insert/update, UTC and explicit offsets, repeated saves, blanks, invalid values, users_sync and users_full_sync, plus the existing calendar/profile/support regressions. Fixtures rolled back. No existing login timestamps were backfilled or altered.

## OCR timeout: separate service failure

At 15:02:50, wa_chat_hub.ai.ocr_summary._extract_with_openai_vision hit a 30-second read timeout contacting api.openai.com. This log identifies an upstream/network timeout; it does not establish a calendar error or prove whether a later OCR attempt succeeded. The deployment owner should check worker/network health and the affected OCR job before retrying it.

## Vobiz reconciliation: still pending in the recorded state

The recent repeated alerts report purpose `cdr`, attempts `8`, and last_error `null`. The recovery policy emits this alert when call-detail reconciliation remains unresolved after retries, and continues checking at a lower rate. The log does not contain a provider exception explaining why CDR data remains unavailable. Provider CDR availability and the relevant recovery jobs need investigation; do not mark call/billing state complete without evidence.

## Raven push notifications: missing configuration

At 00:00:38 the invalid-token synchronization task reported a missing encrypted `push_notification_api_secret` in Raven Settings. The deployment owner needs to restore/configure the appropriate Raven notification secret, or disable that integration through supported settings if unused. No secret was guessed or changed.

## Appointment calendar: not yet confirmed resolved on dev

The same-day Error Log search contained no appointment_calendar traceback. Nevertheless, the authenticated calendar request timed out after 18 seconds during this review. Error Log reads now succeed, so earlier failures are intermittent or endpoint-dependent; they do not prove every authenticated endpoint is continuously unavailable. Dev server-side diagnostics are still required, as described in DEV_CALENDAR_DEPLOYMENT.md. The separate local refresh/timeout fix is ready but has not been deployed by this agent.
