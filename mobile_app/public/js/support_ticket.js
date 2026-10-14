frappe.ui.form.on("Support Ticket", {
    refresh(frm) {
        if (frm.is_new()) return;
        frm.add_custom_button(__("Open Chat"), () => {
            frappe._mobile_support_target = {name: frm.doc.name, doctype: "Support Ticket"};
                        frappe.set_route("List", "App Support Ticket");
        });
    }
});
