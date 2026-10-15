"""Regression coverage for stale module discovery and fresh OPD schema setup."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.model import sync

from mobile_app import hooks, opd_setup


class TestInstallSetup(unittest.TestCase):
    def test_partial_install_recovery_refreshes_modules_before_migration(self):
        self._assert_stale_cache_recovery(hooks.before_migrate)

    def test_install_discovers_schema_even_with_stale_module_cache(self):
        self._assert_stale_cache_recovery(hooks.before_install)

    def _assert_stale_cache_recovery(self, hook_paths):
        # This is the state of a long-running bench just after adding an app.
        cache_data = {"app_modules": {"frappe": ["core"]},
                      "installed_app_modules": {"frappe": ["core"]}}
        cache = Mock()
        cache.get_value.side_effect = cache_data.get
        cache.delete_value.side_effect = lambda key: cache_data.pop(key, None)
        cache.set_value.side_effect = lambda key, value: cache_data.update({key: value})
        local = SimpleNamespace(app_modules={"frappe": ["core"]},
                                flags=frappe._dict(in_install="mobile_app"))
        with (
            patch.object(frappe, "local", local),
            patch.object(frappe, "cache", cache),
            patch.object(frappe, "db", Mock(), create=True),
            patch.object(frappe, "get_all_apps", return_value=["mobile_app"]),
            patch.object(sync, "import_file_by_path") as importer,
            patch.object(sync, "update_progress_bar"),
        ):
            # Exercise the actual schema enumerator before and after the hook.
            sync.sync_for("mobile_app")
            importer.assert_not_called()
            for hook in ([hook_paths] if isinstance(hook_paths, str) else hook_paths):
                frappe.get_attr(hook)()
            sync.sync_for("mobile_app")
            paths = [call.args[0] for call in importer.call_args_list]
            self.assertTrue(any(path.endswith("/mobile_appointment_workflow.json") for path in paths))
            self.assertTrue(any(path.endswith("/opd_state_record.json") for path in paths))
            self.assertEqual(local.module_app["mobileapp"], "mobile_app")
            self.assertNotIn("installed_app_modules", cache_data)

    def test_opd_setup_creates_tables_before_indexes_and_is_retryable(self):
        tables = set()
        db = Mock()
        db.exists.return_value = False
        db.sql.return_value = [(' {"storage_version": 2} ',)]
        configuration = {"default_clinic": "kept", "clinics": {"kept": {"name": "kept"}}}

        def index(doctype, fields, *args):
            self.assertIn(doctype, tables, "Index requested before schema sync")

        def reload_doc(module, kind, name):
            tables.add({"opd_state_record": opd_setup.store.STATE,
                        "mobile_appointment_workflow": opd_setup.store.WORKFLOW}[name])

        db.add_index.side_effect = index
        with (
            patch.object(frappe, "db", db, create=True),
            patch.object(frappe, "flags", frappe._dict()),
            patch.object(frappe, "get_doc", return_value=Mock()),
            patch.object(frappe, "reload_doc", side_effect=reload_doc),
            patch("frappe.custom.doctype.custom_field.custom_field.create_custom_fields"),
            patch.object(opd_setup.store, "lock"),
            patch.object(opd_setup.store, "all", return_value=[{"name": "kept"}]),
            patch.object(opd_setup.store, "configuration", return_value=configuration),
            patch.object(opd_setup.store, "write_configuration") as write,
            patch.object(opd_setup.store, "_save_state") as save,
        ):
            opd_setup.after_migrate()
            opd_setup.after_migrate()
            write.assert_not_called()
            save.assert_not_called()
            self.assertEqual(db.add_index.call_count, 10)
            self.assertIsNone(frappe.flags.in_opd_mutation)


if __name__ == "__main__":
    unittest.main()
