# Appointment Calendar design QA

final result: passed

## Target and evidence

Reference: the Practo Ray calendar screenshot supplied in this conversation (1366 x 675, including desktop taskbar). This implementation adapts its left doctor filters, central calendar, right queue and indigo navigation to Frappe. It retains Frappe navigation and adds the requested approval/detail workflow. The source and implementation contain different patients and dates; data density is not a fidelity defect.

Rendered desktop evidence (1366 x 768):
- /home/jagmohan/.codex-work/appointment-calendar/calendar-week.png
- /home/jagmohan/.codex-work/appointment-calendar/calendar-pending-detail.png
- /home/jagmohan/.codex-work/appointment-calendar/calendar-completed.png

Responsive evidence (390 x 844 viewport, full-page capture):
- /home/jagmohan/.codex-work/appointment-calendar/calendar-mobile.png

Live-data verification:
- /home/jagmohan/.codex-work/appointment-calendar/calendar-live.png

The named QA patients in the first screenshots were temporary fixtures. They and their audit records were removed after testing. The live screenshot uses the existing May bookings. Screenshots are local artifacts and are not published.

## Findings and fixes

1. Missing calendar/file symbols in initial render: replaced unavailable symbol names with Frappe's bundled calendar/file icons. Inspected the revised screenshot.
2. Calendar initially opened at midnight: restored working-hours scroll position after first data load and view changes. Revised screenshot starts at 08:00.
3. Queue sorting placed 09:00 after afternoon appointments: normalized server times to HH:mm:ss. Retest shows chronological order.
4. Mobile header overflow: wrapped controls and used Day view on narrow screens. After the fix the horizontal-overflow browser assertion passed and controls fit the viewport.
5. Sticky rendered events were duplicated after filters/view changes: use FullCalendar eventRender filtering without a second event source. Added exact event-count assertions after switching to Day and resizing. Retest passed, and the final mobile screenshot has one block per booking.

No remaining P0/P1/P2 visual findings in the requested desktop calendar and action flow. Colours, layout columns, queue counters and doctor filters reflect the reference. Frappe branding, a seven-day calendar, the approval legend and additional detail panel are intentional adaptations.

## Interaction and permission verification

- Chromium exercised approve, check in, start consultation, check out and rejection with a required reason.
- Doctor, online/offline and text filters; Day/Week/Month; date navigation; and narrow-screen layout passed.
- No JavaScript page errors in the workflow run or the subsequent live-data verification.
- Nine backend integration tests passed: lifecycle/audit, rejection, stale/out-of-order requests, agent ownership, assigned doctor actions, manager assignment, guest/invalid-source rejection, source cancellation and encounter/mobile deduplication.
- All nine original mobile appointments remain. No QA appointments or workflow rows remain.
- Opening a real booking form and returning restores the calendar without leaking its layout onto other pages.

## Scope limits

Normal-save Patient Encounter testing subsequently passed with a temporary patient/customer, practitioner, real agent/doctor accounts, and linked Clinic Appointments. Chromium evidence: encounter-pending.png, encounter-completed.png and encounter-rejected.png in the same artifact directory. Approval, check-in, consultation, checkout, rejection, refresh persistence and other-agent access denial passed without JavaScript errors. Ten backend regression tests passed. All temporary records and sessions were removed. Existing mobile bookings are dated May 14-25, 2026. A real Android-to-ERP request and actual Google Meet generation were not tested; the online ERP flow used a mocked external Google Calendar boundary. Patient-facing Android status updates/notifications are outside this ERP calendar change.

## Doctor roster / refresh follow-up

Final screenshot: /home/jagmohan/.codex-work/appointment-calendar/doctors-retained-demo-final.png. Active doctors remain visible with zero counts; check-in decrements the awaiting-check-in count. Repeated refresh, filters, day/week/month, out-of-order requests, simulated load failure/recovery, scroll persistence and mobile overflow assertions passed. Eleven backend tests passed. Six labelled demo encounters remain for manual testing as requested.

## Encounter navigation regression

Reproduced Open Patient Encounter -> browser Back resetting the time-grid scroll from 1120px to midnight while all six events remained loaded. Bound cleanup to Frappe's actual wrapper hide event, retained a scroll bookmark outside the hidden DOM, preserved same-view state, and invalidated hidden-page requests. Chromium verified the exact return, three repeated round trips, active doctor/search filters, and a delayed refresh completing while the form was open. Event count, week, detail selection and scroll position were retained with no page errors. Evidence: return-before.png / return-after.png and browser-return.cjs in the existing artifact directory. Demo appointments were not changed.

## Roles and search verification

Real Appointment Manager and Appointment Receptionist logins opened the calendar without System Manager. Verified manager actions/eligible staff selection, receptionist status restrictions, forbidden direct detail requests, and no reception controls for approval/assignment/consultation. Mobile, formatted mobile, +91 prefix, doctor, patient, booking ID and patient ID search passed, including refresh. An exact mobile query could not expose a pending booking to reception. No browser JavaScript errors; 15 backend tests passed. Temporary users and sessions removed; retained appointments unchanged.

## Past-week selection regression

Reproduced selecting September 26 in Month and switching to Week incorrectly showing September 28?October 4 with zero events. View changes now pass the selected date explicitly, including the mobile switch to Day. Added a toolbar date picker for past/future navigation and named previous/next period controls. Browser checks verified September 21?27 retains all six demo appointments; day/week/month keep September 26 selected; Today/current week, arbitrary past dates, previous/next, refresh, empty day selection, encounter/Back with preserved scroll, and mobile overflow all passed. No appointment data was changed. Evidence: browser-week-anchor.cjs and past-week-date-navigation.png.

## Hard-refresh initialization regression

The prior fix only preserved the in-memory date during view changes; the constructor still reset every full load to today. Navigation is now validated/restored from browser storage keyed by current user (date and view only). On a first visit with no valid saved preference and an empty current period, a permission-scoped lookup within 31 days either side of today selects the nearest available appointment date. Explicit date/Today/navigation choices take precedence and remain saved even when empty.

Chromium CDP Network.setCacheDisabled + Page.reload(ignoreCache:true) verified three repeated hard reloads of September 21?27 retain six demo bookings; Day/Month/Week each survive reload; malformed preferences recover; explicit Today stays today. A fresh receptionist login in the same browser returned only its two permitted Checked In bookings and did not inherit Administrator's saved date/view. Re-ran past date selection, previous/next weeks, encounter/Back scroll and mobile checks. No page errors; no appointment changes. Evidence: browser-hard-refresh.cjs and hard-refresh-past-week.png.

## Counts match displayed appointments

The old doctor badge counted Pending/Approved only, so a matching checked-in/completed appointment appeared beside a zero. Main badges now count exactly the filtered calendar appointments; separate awaiting badges retain the pre-check-in count. Date-range checks are shared with event rendering, and counts refresh on view/date changes. Chromium verified mobile-number search returns one displayed/zero awaiting for the now-completed demo, filter/refresh consistency, doctor/channel/status filters, empty and populated dates, previous/next weeks, month and mobile layout. Existing appointment states were not changed. Evidence: browser-filtered-counts.cjs and filtered-doctor-counts.png.

## Simplified appointment completion (2026-09-28)

This supersedes the earlier consultation/checkout workflow checks above. Appointment status now uses Pending, Approved, Cancelled and Checked In, with check-in terminal. Removed elapsed waiting/consultation metrics, consultation/checkout timestamps and actions, and obsolete consultation transitions from the displayed history; stored audit data remains intact.

Chromium verified the visible read-only Appointment Status field on HLC-ENC-2026-00006, terminal Checked In details, pending approval/cancellation controls and required-reason modal, doctor totals, encounter Back and reload without JavaScript errors. Five appointments were present during the browser run, including one awaiting check-in; no appointment was changed or deleted by the browser test. Evidence: browser-check-in-final.cjs, check-in-final-calendar.png and encounter-appointment-status.png in /home/jagmohan/.codex-work/appointment-calendar.

Normal-save transactional checks verified Pending creation, approval, check-in and cancellation, persistence across subsequent encounter saves, rejection of forged field values, and linked Clinic Appointment completion/cancellation. Fixture records rolled back. Existing legacy workflow states were normalized and Appointment Status backfilled after a database backup.

## Calendar time-scale zoom (2026-09-28)

Added compact minus/percentage/plus controls above the grid and Ctrl + wheel zoom (50-200%) for Day/Week. Plain wheel scrolling remains unchanged. Resolved the global Frappe `!important` slot-height override and jQuery's fractional row-height rounding so appointment blocks stay aligned and the visible centre time stays fixed when zooming away from day boundaries. Month disables controls; zoom preferences survive reload and form navigation.

Chromium passed zoom bounds/reset, centre-time preservation, every rendered event's time alignment, ordinary scrolling, Ctrl-wheel, refresh and reload, Month/Week switches, encounter Back, and 390px mobile overflow/control checks. No page errors or appointment mutations. Evidence: browser-calendar-zoom.cjs, calendar-zoom-50.png, calendar-zoom-200.png and calendar-zoom-mobile.png in the existing local artifact directory. Desktop and mobile screenshots inspected.

## Appointment status marks (2026-09-28)

Added consistent clock/check/double-check/cross status marks to calendar blocks, legend, schedule and details while retaining doctor colors. Chromium verified live Month blocks and overflow popovers, refresh without duplicate marks, Week at 50%, detail badges, accessible status labels, and mobile overflow. All four states were also rendered using browser-memory fixtures without database changes. No page errors or appointment mutations. Evidence: browser-status-marks.cjs and calendar-status-month.png in the existing artifact directory; screenshot inspected.

## Custom date range and simplified panel (2026-09-28)

Removed the duplicate Daily schedule date header and channel tabs. Added Range mode with inclusive From/To inputs, validation for 1-62 days, exact-range appointment queries/counts and a week-row calendar. The appointment list now follows the displayed period and includes dates. Dates outside the range are shaded. Mobile range grids scroll horizontally instead of compressing names into unreadable columns.

Chromium verified one-day, cross-month and maximum 62-day ranges, invalid/reversed/overlong selections, previous/next, matching counts and list, search/refresh, reload persistence, encounter Back, mobile overflow and returning to Day/Week/Month. Fixed FullCalendar's same-view range redraw by updating its visibleRange option. No JavaScript errors or appointment mutations. Browser navigation tests wait for the encounter form to finish loading before Back. Evidence: browser-date-range.cjs, calendar-date-range.png and calendar-date-range-mobile.png in the existing artifact directory; screenshots inspected.

## Compatible mobileintl develop update (2026-09-28)

Imported the support-ticket inbox into the existing mobile_app namespace while preserving the appointment calendar implementation. Browser smoke checks verified calendar status marks, Range/zoom controls, Mobile App User Support Ticket tab/API, and App Support Ticket list without page errors. Source hashes and appointment-data snapshots confirm no calendar/clinic workflow or booking changes. 29 backend tests passed. Integration details and dependency exclusions are documented in MOBILEINTL_INTEGRATION.md; browser screenshots are in the recorded integration backup directory.

## Slow calendar reads (2026-09-28)

A pending read now survives the 30-second polling interval; subsequent same-range polls are skipped. Navigation aborts obsolete requests and reads have a 45-second deadline. Initial failures show Appointments unavailable, refresh failures retain previous records, and malformed responses cannot silently empty the calendar. Post-mutation reloads force a fresh read. Chromium verified delayed/held responses using controlled network responses and virtual time, timeout/retry, HTTP 500, malformed payloads and initial-load failures. Range/search/count/reload/Back/mobile regression checks also passed. No appointment changes. Dev's authenticated API timeout remains a separate server-side investigation, documented in DEV_CALENDAR_DEPLOYMENT.md.
