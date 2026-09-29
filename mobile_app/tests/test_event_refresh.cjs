const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { EventEmitter } = require("node:events");

function harness() {
    const events = new EventEmitter(), socket = new EventEmitter(), document = new EventEmitter();
    document.hidden = false;
    document.addEventListener = document.on.bind(document);
    const timers = new Map(), intervals = [];
    let next = 0;
    class ListView {
        patch_refresh_and_load_lib() { intervals.push("standard-list"); }
        setup_realtime_updates() { this.standardRealtime = true; }
    }
    const context = {
        console, document, window: { location: { origin: "https://example.test" } },
        mobile_app: { realtime: {} },
        setTimeout(fn) { timers.set(++next, fn); return next; },
        clearTimeout(id) { timers.delete(id); },
        setInterval() { throw Error("Mobile App must not poll"); },
        $: () => ({ ready: fn => fn(), on: () => {} }),
        frappe: {
            provide() {}, boot: {}, views: { ListView },
            realtime: { on: events.on.bind(events), socket },
            router: new EventEmitter(), utils: { throttle: fn => fn },
            get_route_str: () => "List/Mobile App User/List",
        },
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../public/js/mobile_app_realtime.js"), "utf8"), context);
    return {
        api: context.mobile_app.realtime, events, socket, document, timers, intervals, ListView,
        async flush() {
            const pending = [...timers.values()]; timers.clear();
            pending.forEach(fn => fn());
            await new Promise(resolve => setImmediate(resolve));
        },
        change(doctype = "Mobile App User") { events.emit("mobile_app_data_changed", { doctype }); },
    };
}

test("idle views never poll; bursts coalesce and unrelated changes do not refresh", async () => {
    const h = harness(); let calls = 0;
    h.api.watch("view", ["Mobile App User"], () => true, () => { calls++; });
    assert.equal(h.timers.size, 0);
    h.change("Patient"); await h.flush(); assert.equal(calls, 0);
    h.change(); h.change(); h.change(); await h.flush();
    assert.equal(calls, 1); assert.equal(h.timers.size, 0);
});

test("changes received while hidden are loaded once when visible", async () => {
    const h = harness(); let calls = 0;
    h.api.watch("view", ["Mobile App User"], () => true, () => { calls++; });
    h.document.hidden = true; h.change(); await h.flush(); assert.equal(calls, 0);
    h.document.hidden = false; h.document.emit("visibilitychange"); await h.flush();
    assert.equal(calls, 1); assert.equal(h.timers.size, 0);
});

test("a change during a request queues one follow-up without overlapping reads", async () => {
    const h = harness(); let calls = 0, finish;
    h.api.watch("view", ["Mobile App User"], () => true, () => {
        calls++;
        return calls === 1 ? new Promise(resolve => { finish = resolve; }) : undefined;
    });
    h.change(); await h.flush(); h.change(); h.change(); await h.flush();
    assert.equal(calls, 1);
    finish(); await new Promise(resolve => setImmediate(resolve)); await h.flush();
    assert.equal(calls, 2); assert.equal(h.timers.size, 0);
});

test("failed reads do not enter automatic retry loops", async () => {
    const h = harness(); let calls = 0;
    h.api.watch("view", ["Mobile App User"], () => true, () => { calls++; throw Error("offline"); });
    h.change(); await h.flush(); assert.equal(calls, 1); assert.equal(h.timers.size, 0);
    h.change(); await h.flush(); assert.equal(calls, 2); assert.equal(h.timers.size, 0);
});

test("only Mobile App lists lose the five-minute timer", () => {
    const h = harness(); h.api.configure_lists();
    const mobile = Object.assign(new h.ListView(), { doctype: "Mobile App User", refresh() {} });
    mobile.patch_refresh_and_load_lib(); mobile.setup_realtime_updates();
    assert.equal(h.intervals.length, 0); assert.equal(mobile.standardRealtime, undefined);
    const unrelated = Object.assign(new h.ListView(), { doctype: "Item" });
    unrelated.patch_refresh_and_load_lib(); unrelated.setup_realtime_updates();
    assert.equal(h.intervals.length, 1); assert.equal(unrelated.standardRealtime, true);
});

test("reconnect catches up once and idle connections do not refresh", async () => {
    const h = harness(); let calls = 0;
    h.api.watch("view", ["Mobile App User"], () => true, () => { calls++; });
    h.socket.emit("connect"); await h.flush(); assert.equal(calls, 0);
    h.socket.emit("connect"); await h.flush(); assert.equal(calls, 1);
    assert.equal(h.timers.size, 0);
});

test("record notifications bypass the list's recent identical-request cache", async () => {
    const h = harness(); h.api.configure_lists(); let calls = 0;
    const list = Object.assign(new h.ListView(), {
        doctype: "Mobile App User", page_name: "List/Mobile App User/List",
        $result: {length: 1}, last_args: "cached request", avoid_realtime_update: () => false,
        refresh() { if (this.last_args === null) calls++; },
    });
    list.patch_refresh_and_load_lib(); h.change(); await h.flush();
    assert.equal(calls, 1);
});
