from unittest import TestCase
from unittest.mock import patch

import frappe

from mobile_app.patches.v1_0 import configure_ai_chat_phone_region as configuration


class TestAIChatConfiguration(TestCase):
	def test_domestic_default_is_set_once(self):
		with patch.object(frappe, "conf", frappe._dict()), patch.object(configuration, "update_site_config") as update:
			configuration.execute()
			configuration.execute()
			update.assert_called_once_with("mobile_app_ai_phone_region", "IN")

	def test_explicit_configuration_is_preserved(self):
		for settings in ({"mobile_app_ai_phone_region": "GB"}, {"mobile_app_ai_require_country_code": 1}):
			with self.subTest(settings=settings), patch.object(frappe, "conf", frappe._dict(settings)), patch.object(configuration, "update_site_config") as update:
				configuration.execute()
				update.assert_not_called()
