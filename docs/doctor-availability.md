# Doctor working hours and leave

Open **Mobile App → Doctors** and select a practitioner.

## Weekly working hours

The **Working Schedule** and **Leave / Holiday** controls and editor are above
the availability calendar. Select **Working Schedule**, then
select the working days, start/end time and appointment length. For example,
Monday–Friday, 10:00–17:00, 30 minutes produces 14 appointments per working day.
Click **Save weekly schedule** to generate and save the appointment slots.
The editor uses the doctor's first enabled linked schedule, or creates one if
none exists. The schedule selector, disable toggle and individual-slot editor
are no longer shown.
Unchecked weekdays have no slots in that schedule. Other linked schedules still
apply.
When a schedule is shared by doctors, saving copies it for the selected doctor.

## Leave dates

Select **Leave / Holiday**, then choose **Single day** or **Date range** and
enter the date(s). You can also click a day or drag across days on the monthly
calendar. Click **Mark Leave / Holiday** to save the inclusive date range.
The calendar shows working hours in green and leave/holiday dates in red.
The calendar reads `calendar_days` from Frappe for the full visible 42-day range;
each date includes effective working windows after date overrides. The UI shows
one Available label per working day, without times, slot positions or booking
counts. Each full date cell has a status background (mint for available, pink
for leave, gray for off/inactive) and centered status text. Days without hours show Weekly off; leave and inactive dates retain
their status. The editor labels unsaved changes and missing schedules so
default form values are not confused with saved calendar hours.
Use **Remove leave** for the selected day/range to restore normal weekly hours.
Removal preserves unrelated date-specific working-hours overrides. Leave
overrides all weekly schedules for that practitioner. Dates must be today or
later; a range can contain at most 366 days. A range save is atomic and checks
permissions and concurrent edits before writing.

Existing appointments are retained and their count is shown to the operator;
they must be handled through the existing appointment workflow. New bookings on
leave dates are rejected during server-side booking validation as well as being
removed from the available-slot response.

## Flutter and deployment

The existing `mobile_app.api.practitioners.availability` endpoint reads these
settings. The Node `/api/v1/doctors/:id/availability` proxy and Flutter's booking
screen already consume it. Flutter hides a doctor when this endpoint returns no
slots for the selected day. A new booking flow/date selection fetches availability;
there is no live push refresh of an already open screen. Checkout revalidates slots.

Implementation is in the Frappe `mobile_app` repository at
`/home/mit/frappe-bench/apps/mobile_app`, not the older Windows backend snapshot.
Deploy the doctor directory API/UI, `doctor_schedule.py`, the practitioner API
changes and the `Doctor Availability Exception` DocType together, then run
`bench --site <site> migrate` and the normal build/restart procedure. Refresh Desk
after deployment. These changes have been tested locally; production deployment
is a separate step. No Flutter or Node changes are needed for these two settings.

## Verification

`mobile_app.tests.test_doctor_directory` contains 18 site tests, using synthetic
records that are rolled back. They cover weekday slot generation, leave and
restoration, rejecting bookings on leave, preserving existing appointments,
permissions, stale edits, shared schedule isolation, invalid time ranges,
inclusive leave ranges, past-date rejection and rollback of failed range saves.
Browser checks cover preparing/saving hours, editing and saving without a preview,
calendar leave markers, single-day and range saving, dragging to select a range,
restoration, editor placement above the calendar and a 390px viewport.
The calendar was also checked against all 42 API-returned dates for each of the
four local practitioner records, using read-only snapshots of their schedules.

Weekly saves update only schedule records and their practitioner child links,
under the practitioner write permission, timestamp check and row lock. New
schedules and links are validated. Unrelated missing legacy profile links are
preserved rather than causing a whole-profile save failure. The missing-link
regression test covers creation and updates, including profile/link preservation.
The affected local Dr Aayushi Pathak record was also tested with all temporary
schedule changes rolled back.


## About Doctor

Healthcare Practitioner includes a plain-text **About Doctor** field (`custom_about_doctor`)
after Diseases. MobileApp creates and positions it through its install/migration hook.
The Doctors panel on `/app/doctor-clinical` displays the same value and provides
**Edit About Doctor** to users with write access to that practitioner. Saving the
biography preserves unsaved availability edits and rejects stale profile changes.

Both directory responses and `mobile_app.api.practitioners.list_doctors` expose
`about_doctor` as a string, empty when no biography has been entered. Mobile clients
should display this as plain text. Changes saved in either the practitioner form or
the Doctors panel are reflected in the shared field and API response.


## Practitioner charges

The Doctors panel displays **Out Patient Consulting Charge** beneath About Doctor.
The value comes directly from the selected Healthcare Practitioner
(`op_consulting_charge`), including zero amounts, and uses the site's currency and
number formatting. Edit the amount on the practitioner form, save, then refresh the
Doctors panel to see the current charge.

## Appointment types

In the Doctors panel, select **OPD (in-person)**, **Online**, or both, then click
**Save appointment types**. Both can be disabled to stop new mobile bookings.
OPD defaults to enabled to preserve existing booking behavior; existing Online
choices are retained. Saving checks practitioner write access and rejects stale
edits. Existing appointments are retained.

Directory and mobile practitioner responses include `accepts_opd_appointments`
and `accepts_online_appointments`. Mobile booking validation rejects a new or
rescheduled booking when its appointment type is disabled. Existing OPD queue
operations and clinic check-in are unchanged. The prior online-only API remains
compatible and changes only the Online option.
