/* Customer chat messages are separate from Frappe document comments. */
(() => {
    async function refresh_badges(listview) {
        if (listview._message_counts_running) { listview._message_counts_again = true; return; }
        const names = (listview.data || []).map(row => row.name);
        if (!names.length) return;
        listview._message_counts_running = true;
        try {
            const counts = {};
            for (let i = 0; i < names.length; i += 200) {
                Object.assign(counts, await frappe.xcall("mobile_app.api.support_ticket.get_support_ticket_message_counts", {ticket_names: names.slice(i, i + 200)}));
            }
            listview.$result.find(".support-customer-message-badge").remove();
            listview.$result.find(".list-row-container").each((index, node) => {
                const doc = listview.data[index];
                const count = counts[doc?.name] || 0;
                if (!count) return;
                const $badge = $('<button type="button" class="btn btn-xs support-customer-message-badge"></button>')
                    .css({background: "#e8f0ff", color: "#245bdb", marginLeft: "8px", fontWeight: "600"})
                    .text(__("{0} new", [count]))
                    .attr("title", __("Unread customer messages — open chat"))
                    .on("click", event => {
                        event.preventDefault(); event.stopPropagation();
                        frappe._mobile_support_target = {name: doc.name, doctype: "Support Ticket"};
                        frappe.set_route("List", "App Support Ticket");
                    });
                $(node).find(".list-row .level-right").first().prepend($badge);
            });
        } catch (error) {
            // Keep the list usable; retry on the next refresh or realtime event.
        } finally {
            listview._message_counts_running = false;
            if (listview._message_counts_again) {
                listview._message_counts_again = false;
                refresh_badges(listview);
            }
        }
    }
    frappe.listview_settings["Support Ticket"] = {
        onload(listview) {
            mobile_app.realtime?.watch("support-customer-badges", ["Support Ticket", "Support Ticket Message"],
                () => !document.hidden && listview.$result.is(":visible"),
                () => refresh_badges(listview));
        },
        refresh: refresh_badges
    };
})();
