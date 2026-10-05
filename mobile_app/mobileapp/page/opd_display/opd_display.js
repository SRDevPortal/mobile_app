frappe.pages["opd-display"].on_page_load = wrapper => {
    frappe.require(["/assets/mobile_app/css/opd_queue.css", "/assets/mobile_app/js/opd_queue.js"], () => {
        wrapper.opd = new mobile_app.OPDPortal(wrapper, true);
        wrapper.opd.show();
    });
};
frappe.pages["opd-display"].on_page_show = wrapper => wrapper.opd?.show();
frappe.pages["opd-display"].on_page_hide = wrapper => wrapper.opd?.hide();
