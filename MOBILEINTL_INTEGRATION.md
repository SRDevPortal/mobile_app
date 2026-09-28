# Compatible mobileintl_app integration

Source: https://github.com/SRDevPortal/mobileintl_app, branch `develop`, commit `7da84e3` (Enable account-owned AI chat and profile identity sync).
Integrated into the existing local `mobile_app` on 2026-09-28 after the user selected compatible merging rather than a separate site.

## Applied

- App Support Ticket and App Support Ticket Message schema/controllers.
- Mobile App User Support Ticket tab and inbox API/UI, including reply/status/read functionality.
- Support-ticket list UI and scoped styles.
- Form/list JavaScript hooks and existing workspace ticket links routed to App Support Ticket.
- Idempotent support schema/permission migrations; removed the scheduled retirement patch that would delete the newly restored ticket DocTypes.
- Namespaced imports, API paths and assets adapted from mobileintl_app to mobile_app. The site still has one owner for the shared MobileApp module.

## Preserved

Appointment Calendar page/code/styles, Range controls, zoom, status marks, permissions, appointment lifecycle, Patient Encounter Appointment Status, Mobile Appointment Workflow, Clinic Appointment sync and all existing appointment records. Existing user-operation locking, token-authenticated onboarding permission fixes, profile identity preservation and user deletion API remain intact; incoming versions would regress these protections. The locally deleted workspace JSON stays deleted; only existing database support shortcuts are updated.

## Intentionally not activated

The international-only `require_ai_chat_country_codes` and `enable_account_owned_ai_chat` configuration migrations require newer wa_chat_hub code/schema. This local hub does not implement those flags. They were not installed or run, and site settings were not changed. Account-owned AI chat requires a separate compatible wa_chat_hub update; this integration does not claim to enable it.

No second app was installed and no remote changes were pushed. This is a selective source integration, not a merge of the unrelated repository histories. The raw develop checkout is retained for comparison at `/home/jagmohan/.codex-work/mobileintl-sync-20260928-083557/upstream`.

## Validation and recovery

29 backend tests passed: appointment calendar/lifecycle/permissions (18), profile identity sync (4), appointment utilities (4), and transactional support inbox integration (3). Browser checks passed for the customized calendar, status marks, range/zoom controls, Mobile App User support inbox/API and App Support Ticket list with no JavaScript errors. Syntax/namespace and diff whitespace checks passed. All support fixtures rolled back.

Protected source hashes and full before/after snapshots of Patient Encounter, Mobile Appointment Workflow, Clinic Appointment and Mobile App Appointment match. Module ownership and site configuration are unchanged.

A database backup was taken before schema updates. Source archives, the original site configuration and appointment snapshots are stored in `/home/jagmohan/.codex-work/mobileintl-sync-20260928-083557`. Git stash `08c6949f698958567f039c76037924d58a0c2faa` preserves the pre-integration mobile_app work; it remains retained. Do not restore the entire site backup over newer user data without reviewing subsequent changes.
