/* Mobile App User list - doctors must not create patients manually */

frappe.listview_settings["Mobile App User"] = {
	onload(listview) {
		if (mobile_app.desk?.is_mobile_app_admin?.()) return;

		listview.page.btn_primary?.hide();
		listview.page.inner_toolbar?.find(".btn-new-doc").hide();
	},

	refresh(listview) {
		if (mobile_app.desk?.is_mobile_app_admin?.()) return;
		listview.page.btn_primary?.hide();
	},
};
