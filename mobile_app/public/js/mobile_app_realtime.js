/* Refresh Mobile App views on committed changes, never on a polling interval. */
frappe.provide("mobile_app.realtime");

(() => {
    const watchers = new Map();
    const listTypes = new Set(["Mobile App User", "Mobile App Appointment", "App Support Ticket"]);
    let flushTimer;

    // A reverse-proxied development site must use its public HTTPS origin.
    if (frappe.boot.mobile_app_realtime_origin === window.location.origin) {
        frappe.realtime.get_host = () => `${window.location.origin}/${frappe.boot.sitename}`;
    }

    function scheduleFlush() {
        clearTimeout(flushTimer);
        flushTimer = setTimeout(flush, 200);
    }

    function flush() {
        if (document.hidden) return;
        for (const watcher of watchers.values()) {
            if (!watcher.dirty || watcher.running || !watcher.active()) continue;
            const version = watcher.version;
            watcher.dirty = false;
            watcher.running = true;
            Promise.resolve().then(watcher.refresh).then(result => {
                if (result === false) watcher.dirty = true;
            }).catch(() => {
                // Keep the invalidation for the next event, navigation or reconnect.
                // A failed request must not start a retry loop.
                watcher.dirty = true;
            }).finally(() => {
                watcher.running = false;
                if (watcher.version !== version) scheduleFlush();
            });
        }
    }

    function changed(message) {
        for (const watcher of watchers.values()) {
            if (!watcher.types.has(message?.doctype)) continue;
            watcher.version++;
            watcher.dirty = true;
        }
        scheduleFlush();
    }

    mobile_app.realtime.watch = (key, types, active, refresh) => {
        if (watchers.has(key)) {
            Object.assign(watchers.get(key), {types: new Set(types), active, refresh});
        } else {
            watchers.set(key, {types: new Set(types), active, refresh, dirty: false, running: false, version: 0});
        }
    };
    mobile_app.realtime.flush = scheduleFlush;

    // Called by each Mobile App list script before ListView.setup_defaults runs.
    mobile_app.realtime.configure_lists = () => {
        const proto = frappe.views.ListView.prototype;
        if (proto._mobileAppEventRefresh) return;
        proto._mobileAppEventRefresh = true;
        const originalSetup = proto.patch_refresh_and_load_lib;
        const originalRealtime = proto.setup_realtime_updates;
        proto.patch_refresh_and_load_lib = function () {
            if (!listTypes.has(this.doctype)) return originalSetup.call(this);
            this.refresh = frappe.utils.throttle(this.refresh.bind(this), 1000);
            this.load_lib = new Promise(resolve => {
                if (this.required_libs) frappe.require(this.required_libs, resolve);
                else resolve();
            });
            mobile_app.realtime.watch(`list:${this.doctype}`, [this.doctype],
                () => frappe.get_route_str() === this.page_name && Boolean(this.$result?.length),
                () => {
                    if (this.$checks?.length || this.avoid_realtime_update()) return false;
                    // A server change invalidates Frappe's three-second args cache.
                    this.last_args = null;
                    return this.refresh();
                });
        };
        proto.setup_realtime_updates = function () {
            if (!listTypes.has(this.doctype)) return originalRealtime.call(this);
            // The shared event watcher handles inserts, edits and deletions together.
        };
    };

    $(document).ready(() => {
        frappe.realtime.on("mobile_app_data_changed", changed);
        let connected = false;
        frappe.realtime.socket?.on("connect", () => {
            // Catch up once after a dropped connection; no periodic data requests.
            if (connected) {
                for (const watcher of watchers.values()) {
                    watcher.version++;
                    watcher.dirty = true;
                }
                scheduleFlush();
            }
            connected = true;
        });
        frappe.router.on("change", scheduleFlush);
    });
    document.addEventListener("visibilitychange", scheduleFlush);
    $(document).on("change", ".list-row-checkbox, .list-check-all", scheduleFlush);
    $(document).on("page-change", scheduleFlush);
})();
