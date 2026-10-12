import unittest
from datetime import datetime
from unittest.mock import patch, MagicMock
import frappe
from mobile_app import token_notifications as service
from wa_chat_hub.delivery_outcomes import PatientTemplateNotSentError, PatientTemplateOutcomeUnknownError


class TestTokenNotifications(unittest.TestCase):
    def run_delivery(self, status="Queued", enabled=True, error=None):
        doc = frappe._dict(name="visit-1", status=status, attempts=0, retryable=0,
            next_attempt=None, clinic="clinic-1", channel="channel-1", template_name="opd_token_issued",
            language="en", payload='{"phone":"919999999999","values":["Patient","Clinic","GGN-001"]}')
        db = MagicMock()
        config = {"enabled": enabled, "channel": doc.channel, "template_name": doc.template_name}
        with patch.object(service, "now_datetime", return_value=datetime(2026, 10, 12, 12)), patch.object(service, "frappe") as f, patch.object(service.store, "get", return_value={"token_whatsapp": config}), \
             patch("wa_chat_hub.token_notifications.prepare", return_value=("conversation", {}, "919999999999")), \
             patch("wa_chat_hub.token_notifications.send", side_effect=error, return_value={"message":"message-1","provider_message_id":"provider-1"}) as send:
            f.db = db; f.get_doc.return_value = doc
            f.conf = {"opd_token_whatsapp_enabled": enabled}; f.parse_json = frappe.parse_json
            service.deliver(doc.name)
            return db, send

    def test_persist_send_fence_and_provider_link(self):
        db, send = self.run_delivery()
        self.assertEqual(send.call_count, 1)
        updates = [c.args[2] for c in db.set_value.call_args_list]
        self.assertEqual([u["status"] for u in updates], ["Sending", "Sent"])
        self.assertEqual(updates[-1]["provider_message_id"], "provider-1")
        self.assertEqual(db.commit.call_count, 2)

    def test_duplicate_worker_never_resends(self):
        for status in ("Sending", "Sent", "Delivered", "Unknown", "Skipped"):
            _, send = self.run_delivery(status=status)
            send.assert_not_called()

    def test_disabled_site_never_sends(self):
        db, send = self.run_delivery(enabled=False)
        send.assert_not_called()
        self.assertEqual(db.set_value.call_args.args[2]["status"], "Skipped")

    def test_uncertain_delivery_is_never_retryable(self):
        db, _ = self.run_delivery(error=PatientTemplateOutcomeUnknownError("timeout"))
        result = db.set_value.call_args.args[2]
        self.assertEqual(result["status"], "Unknown")
        self.assertEqual(result["retryable"], 0)

    def test_confirmed_retryable_failure_backoff(self):
        db, _ = self.run_delivery(error=PatientTemplateNotSentError("Rate limited", retryable=True))
        result = db.set_value.call_args.args[2]
        self.assertEqual(result["status"], "Failed")
        self.assertEqual(result["retryable"], 1)
        self.assertIsNotNone(result["next_attempt"])

    def test_invalid_template_not_retried(self):
        db, _ = self.run_delivery(error=PatientTemplateNotSentError("Invalid template"))
        self.assertEqual(db.set_value.call_args.args[2]["retryable"], 0)

    def test_nonretryable_failure_is_not_resent(self):
        db, send = self.run_delivery(status="Failed")
        send.assert_not_called()

    def test_database_error_after_send_is_unknown(self):
        db, _ = self.run_delivery(error=RuntimeError("Message persistence failed"))
        self.assertEqual(db.set_value.call_args.args[2]["status"], "Unknown")
        self.assertEqual(db.set_value.call_args.args[2]["retryable"], 0)

    def test_dispatch_waits_for_commit(self):
        with patch.object(service, "frappe") as f, patch.object(service, "dispatch_now") as dispatch:
            service.schedule_delivery("visit-1")
            dispatch.assert_not_called()
            callback = f.db.after_commit.add.call_args.args[0]
            callback()
            dispatch.assert_called_once_with("visit-1")

    def test_immediate_dispatch_prioritizes_short_queue(self):
        with patch.object(service, "frappe") as f:
            service.dispatch_now("visit-1")
            f.enqueue.assert_called_once_with("mobile_app.token_notifications.deliver", name="visit-1", queue="short", at_front=True)

    def test_dispatch_outage_preserves_successful_checkin(self):
        with patch.object(service, "frappe") as f:
            f.enqueue.side_effect = ConnectionError("Redis unavailable")
            service.dispatch_now("visit-1")
            f.log_error.assert_called_once()
