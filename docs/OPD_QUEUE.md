# Department-based OPD tokens and rooms

## Entry points and storage

Open `/app/doctor-clinical` and click **OPD** in the existing navigation, alongside Calendar and Doctors. The embedded panel contains OPD Queue, My Room and manager-only OPD Setup. There is no separate OPD Queue page or OPD DocType.

The TV remains a fullscreen-capable custom page at `/app/opd-display`. Click Enable sound once. A custom page is not a new DocType.

- Room setup, department routes, active room sessions and the daily token counter are held in a hidden configuration field on existing **Healthcare Settings**.
- Patient token, saved route and stage state are stored on existing **Mobile Appointment Workflow** records. Appointment check-in extends the same workflow record already used by the calendar. Walk-ins create a workflow linked to the existing Patient record.
- Request deduplication and immutable transition audit data use existing **Version** records. No OPD tables or DocTypes are created by this release.
- Indexed workflow fields support active-visit lookup and unique token/source protection. A locked settings row serializes queue mutations; current reads avoid stale data under MariaDB REPEATABLE READ.

Migration initializes Main Clinic inside the settings configuration. It does not invent room/doctor/department assignments. Configure real assignments before check-in. The initial portal chooses the first accessible clinic; multi-clinic selection is outside this release. The migration also preserves any data from the previous OPD implementation in existing records before deleting the superseded OPD DocType metadata. Old SQL tables are left as migration backups, not used by the app.

## Configuration and permissions

System Manager and Appointment Manager manage setup. Appointment Receptionist and managers issue tokens and handle priority, withdrawals, requeueing, department and doctor changes. Authorized OPD Staff, reception, doctor and manager accounts can open any available enabled room; staff-account assignments are not required. Existing clinical document permissions remain in force; OPD Staff does not itself grant access to clinical forms.

Create rooms with a unique number, purpose, enabled status and doctors. Consultation rooms require at least one practitioner. A practitioner can have one enabled consultation room; a staff account can control only one session at a time. Select the three appropriate rooms in each department route, then Save all changes. Inline room editing affects every department using that room. Incomplete routes, repeated room numbers and conflicting doctor assignments are rejected transactionally.

Create a dedicated System User with only the OPD Display role (and normal baseline roles), then add it under Display accounts and save. Do not give the TV account clinical roles. Its endpoint exposes only clinic/date, active token calls, patient names, rooms and stages. Queue events contain no patient details in realtime broadcasts; clients fetch permission-checked snapshots.

Exact booking disease labels can be mapped explicitly in a department route, one per line. An encounter's explicit department takes precedence. Ambiguous/unmapped labels require reception selection. Reception confirms the department and consultation doctor; a booked-doctor change requires a reason and an assigned-room match.

## Workflow

Issue one daily token when an approved in-person appointment checks in or a walk-in registers. The route is copied onto the visit. A linked mobile booking and Patient Encounter share the same visit. Safe retries return the original result; a repeated matching active walk-in returns its existing token. A patient cannot receive a second active visit in the clinic.

Shared-room queues order priority first, then entry time at the current stage. Consultation queues additionally match the session's practitioner. Call Next reserves a patient; Start removes the TV call; Complete advances to the next saved room. The last stage completes the visit and linked Clinic Appointment. Merely checking in does not complete the linked appointment, including subsequent encounter-to-appointment synchronization.

Patient Not Present places a called patient on hold. Reception requeues them at the end of the current stage. Release handles a called/in-progress patient with a reason. Pause prevents new calls; End requires an empty room. Managers can transfer control to another authorized user with a reason; changing the consultation doctor requires releasing an active patient first. End a room session before editing its doctors or purpose.

Existing Patient and Patient Encounter screens remain the clinical forms. A walk-in without an encounter can open the existing New clinical encounter form when the user has create permission.

Setup changes affect new check-ins. Apply to waiting patients previews affected visits and explicitly applies eligible changes with a reason. It preserves tokens, completed stages and queue-entry timestamps. Called/in-progress patients and doctor mismatches are listed as blocked. Move patients before disabling their saved room. The TV restores current calls on reconnect without replaying announcements. A previous day's unfinished visit retains its date and token; its date is shown on TV to distinguish it from today's daily numbering.

## Reliability

All queue mutations lock the Healthcare Settings configuration row and use current locking reads under MariaDB REPEATABLE READ. This serializes daily token allocation, session ownership and patient claiming. Unique token/source/request keys provide additional duplicate protection. Setup and record revisions reject stale edits. Successful requests write native Version audit entries, including the affected patient/stage transition and handover details; OPD configuration and workflow fields are protected against normal direct writes. Realtime invalidations publish after commit. Failed HTTP responses can be retried with the same request ID.

## Validation completed locally

- Backend integration tests: `mobile_app.tests.test_opd_queue` and `mobile_app.tests.test_appointment_calendar`.
- Separate-process race tests: `mobile_app.tests.test_opd_concurrency.run()` (committed, uniquely named fixtures cleaned in finally; use a local/test site only).
- Chromium: setup edit/save, room occupation, call/start/complete, TV snapshots, live call/recall, disconnect/reconnect without duplicate chimes, calendar navigation and ngrok page access.

Run backend tests from bench/sites with the bench Python environment:

```python
import frappe, unittest
frappe.init(site="YOUR_TEST_SITE")
frappe.connect()
frappe.set_user("Administrator")
suite = unittest.defaultTestLoader.loadTestsFromNames([
    "mobile_app.tests.test_opd_queue",
    "mobile_app.tests.test_appointment_calendar",
])
result = unittest.TextTestRunner(verbosity=2).run(suite)
if result.wasSuccessful():
    from mobile_app.tests.test_opd_concurrency import run
    run()
frappe.destroy()
assert result.wasSuccessful()
```

The regular tests roll back their data. Do not run the committed concurrency fixture on production.

## Deployment

Deploy the reviewed mobile_app changes using the normal server deployment procedure, preserving server-local changes. This release extends existing workflow/settings fields and includes a TV custom page; it requires a database migration. It creates no OPD DocTypes. From the target bench, using the actual site name:

```sh
bench --site SITE backup --with-files
bench --site SITE migrate
bench build --app mobile_app
bench --site SITE clear-cache
bench restart
```

Confirm the new pages and roles, configure actual rooms/routes, test a designated appointment through all three stages, and verify TV realtime over the deployed websocket proxy before opening reception use. Do not reverse code/schema while active token visits remain; pause room calling and resolve/withdraw visits first if rolling back.

Local migration note: the existing login_security app's patches.txt has a post_model_sync section without the required pre_model_sync section, blocking full bench migrate. The local schema/pages were installed using targeted `frappe.model.sync.sync_for("mobile_app")` followed by `mobile_app.opd_setup.after_migrate()` and cache clearing. Fix the unrelated login_security migration metadata in its owning deployment before relying on full migration. Do not blindly skip unrelated production migrations.

Production status: not deployed by this implementation session. The configured dev-sr.butest.tech API credentials returned HTTP 401, and no SSH/deployment pipeline access was available. Local/ngrok verification does not establish production readiness or completion.


## Dedicated room view

My Room shows room summary cards. Open room navigates to `/app/doctor-clinical/room/ROOM_ID` and starts an available room session. View room opens an existing session; only its current controller can operate it. The room view contains the current patient, clinical-record access, waiting queue, on-hold patients and session controls. Its URL can be refreshed/bookmarked, and Back to rooms returns to the overview. Opening a direct link alone does not claim a room; use Start room session there if needed.

Staff assignment is no longer required. Any enabled authorized OPD operator can open an available room, while consultation doctor matching and exclusive session ownership remain enforced. Transfer control is removed from the user interface. Finish & call next atomically completes the current stage and calls the next eligible patient; if the queue is empty, the room remains open. Close room ends an empty session.


### OPD access and multiple rooms (2026-10-06)
OPD Staff and existing authorized OPD operator roles have full OPD setup, reception, and room-action access. The left-menu OPD entry uses these same roles. This supersedes the earlier manager-only setup and single-room-per-user policy. One user can operate multiple rooms, and any authorized OPD operator can act on an existing room session. Opening a room is navigation; starting a session is explicit. One session per room, optimistic version checks, patient claims, and audit attribution remain enforced. Display-only accounts remain restricted to assigned clinic snapshots; clinical form permissions remain native.
