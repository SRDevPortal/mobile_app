mobile_app.realtime.configure_lists();
frappe.listview_settings["Mobile App Appointment"] = {
	onload(listview) {
		listview.page.add_inner_button(__("Doctor Portal"), () => {
			frappe.set_route("doctor-clinical");
		});
	},
};
