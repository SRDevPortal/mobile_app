# OPD token WhatsApp notifications

New tokens create one durable OPD Token Notification record per visit, in the same transaction. Existing tokens, reprints and repeated check-ins do not create notifications. Delivery is dispatched to the short queue immediately after the token transaction commits. The scheduler scans every minute as recovery if immediate dispatch fails. Disabled or missing-number records are recorded as Skipped and are not replayed when sending is enabled.

## Configuration

Install both mobile_app and wa_chat_hub changes and run `bench --site <site> migrate`. Ensure the scheduler and short queue worker are running. Under Doctor Clinical ? OPD ? Branch ? Token WhatsApp select an active Interakt channel, an approved chat-enabled template and its language. Sending defaults to disabled both globally and per branch. After reviewing the intended site and recipient, enable the site explicitly with `bench --site <site> set-config opd_token_whatsapp_enabled 1`. Leave this off on localhost except during an authorized test. Disabling either switch stops unsent jobs.

Template name: use the exact approved name (currently configured as opd_visit_confirmation). Configure a text template with exactly three body parameters in this order: patient name, token number, appointment details (date, scheduled time when available, doctor name and room sequence). No header or button parameters. Submit and approve the template in the intended Interakt account before enabling it. Example:

Hello {{1}}, your OPD check-in is confirmed.

Token Number: {{2}}
Appointment Details: {{3}}

Please follow the instructions provided by the clinic and wait until your token is called.

No department, diagnosis or medical notes are sent. The phone comes from the linked Patient mobile field and is normalized by WhatsApp Hub. Verify the patient's number and messaging eligibility before enabling notifications; appointment consent is not automatically inferred by this feature.

## Delivery and failure handling

The notification freezes the token details, channel and recipient at issuance. It resolves the approved template and creates/reuses a conversation on that exact account; no fallback sender is chosen. Account/template changes before dispatch skip the old queued notification for review.

The worker commits Sending before calling Interakt. Repeated workers cannot resend a Sending/Sent record. Confirmed retryable rejection uses 2/4/8 minute backoff with at most three attempts; manual retry is restricted to safely retryable failures within the same limit. Timeouts, worker crashes or failures after provider acceptance become Unknown and are never automatically retried. Review those in WhatsApp Hub before any deliberate resend. This is duplicate protection, not an exactly-once delivery guarantee from the provider.

Provider IDs and Chat Message links are retained. Existing signed webhook processing updates Chat Message; the receipt and scheduled reconciler reflect Delivered/Read/Failed. Popup closing does not cancel delivery. Check-in succeeds even when WhatsApp is disabled or the recipient is unavailable. Redis dispatch is decoupled from the token transaction by the durable outbox.

## Validation

Automated tests mock provider sending. A real end-to-end check requires an approved template, an explicitly selected channel, a verified recipient and an enabled site. No patient messages were sent during implementation. Check-in should be tested with transaction rollback, repeat submissions, missing phone, two concurrent workers, provider timeout, confirmed rate limit and delivery webhook events.
