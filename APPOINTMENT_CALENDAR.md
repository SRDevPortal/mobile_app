# Appointment Calendar

Open `/app/doctor-clinical` for the calendar, doctor filters, appointment list and appointment details.

## Booking sources and status

Patient Encounter records with `sr_encounter_type = Appointment` appear automatically, using `pe_appointment_date` and `pe_appointment_time` (encounter date/time are fallbacks). Mobile App Appointment records are also supported. Set their `patient_encounter` link when the integration creates a corresponding encounter; the encounter then becomes the canonical calendar entry without name/phone deduplication.

The workflow is Pending -> Approved -> Checked In. Pending and Approved can instead become Cancelled, with a mandatory reason. Checked In completes the appointment flow and is terminal; there are no consultation-start or checkout actions, waiting-time metrics, or consultation-time metrics.

Patient Encounter has a read-only **Appointment Status** field (`custom_appointment_status`) directly below Encounter Type, visible for Appointment encounters. Its options are Pending, Approved, Cancelled and Checked In. Calendar actions update it in the same transaction. Normal encounter saves derive it from the workflow, so changing that field through a form/API cannot bypass approval. Clinical encounter status and billing remain separate.

`Mobile Appointment Workflow` stores assignment, decisions, timestamps and audit history independently of Android booking payloads. Updates lock the source record, require its expected current status, use authenticated POST requests and enforce permissions server-side. Legacy Rejected states become Cancelled; In Consultation and Completed become Checked In. Existing audit records and timestamps are preserved; obsolete consultation transitions are omitted from the calendar details.

Linked Clinic Appointments follow Pending -> Draft, Approved -> Confirmed, Checked In -> Completed and Cancelled -> Cancelled. The clinic_appointments encounter-sync hook preserves this state on later encounter saves. Deploy both apps' changes together. No patient notifications or Android status updates are sent by these calendar actions.

## Staff access

Assign roles in User -> Roles, then sign out and back in:

- Appointment Manager and System Manager: view all appointments, approve, cancel, assign responsible staff and check in.
- Appointment Receptionist: view only Approved and Checked In appointments, and check in approved bookings. No approval, cancellation, assignment, new-encounter or source-form controls.
- Agent / Appointment Agent: view unassigned bookings and their own assignments; take responsibility before approving, cancelling or checking in.
- Physician / Healthcare Practitioner / Mobile App Doctor: view appointments linked to their practitioner login. No consultation or checkout actions.

Manager access takes precedence over reception. Otherwise reception restrictions take precedence over Agent/doctor roles. Source forms retain their existing Frappe permissions; these calendar roles do not grant general Patient Encounter access. Assignment selection is manager-only and limited to eligible enabled staff. A practitioner without an assigned booking remains visible with zero counts; doctor logins receive only their own practitioner records.

## Calendar and filtering

Search matches patient names/IDs, normalized mobile numbers (including country prefixes), doctor names/IDs, booking/encounter IDs, email and assigned agent within the displayed period and active filters. Only authorized records reach the browser.

The main doctor count matches the appointments displayed after date, search, doctor, channel and status filters, including Checked In bookings. A separate awaiting count includes only Pending and Approved; check-in reduces that count without hiding the appointment. The right-side appointment list covers the same displayed period and filters as the calendar; each entry includes its date.

Day/Week/Month keep the selected date as their anchor. The toolbar date picker and previous/next controls allow past/future navigation. The browser remembers date/view, custom range and time-scale zoom per user across hard refreshes, without caching appointment data. On a first visit with no saved selection and no bookings in the current period, a permission-scoped lookup within 31 days either side of today selects the nearest appointment. Explicit Today/date choices are respected even when empty.

Refresh runs every 30 seconds while visible, retaining filters and scroll position. Stale responses are ignored; failures retain existing records with a retry notice. Opening an encounter and returning preserves the calendar selection and scroll. Calendar blocks have a default 30-minute display duration; booking dates/times are not rescheduled. Narrow screens use Day view.

## Installation and verification

Run the normal site backup/migration process and clear cache. Migration synchronizes Mobile Appointment Workflow and Page roles, creates the calendar staff roles and Appointment Status custom field, normalizes legacy states, and backfills existing Appointment encounters and linked Clinic Appointments. FullCalendar and icons come from the installed Frappe version.

API: `mobile_app.api.appointment_calendar.get_calendar`, `get_appointment`, `update_appointment`.

Eighteen backend tests in `mobile_app/tests/test_appointment_calendar.py` cover transitions, cancellation reasons, terminal/invalid/stale actions, staff permissions, source cancellation, legacy normalization, encounter field synchronization, deduplication and Clinic Appointment synchronization. Fixtures roll back. Normal-save verification also exercises Patient/Customer creation and encounter document hooks without persisting fixtures.

Chromium verified the read-only encounter field, simplified terminal details, approval/cancellation controls, doctor counts, encounter Back and reload. Prior browser coverage includes role restrictions, mobile/doctor search, date/view navigation, filters, delayed/failed requests and responsive layout. A live Android request and real Google Meet delivery were not exercised.

Local practitioner profiles were copied from dev on 2026-09-25 (9 active, 7 disabled). Synthetic September 25-26 appointments were retained for user testing; this change does not delete appointments. Their states and availability can change during manual testing.

## Calendar time scale

Day and Week views include minus/plus controls for 50%, 75%, 100%, 125%, 150% and 200% vertical zoom. Click the percentage to reset to 100%. Ctrl + mouse wheel over the time grid changes the scale around the pointer; ordinary wheel scrolling moves through the day. Buttons preserve the visible centre time where the start/end of the day allows it. Month view disables time-scale controls and retains the preference for the next Day/Week view. Appointment times, filters and counts are unchanged.

The calendar overrides Frappe's fixed slot-height style locally, rebuilds FullCalendar slot/event geometry, and uses fractional DOM measurements to prevent drift at 125%. Browser verification covers bounds/reset, event-time alignment, scroll anchoring, ordinary/Ctrl-wheel behavior, refresh/reload persistence, Month/Week transitions, encounter navigation and mobile controls.

## Appointment status marks

Every calendar block has a compact status symbol: a clock for Pending, one check for Approved, two checks for Checked In, and a cross for Cancelled. Doctor colors remain unchanged. The legend, daily schedule badges and detail badge use the same symbols with full status text. Calendar blocks expose the full status in hover titles and accessible names. Marks are also rendered in Month overflow popovers and remain separate from truncated patient names at smaller zoom levels.

## Date range selection

The Range button opens inclusive From/To controls with Apply range. Select 1-62 days, including across month boundaries. The range calendar uses a week-row grid with dates outside the selected range shaded; those dates have no fetched or displayed appointments. Doctor totals, search, status filters and the appointment list use the exact range. Previous/next shifts by the range length; Today returns to the current week. Day/Week/Month exits Range mode. The selection persists per user across refresh and encounter navigation. On small screens the range grid scrolls horizontally to keep cards readable.

Removed the duplicate Daily schedule heading/date picker and All/Online/Offline tabs from the right panel. Appointment mode remains visible in booking details. The panel now starts with status counters and lists appointments chronologically across the displayed period.
