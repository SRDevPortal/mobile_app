function apply_support_ticket_route(listview) {
    const context = listview._support_inbox;
    if (!context) return;
    const params = new URLSearchParams(window.location.search);
    const target = frappe._mobile_support_target;
    const name = target?.name || params.get("support_ticket");
    const source = target?.doctype || params.get("support_source") || "Support Ticket";
    const key = name ? `${source}:${name}` : "";
    if (!name && context._ma_route_key) {
        context._ma_route_key = ""; context._ma_ticket_name = null; context._ma_ticket_doctype = null;
        context._ma_start = 0; context._ma_search = "";
        listview._support_search?.val("");
    }
    if (!name || !["Support Ticket", "App Support Ticket"].includes(source) || context._ma_route_key === key) return;
    delete frappe._mobile_support_target;
    const url = new URL(window.location.href);
    url.searchParams.set("support_ticket", name); url.searchParams.set("support_source", source);
    window.history.replaceState(window.history.state, "", url);
    context._ma_route_key = key;
    context._ma_ticket_name = name;
    context._ma_ticket_doctype = source;
    context._ma_active_ticket_name = name;
    context._ma_active_ticket_doctype = source;
    context._ma_start = 0;
    context._ma_search = "";
    context._ma_support_ticket_filters = {status: "__all", category: "__all"};
    context._ma_support_inbox?.find("select").val("__all");
    listview._support_search?.val(name);
}

/* Central support inbox: both ticket integrations keep their original IDs. */
frappe.listview_settings["App Support Ticket"] = {
    onload(listview) {
        $(listview.page.wrapper).addClass("ma-central-support");
        frappe.require("/assets/mobile_app/js/mobile_app_support_ticket_ui.js", () => {
            if (listview._support_inbox) return;
            const $host = $('<div class="app-support-conversations"></div>').insertBefore(listview.$result);
            const $tools = $('<div class="mb-3 d-flex align-items-center" style="gap: 10px"></div>').appendTo($host);
            const $search = $('<input type="search" class="form-control" placeholder="Search customer, subject or ticket" aria-label="Search tickets" style="max-width: 360px">').appendTo($tools);
            const $previous = $('<button class="btn btn-default btn-sm">Previous</button>').appendTo($tools);
            const $next = $('<button class="btn btn-default btn-sm">Next</button>').appendTo($tools);
            const $page = $('<span class="text-muted"></span>').appendTo($tools);
            const $mount = $('<div></div>').appendTo($host);
            const context = {
                doc: {}, fields_dict: {}, is_new: () => false,
                _ma_global_inbox: true, _ma_inbox_host: $mount, _ma_start: 0,
                _ma_page_update(data) {
                    $previous.prop("disabled", !context._ma_start);
                    $next.prop("disabled", !data?.has_more);
                    $page.text(__("Page {0}", [Math.floor(context._ma_start / 20) + 1]));
                }
            };
            listview._support_inbox = context;
            listview._support_search = $search;
            apply_support_ticket_route(listview);
            const refresh = () => mobile_app.support_ticket_ui.refresh(context, {preserve_composer: true, preserve_scroll: true});
            // Frappe can reuse a cached list without calling its refresh hook.
            frappe.router.on("change", () => {
                const route = frappe.get_route();
                if (route[0] !== "List" || route[1] !== "App Support Ticket") return;
                apply_support_ticket_route(listview);
                refresh();
            });
            let timer;
            $search.on("input", () => {
                clearTimeout(timer);
                timer = setTimeout(() => {
                    context._ma_start = 0; context._ma_search = $search.val();
                    context._ma_ticket_name = null; context._ma_ticket_doctype = null; context._ma_route_key = "";
                    const url = new URL(window.location.href);
                    url.searchParams.delete("support_ticket"); url.searchParams.delete("support_source");
                    window.history.replaceState(window.history.state, "", url);
                    refresh();
                }, 300);
            });
            $previous.on("click", () => {context._ma_start = Math.max(0, context._ma_start - 20); refresh();});
            $next.on("click", () => {context._ma_start += 20; refresh();});
            mobile_app.support_ticket_ui.setup(context);
            listview.$result.hide();
            $(listview.page.wrapper).find(".list-filters, .result-pagination, .layout-side-section").hide();
        });
    },
    refresh(listview) {
        if (!listview._support_inbox) return;
        apply_support_ticket_route(listview);
        listview.$result.hide();
        $(listview.page.wrapper).find(".list-filters, .result-pagination, .layout-side-section").hide();
        mobile_app.support_ticket_ui.refresh(listview._support_inbox, {preserve_composer: true, preserve_scroll: true});
    }
};
