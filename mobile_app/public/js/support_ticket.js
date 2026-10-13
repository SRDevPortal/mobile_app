// Open the customer conversation from the app's existing Support Ticket record.
frappe.ui.form.on("Support Ticket", {
    refresh(frm) {
        if (frm.is_new()) return;
        frm.add_custom_button(__("Open Chat"), async () => {
            let user = frm.doc.custom_mobile_app_user;
            if (!user && frm.doc.user_id) {
                const direct = await frappe.db.get_value("Mobile App User", {name: frm.doc.user_id}, "name");
                user = direct.message?.name;
                if (!user) {
                    const external = await frappe.db.get_value("Mobile App User", {external_id: frm.doc.user_id}, "name");
                    user = external.message?.name;
                }
            }
            if (!user) {
                frappe.msgprint(__("This ticket is not linked to a Mobile App User."));
                return;
            }
            frappe.route_options = {
                open_support_ticket_chat: 1,
                support_ticket_name: frm.doc.name,
                support_ticket_doctype: "Support Ticket",
            };
            frappe.set_route("Form", "Mobile App User", user);
        });
    },
});
