from unittest import TestCase
from unittest.mock import patch

import frappe
from mobile_app.api.v1 import _replace_child_table


class TestProfileSyncIdentity(TestCase):
    def user(self):
        return frappe.get_doc({"doctype": "Mobile App User", "name": "test-user",
            "profiles": [{"name": "profile-a", "profile_name": "A"},
                         {"name": "profile-b", "profile_name": "B"}]})

    def test_reorder_and_edit_preserve_profile_ids(self):
        doc = self.user()
        _replace_child_table(doc, "profiles", [{"name": "profile-b", "profile_name": "Renamed"},
                                               {"name": "profile-a", "profile_name": "A"}])
        self.assertEqual([p.name for p in doc.profiles], ["profile-b", "profile-a"])
        self.assertEqual(doc.profiles[0].profile_name, "Renamed")

    def test_foreign_or_duplicate_profile_is_rejected_before_mutation(self):
        for rows in ([{"name": "foreign"}], [{"name": "profile-a"}, {"name": "profile-a"}]):
            doc = self.user()
            with self.assertRaises(frappe.PermissionError):
                _replace_child_table(doc, "profiles", rows)
            self.assertEqual([p.name for p in doc.profiles], ["profile-a", "profile-b"])

    def test_new_profile_does_not_inherit_existing_identity(self):
        doc = self.user()
        _replace_child_table(doc, "profiles", [{"profile_name": "New", "parent": "other-user"}])
        self.assertNotIn(doc.profiles[0].name, ["profile-a", "profile-b"])
        self.assertEqual(doc.profiles[0].parent, "test-user")

    def test_omitted_profiles_leave_existing_ids_unchanged(self):
        doc = self.user()
        _replace_child_table(doc, "profiles", None)
        self.assertEqual([p.name for p in doc.profiles], ["profile-a", "profile-b"])
