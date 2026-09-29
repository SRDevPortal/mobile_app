# Mobile App updates

The workspace creates its cards once per browser session and keeps the same nodes
and counts when navigating away and back. Its first opening loads the counts;
returning only fetches them if a data-change notification is pending. Cards live
outside the native workspace editor, and the native loading skeleton is hidden
only on this workspace so asynchronous loading cannot shift or replace the cards.

The appointment calendar and the Mobile App User, Mobile App Appointment and App
Support Ticket lists have no periodic data refresh. Opening these views, changing
filters/date ranges and manual Refresh still load data.

Committed inserts, edits, cancellations and deletions publish
`mobile_app_data_changed`. The message contains only a DocType name; each view
fetches data through its existing permission-checked API. ORM `on_change` and
`after_delete` hooks cover the source documents. Code that writes directly with
SQL or `frappe.db.set_value` must explicitly call `mobile_app.realtime.notify_change`
within the same transaction if no parent document save follows.

Notifications are debounced for 200 ms, deferred while the tab/view is hidden,
and queued if another read is running. Reconnecting after a dropped WebSocket
invalidates views once to recover changes missed while disconnected. Failed
requests do not start polling or timed retries. Manual Refresh remains available.

Only the three Mobile App lists override Frappe's timer setup; other lists keep
their normal behavior. The override retains the existing throttle and library
loader. Check this small compatibility adapter when upgrading Frappe ListView.

## Deployment

Deploy the app, clear the site's cache and reload web/worker processes through
the usual process manager. No schema migration is needed for these changes.
Users must reload existing browser tabs to discard their old timers.

Realtime must be reachable and authenticated. For development behind a reverse
proxy, set `mobile_app_realtime_origin` to the public origin. The client then uses
that origin for Socket.IO instead of appending the internal port 9000. The proxy
must forward `/socket.io` HTTP and WebSocket requests to port 9000, preserve Host
and Origin, and send the correct `X-Frappe-Site-Name`. All other requests go to
the web service. A production site with working standard Frappe proxy routing
does not need this setting.

The local site1.local bench has an existing proxy on port 8080 and its ngrok
tunnel must target that port, rather than the web-only port 8000. The local
`start-ngrok-realtime.ps1` script starts this configuration after ngrok is stopped.

## Checks

`node --test mobile_app/tests/test_event_refresh.cjs` checks idle behavior,
coalescing, hidden views, in-flight changes, failure behavior, reconnects and the
list compatibility adapter. The site-backed `mobile_app.tests.test_realtime`
suite checks transaction commit/rollback and actual insert/update/delete hooks.

For workspace layout regressions, check from the first visible frame with a slow
`frappe.desk.desktop.get_desktop_page` response: card positions should stay fixed
and the native workspace skeleton should not appear above them. Navigate to a
list and another workspace and back; the same cards and counts should return
without another metrics request. A change received while away should trigger one
metrics update on return, without replacing the cards.
