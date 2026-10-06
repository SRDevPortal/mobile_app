/* Appointment operations calendar. Uses Frappe's bundled FullCalendar. */
(() => {
    const API = "mobile_app.api.appointment_calendar";
    const esc = value => frappe.utils.escape_html(String(value == null ? "" : value));
    const icon = name => frappe.utils.icon(name, "sm");
    const clinicIcon = name => {
        const shapes = {
            encounters: '<path d="M9 4H6a2 2 0 0 0-2 2v14h16V6a2 2 0 0 0-2-2h-3"/><rect x="9" y="2" width="6" height="4" rx="1"/><path d="M8 11h8M8 15h5"/>',
            doctors: '<circle cx="12" cy="6" r="3"/><path d="M4 21v-3a8 8 0 0 1 16 0v3M8 12v4a2 2 0 0 0 4 0v-5M16 13v3"/><circle cx="16" cy="18" r="2"/>',
        };
        return `<svg class="ac-clinic-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${shapes[name]}</svg>`;
    };
    // Dark doctor colours keep white appointment labels legible in every view.
    const colors = ["#176b63", "#456579", "#316596", "#667333", "#885367", "#366b4b"];
    const labels = {approve: "Approve appointment", cancel: "Cancel appointment", check_in: "Check in", claim: "Take responsibility"};
    const statusClass = value => String(value).toLowerCase().replaceAll(" ", "-");
    const statusMark = status => {
        const shapes = {
            Pending: '<circle cx="8" cy="8" r="5.5"/><path d="M8 4.5V8l2.5 1.5"/>',
            Approved: '<path d="m3 8 3 3 7-7"/>',
            "Checked In": '<path d="m1 8 3 3 7-7M8 10l1 1 6-7"/>',
            Cancelled: '<path d="m4 4 8 8M12 4l-8 8"/>',
        };
        return `<span class="ac-status-mark ${statusClass(status)}" aria-hidden="true"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${shapes[status] || shapes.Pending}</svg></span>`;
    };
    const activeRoute = () => frappe.get_route()[0] === "doctor-clinical";
    const zoomLevels = [50, 75, 100, 125, 150, 200];

    class AppointmentCalendar {
        constructor(wrapper) {
            this.wrapper = wrapper;
            this.page = frappe.ui.make_app_page({parent: wrapper, title: __("Appointment Calendar"), single_column: true});
            $(wrapper).addClass("ma-calendar-page");
            this.storageKey = `appointment-calendar:view:v1:${frappe.session.user}`;
            let saved;
            try {
                const value = JSON.parse(localStorage.getItem(this.storageKey));
                if (value && typeof value.date === "string" && moment(value.date, "YYYY-MM-DD", true).isValid() &&
                    ["agendaDay", "agendaWeek", "month", "appointmentRange"].includes(value.view)) saved = value;
            } catch (e) { /* A blocked or invalid browser preference must not stop the calendar. */ }
            this.date = moment(saved?.date || frappe.datetime.get_today());
            this.viewName = saved?.view || "agendaWeek";
            this.customRange = this.validDateRange(saved?.range?.from, saved?.range?.to) ? saved.range : null;
            if (this.viewName === "appointmentRange" && !this.customRange) this.viewName = "agendaWeek";
            this.zoom = zoomLevels.includes(saved?.zoom) ? saved.zoom : 100;
            this.initialLookup = !saved;
            this.doctor = ""; this.channel = "All"; this.queue = "All"; this.search = ""; this.rows = []; this.doctors = [];
            this.request = 0; this.detailRequest = 0; this.colors = {};
            this.canCreate = frappe.model.can_create("Patient Encounter");
            this.opdOnly = (frappe.user_roles || []).includes("OPD Staff") && !(frappe.user_roles || []).some(r => ["System Manager","Appointment Manager","Appointment Receptionist","Agent","Appointment Agent","Physician","Healthcare Practitioner","Mobile App Doctor"].includes(r));
            if (this.opdOnly) this.section = "opd";
            this.render();
            this.canOPD = frappe.session.user === "Administrator" || (frappe.user_roles || []).some(r => ["System Manager", "Appointment Manager", "OPD Staff", "Appointment Receptionist", "Physician", "Healthcare Practitioner", "Mobile App Doctor"].includes(r));
            this.$root.find('[data-action="opd"]').toggle(this.canOPD);
            this.bind();
            this.initCalendar();
            if (this.opdOnly) { this.$root.find('[data-action="calendar"], [data-action="doctors"], [data-action="encounters"]').hide(); this.switchSection("opd"); }
            mobile_app.realtime.watch("calendar", ["Mobile App Appointment", "Patient Encounter",
                "Mobile Appointment Workflow", "Healthcare Practitioner", "Patient", "Clinic Appointment"],
                () => activeRoute() && $(this.wrapper).is(":visible"),
                () => {
                    if (this.loading || this.mutating) return false;
                    // Names and doctor availability can change without a booking revision.
                    this.revisions?.clear();
                    return this.fetch(false, true);
                });
        }
        rememberView() {
            if (this.initialLookup) return;
            try {
                // Store view preferences only, never appointment or patient data.
                localStorage.setItem(this.storageKey, JSON.stringify({date: this.date.format("YYYY-MM-DD"), view: this.viewName, zoom: this.zoom, range: this.customRange}));
            } catch (e) { /* Navigation still works when browser storage is unavailable. */ }
        }
        button(action, text, cls = "", extra = "") {
            return `<button type="button" class="ac-btn ${cls}" data-action="${action}" ${extra}>${text}</button>`;
        }
        render() {
            this.$root = $(`<div class="ac-shell">
                <nav class="ac-rail" aria-label="Clinic navigation">
                    <span class="ac-brand" title="Clinic Calendar">${icon("calendar")}</span>
                    <button class="ac-rail-link active" data-action="calendar" aria-label="Appointment calendar" title="Appointment calendar" aria-current="page">${icon("calendar")}</button>
                    <button class="ac-rail-link" data-action="encounters" aria-label="Patient Encounter" title="Patient Encounter">${clinicIcon("encounters")}</button>
                    <button class="ac-rail-link" data-action="doctors" aria-label="Doctors" title="Doctors">${clinicIcon("doctors")}</button>
                <button class="ac-rail-link" data-action="opd" aria-label="OPD tokens and rooms" title="OPD tokens and rooms">OPD</button>
                </nav>
                <main class="ac-main">
                    <header class="ac-topbar"><div><h1>Appointment Calendar</h1><span class="ac-subtitle">Bookings, approvals and patient queue</span></div>
                        <div class="ac-top-actions"><input class="ac-search" type="search" aria-label="Search patient, mobile number, doctor or booking" placeholder="Patient, mobile, doctor or booking...">
                        ${this.canCreate ? this.button("new", "New appointment", "ac-primary") : ""}
                        ${this.button("refresh", icon("es-line-reload"), "ac-icon-btn", 'aria-label="Refresh appointments" title="Refresh"')}</div>
                    </header>
                    <div class="ac-toolbar"><div class="ac-toolbar-title">Calendar <span class="ac-timezone"></span></div>
                        <div class="ac-date-controls">${this.button("prev", icon("es-line-left-chevron"), "ac-icon-btn", 'aria-label="Previous period"')}
                        <strong class="ac-period"></strong>${this.button("next", icon("es-line-right-chevron"), "ac-icon-btn", 'aria-label="Next period"')}
                        <input type="date" class="ac-calendar-date ac-queue-date" value="${this.date.format("YYYY-MM-DD")}" aria-label="Calendar date" title="Go to any date, including past appointments">
                        ${this.button("today", "Today")}</div>
                        <div class="ac-views" role="group" aria-label="Calendar view">${["Day", "Week", "Month", "Range"].map(v => this.button("view", v, v === "Week" ? "selected" : "", `data-view="${v}"`)).join("")}</div>
                    </div>
                    <div class="ac-range-controls" hidden>
                        <label>From <input type="date" class="ac-range-from ac-queue-date" aria-label="Range start date"></label>
                        <label>To <input type="date" class="ac-range-to ac-queue-date" aria-label="Range end date"></label>
                        ${this.button("apply-range", "Apply range", "ac-primary")}
                        <span class="ac-range-help">Up to 62 days, including both dates</span>
                        <span class="ac-range-error" role="alert"></span>
                    </div>
                    <div class="ac-notice" role="status" aria-live="polite" hidden></div>
                    <div class="ac-body"><aside class="ac-doctors"><h2>DOCTORS</h2><p class="ac-sidebar-note">Displayed appointments<br><small>Awaiting = not yet checked in</small></p><div class="ac-doctor-list"></div>
                        <div class="ac-legend"><h2>APPOINTMENT STATUS</h2>${["Pending", "Approved", "Checked In", "Cancelled"].map(s => `<div>${statusMark(s)}${s}</div>`).join("")}</div>
                        <p class="ac-sidebar-note">Select a doctor to filter the calendar. Select a booking to review it.</p>
                    </aside><section class="ac-calendar-area" aria-label="Appointment calendar">
                        <div class="ac-calendar-tools"><div class="ac-zoom" role="group" aria-label="Calendar time scale">
                            <span>Time scale</span>
                            ${this.button("zoom-out", "&minus;", "ac-icon-btn", 'aria-label="Zoom out calendar" title="Zoom out: see more hours"')}
                            ${this.button("zoom-reset", `${this.zoom}%`, "ac-zoom-value", 'aria-label="Reset calendar zoom to 100 percent" title="Reset to 100%"')}
                            ${this.button("zoom-in", "+", "ac-icon-btn", 'aria-label="Zoom in calendar" title="Zoom in: more appointment detail"')}
                        </div><span class="ac-zoom-hint">Ctrl + scroll to zoom</span></div>
                        <div class="ac-calendar"></div></section>
                    <aside class="ac-panel" aria-label="Appointment details and patient queue"><div class="ac-queue-view"></div><div class="ac-detail-view" hidden></div></aside></div>
                    <footer class="ac-footer"><span class="ac-update">Loading appointments...</span><span>Calendar times follow the clinic timezone &middot; Checks for new appointments every 15 seconds</span></footer>
                </main></div>`).appendTo(this.page.main.empty());
            this.$cal = this.$root.find(".ac-calendar");
            this.$directory = $('<section class="dd-view" hidden aria-label="Doctors and availability"></section>').appendTo(this.$root.find(".ac-main"));
            this.directory = new mobile_app.DoctorDirectory(this, this.$directory);
            this.$opd = $('<section class="ac-opd-view" hidden aria-label="OPD tokens and rooms"></section>').appendTo(this.$root.find(".ac-main"));
            this.opd = new mobile_app.OPDPortal(this.$opd[0], false, this);
            this.$cal[0].style.setProperty("--ac-slot-height", `${30 * this.zoom / 100}px`);
        }
        bind() {
            // Frappe emits a wrapper hide event; it does not call on_page_hide.
            $(this.wrapper).on("hide.appointment-calendar", () => this.hide());
            this.$root[0].addEventListener("scroll", event => {
                if (event.target.matches?.(".fc-time-grid-container") && activeRoute() &&
                    !this.drawing && $(this.wrapper).is(":visible")) {
                    this.calendarScroll = event.target.scrollTop;
                }
            }, true);
            this.$cal[0].addEventListener("wheel", event => {
                const scroller = event.target.closest?.(".fc-time-grid-container");
                if (!event.ctrlKey || !scroller || this.viewName === "month") return;
                event.preventDefault();
                if (!event.deltaY) return;
                // A trackpad gesture emits many small events. Limit zoom changes to discrete steps.
                const now = performance.now();
                if (now - (this.lastZoomWheel || 0) < 120) return;
                this.lastZoomWheel = now;
                this.changeZoom(event.deltaY < 0 ? 1 : -1, event.clientY - scroller.getBoundingClientRect().top);
            }, {passive: false});
            this.$root.on("click", "[data-action]", e => this.action($(e.currentTarget).data("action"), $(e.currentTarget)));
            this.$root.on("click", "[data-doctor]", e => {this.doctor = $(e.currentTarget).attr("data-doctor"); this.refilter();});
            this.$root.on("click", "[data-channel]", e => {this.channel = $(e.currentTarget).attr("data-channel"); this.refilter();});
            this.$root.on("click", "[data-queue]", e => {this.queue = $(e.currentTarget).attr("data-queue"); this.refilter();});
            this.$root.on("click", "[data-booking]", e => this.open(this.rows.find(r => r.id === $(e.currentTarget).attr("data-booking"))));
            this.$root.find(".ac-search").on("input", e => {this.search = e.target.value.trim().toLowerCase(); this.refilter();});
            this.$root.on("change", ".ac-calendar-date", e => {
                if (!moment(e.target.value, "YYYY-MM-DD", true).isValid()) return;
                this.initialLookup = false;
                this.date = moment(e.target.value); this.close(); this.$cal.fullCalendar("gotoDate", this.date); this.renderQueue();
            });
            this.resize = () => {
                if (!activeRoute()) return;
                this.$cal.fullCalendar("option", "height", this.height());
                if (window.innerWidth < 800 && this.$cal.fullCalendar("getView").name === "agendaWeek") {
                    this.$cal.fullCalendar("changeView", "agendaDay", this.date.clone());
                    this.$root.find(".ac-views .ac-btn").removeClass("selected");
                    this.$root.find('[data-view="Day"]').addClass("selected");
                }
            };
            window.addEventListener("resize", this.resize);
        }
        validDateRange(from, to) {
            const start = moment(from, "YYYY-MM-DD", true), end = moment(to, "YYYY-MM-DD", true);
            return typeof from === "string" && typeof to === "string" && start.isValid() && end.isValid() && end.diff(start, "days") >= 0 && end.diff(start, "days") < 62;
        }
        rangeGrid() {
            const r = this.customRange;
            return r ? {start: moment(r.from).startOf("isoWeek"), end: moment(r.to).startOf("isoWeek").add(1, "week")} : null;
        }
        applyRange(from, to) {
            if (!this.validDateRange(from, to)) {
                this.$root.find(".ac-range-error").text("Choose a valid range of 1 to 62 days; To must be on or after From.");
                return;
            }
            this.customRange = {from, to}; this.date = moment(from); this.initialLookup = false;
            this.$root.find(".ac-range-error").text("");
            this.$root.find(".ac-range-from").val(from); this.$root.find(".ac-range-to").val(to);
            this.close();
            if (this.viewName === "appointmentRange") this.$cal.fullCalendar("option", "visibleRange", this.rangeGrid());
            else this.$cal.fullCalendar("changeView", "appointmentRange", this.rangeGrid());
            this.$cal.fullCalendar("option", "height", this.height());
        }
        height() { return Math.max(380, window.innerHeight - (this.viewName === "appointmentRange" ? 308 : 258)); }
        renderZoom() {
            const month = ["month", "appointmentRange"].includes(this.viewName);
            this.$root.find('[data-action="zoom-out"]').prop("disabled", month || this.zoom === zoomLevels[0]);
            this.$root.find('[data-action="zoom-in"]').prop("disabled", month || this.zoom === zoomLevels[zoomLevels.length - 1]);
            this.$root.find('[data-action="zoom-reset"]').text(`${this.zoom}%`).prop("disabled", month);
            this.$root.find(".ac-zoom-hint").text(month ? "Zoom available in Day / Week" : "Ctrl + scroll to zoom");
        }
        changeZoom(step, pointerOffset) {
            if (this.viewName === "month") return;
            const index = zoomLevels.indexOf(this.zoom);
            const next = step === 0 ? 100 : zoomLevels[Math.max(0, Math.min(zoomLevels.length - 1, index + step))];
            if (next === this.zoom) return;
            const $scroll = this.$cal.find(".fc-time-grid-container");
            const oldHeight = this.$cal.find(".fc-slats tr")[0]?.getBoundingClientRect().height;
            if (!$scroll.length || !oldHeight) return;
            const offset = pointerOffset == null ? $scroll.innerHeight() / 2 : Math.max(0, Math.min($scroll.innerHeight(), pointerOffset));
            const anchor = ($scroll.scrollTop() + offset) / oldHeight;
            this.zoom = next;
            this.drawing = true;
            this.$cal[0].style.setProperty("--ac-slot-height", `${30 * this.zoom / 100}px`);
            // FullCalendar v3 must rebuild slot coordinates before positioning appointment blocks.
            this.$cal.fullCalendar("render");
            this.$cal.fullCalendar("rerenderEvents");
            const newHeight = this.$cal.find(".fc-slats tr")[0]?.getBoundingClientRect().height;
            $scroll.scrollTop(anchor * newHeight - offset);
            this.calendarScroll = $scroll.scrollTop();
            this.drawing = false;
            this.renderZoom();
            this.rememberView();
        }
        initCalendar() {
            this.$cal.fullCalendar({
                header: false, defaultView: window.innerWidth < 800 && this.viewName === "agendaWeek" ? "agendaDay" : this.viewName, defaultDate: this.date, firstDay: 1,
                views: {appointmentRange: {type: "basic", columnFormat: "ddd"}},
                visibleRange: this.rangeGrid(),
                dayRender: (date, cell) => {
                    if (this.customRange && (date.format("YYYY-MM-DD") < this.customRange.from || date.format("YYYY-MM-DD") > this.customRange.to)) cell.addClass("ac-outside-range");
                },
                height: this.height(), allDaySlot: false, nowIndicator: true, editable: false,
                slotDuration: "00:15:00", slotLabelInterval: "00:30:00", scrollTime: "08:00:00",
                timeFormat: "h:mm A", columnFormat: "ddd D MMM", displayEventEnd: false,
                eventLimit: true, eventTextColor: "#fff", timezone: false,
                lazyFetching: false,
                viewRender: view => {
                    this.viewName = view.name;
                    this.renderZoom();
                    const custom = view.name === "appointmentRange" && this.customRange;
                    this.$root.toggleClass("ac-range-active", Boolean(custom));
                    this.$root.find(".ac-range-controls").prop("hidden", !custom);
                    this.$root.find(".ac-calendar-date").prop("hidden", Boolean(custom));
                    if (custom) {
                        this.$root.find(".ac-range-from").val(custom.from); this.$root.find(".ac-range-to").val(custom.to);
                    }
                    this.$root.find(".ac-period").text(custom ? `${moment(custom.from).format("D MMM YYYY")} - ${moment(custom.to).format("D MMM YYYY")}` : view.title);
                    if (this.date.isBefore(view.start) || !this.date.isBefore(view.end)) this.date = this.$cal.fullCalendar("getDate").clone();
                    const viewKey = `${view.name}:${custom ? custom.from : view.start.format("YYYY-MM-DD")}:${custom ? custom.to : view.end.format("YYYY-MM-DD")}`;
                    if (this.viewKey !== viewKey) {
                        this.viewKey = viewKey;
                        this.scrolled = false; this.calendarScroll = null; this.close();
                    }
                    const label = {agendaDay:"Day", agendaWeek:"Week", month:"Month", appointmentRange:"Range"}[view.name];
                    this.$root.find('[data-action="prev"]').attr({title: `Previous ${label.toLowerCase()}`, "aria-label": `Previous ${label.toLowerCase()}`});
                    this.$root.find('[data-action="next"]').attr({title: `Next ${label.toLowerCase()}`, "aria-label": `Next ${label.toLowerCase()}`});
                    this.$root.find(".ac-views .ac-btn").removeClass("selected").filter(`[data-view="${label}"]`).addClass("selected");
                    this.range = custom ? {start: moment(custom.from), end: moment(custom.to).add(1, "day")} : {start: view.start.clone(), end: view.end.clone()};
                    this.renderDoctors(); this.renderQueue();
                    this.rememberView();
                    this.fetch();
                },
                dayClick: date => {if (this.viewName === "appointmentRange" && (date.format("YYYY-MM-DD") < this.customRange.from || date.format("YYYY-MM-DD") > this.customRange.to)) return; this.initialLookup = false; this.date = date.clone(); this.renderQueue(); this.close();},
                eventClick: event => {this.date = moment(event.record.date); this.open(event.record);},
                eventRender: (event, element) => {
                    if (!this.matches(event.record)) return false;
                    element.attr({title: `${event.record.patient_name} | ${event.record.status}`, tabindex: 0, role: "button", "aria-label": `${event.record.patient_name}, ${event.record.status}, ${event.start.format("D MMM h:mm A")}`});
                    element.on("keydown", e => {if (e.key === "Enter" || e.key === " ") {e.preventDefault(); this.open(event.record);}});
                    element.addClass(`ac-event-${statusClass(event.record.status)}`);
                    element.find(".fc-title").text(event.record.patient_name);
                    element.find(".fc-content").append(`<span class="ac-event-status" title="${esc(event.record.status)}" data-status="${esc(event.record.status)}">${statusMark(event.record.status)}</span>`);
                },
            });
        }
        dateSections(start, end) {
            const sections = [];
            for (let date = start.clone(); date.isBefore(end); date.add(7, "days")) {
                const stop = moment.min(date.clone().add(7, "days"), end);
                sections.push({start: date.format("YYYY-MM-DD"), end: stop.format("YYYY-MM-DD")});
            }
            return sections;
        }
        sectionKey(section) { return `${section.start}:${section.end}`; }
        async calendarRead(method, data) {
            this.calendarXHR = $.ajax({
                url: `/api/method/${API}.${method}`, type: "POST", dataType: "json",
                headers: {"X-Frappe-CSRF-Token": frappe.csrf_token}, timeout: 45000, data,
            });
            return (await this.calendarXHR)?.message;
        }
        async loadCalendarRange(start, end) {
            const data = await this.calendarRead("get_calendar", {start, end});
            if (!Array.isArray(data?.appointments) || !Array.isArray(data?.doctors) ||
                !Array.isArray(data?.revisions) || !data.revisions[0]?.revision) {
                throw new Error("The calendar server returned an invalid response.");
            }
            return data;
        }
        syncEvents() {
            // Reconcile events in place: a background check must not blank the calendar.
            const desired = new Map(this.events().map(event => [event.id, event]));
            for (const existing of this.$cal.fullCalendar("clientEvents")) {
                const next = desired.get(existing.id);
                if (!next) this.$cal.fullCalendar("removeEvents", existing.id);
                else {
                    if (JSON.stringify(existing.record) !== JSON.stringify(next.record)) {
                        Object.assign(existing, next);
                        this.$cal.fullCalendar("updateEvent", existing);
                    }
                    desired.delete(existing.id);
                }
            }
            if (desired.size) this.$cal.fullCalendar("renderEvents", [...desired.values()], true);
        }
        applySection(section, data, serial) {
            const incoming = new Set(data.appointments.map(row => row.id));
            this.rows = this.rows.filter(row => !incoming.has(row.id) && (row.date < section.start || row.date >= section.end))
                .concat(data.appointments);
            this.doctors = data.doctors; this.canAssign = data.can_assign;
            this.hasLoadedData = true;
            if (this.selected && this.selected.date >= section.start && this.selected.date < section.end &&
                !data.appointments.some(row => row.id === this.selected.id)) this.close();
            this.receptionOnly = Boolean(data.reception_only);
            this.$root.find('[data-action="new"], [data-action="encounters"]').prop("hidden", this.receptionOnly);
            this.$root.find(".ac-main > .ac-topbar .ac-subtitle").text(this.receptionOnly ? "Approved appointments and completed check-ins" : "Bookings, approvals and patient queue");
            if (this.receptionOnly && !["All", "Approved", "Checked In"].includes(this.queue)) this.queue = "All";
            this.$root.find(".ac-timezone").text(data.timezone);
            this.$root.find(".ac-notice").prop("hidden", true);
            this.renderDoctors(); this.renderQueue();
            const previousScroll = this.calendarScroll ?? this.$cal.find(".fc-time-grid-container").scrollTop();
            this.drawing = true;
            this.syncEvents();
            const initialScroll = !this.scrolled;
            this.scrolled = true;
            setTimeout(() => {
                if (serial !== this.request || !activeRoute()) return;
                const rowHeight = this.$cal.find(".fc-slats tr")[0]?.getBoundingClientRect().height || 30;
                let scrollTop = previousScroll || 0;
                if (initialScroll) {
                    const visible = this.filtered();
                    const day = visible.filter(r => r.date === this.date.format("YYYY-MM-DD"));
                    const times = (day.length ? day : visible).map(r => r.time).sort();
                    const first = times.length ? moment(times[0], "HH:mm:ss") : null;
                    const minutes = first ? Math.max(0, first.hours() * 60 + first.minutes() - 30) : 480;
                    scrollTop = rowHeight * Math.floor(minutes / 15);
                }
                this.$cal.find(".fc-time-grid-container").scrollTop(scrollTop);
                this.calendarScroll = this.$cal.find(".fc-time-grid-container").scrollTop() ?? this.calendarScroll;
                this.drawing = false;
            }, 50);

        }
        async fetch(force = false, background = false) {
            if (["doctors", "opd"].includes(this.section)) return;
            if (!this.range || !activeRoute() || !$(this.wrapper).is(":visible")) return;
            const {start, end} = this.range;
            const rangeKey = `${start.format("YYYY-MM-DD")}:${end.format("YYYY-MM-DD")}`;
            if (!force && this.loadingRange === rangeKey) return;
            const serial = ++this.request;
            this.calendarXHR?.abort();
            this.loadingRange = rangeKey; this.loading = true; this.loadError = false;
            this.revisions ||= new Map();
            const sameRange = this.loadedRange === rangeKey;
            if (!sameRange) {
                this.revisions.clear();
                this.rows = this.rows.filter(row => row.date >= start.format("YYYY-MM-DD") && row.date < end.format("YYYY-MM-DD"));
                this.loadedRange = rangeKey;
                this.hasLoadedData = false;
                this.syncEvents(); this.renderDoctors();
            }
            // Keep the chosen period. Do not issue a hidden 62-day nearest-booking scan.
            this.initialLookup = false; this.rememberView();
            this.renderQueue();
            this.$root.find('[data-action="refresh"]').prop("disabled", true).attr("aria-busy", "true");
            this.$root.find(".ac-update").text(background && sameRange ? "Checking for new appointments..." : "Loading appointments...");
            this.$root.find(".ac-notice").prop("hidden", true);
            let completed = 0;
            try {
                let sections = this.dateSections(start, end);
                if (background && sameRange && this.revisions.size) {
                    const changes = await this.calendarRead("get_calendar_changes", {
                        start: start.format("YYYY-MM-DD"), end: end.format("YYYY-MM-DD"),
                    });
                    if (serial !== this.request || !activeRoute()) return;
                    if (!Array.isArray(changes?.chunks) || changes.chunks.length !== sections.length ||
                        changes.chunks.some((part, i) => this.sectionKey(part) !== this.sectionKey(sections[i]) || !part.revision)) {
                        throw new Error("Invalid calendar change response.");
                    }
                    sections = changes.chunks.filter(section => this.revisions.get(this.sectionKey(section)) !== section.revision);
                }
                // Load the selected date first, then progressively fill the remaining dates.
                const focus = this.date.format("YYYY-MM-DD");
                sections.sort((a, b) => Number(b.start <= focus && focus < b.end) - Number(a.start <= focus && focus < a.end));
                for (const section of sections) {
                    const data = await this.loadCalendarRange(section.start, section.end);
                    if (serial !== this.request || !activeRoute() || !$(this.wrapper).is(":visible")) return;
                    this.applySection(section, data, serial);
                    this.revisions.set(this.sectionKey(section), data.revisions[0].revision);
                    completed++;
                    this.$root.find(".ac-update").text(`${this.rows.length} bookings loaded | Loading dates ${completed}/${sections.length}...`);
                }
                this.loading = false; this.hasLoadedData = true;
                this.renderQueue();
                this.$root.find(".ac-update").text(`${this.rows.length} bookings in this period | Updated ${moment().format("h:mm A")}`);
                if (completed && this.selected && !this.mutating) this.open(this.selected, true);
            } catch (e) {
                if (serial !== this.request) return;
                this.drawing = false; this.loading = false; this.loadError = true;
                const failure = e?.responseJSON?.exc_type === "QueryTimeoutError" ? "The database stopped a slow appointment query." :
                    e?.statusText === "timeout" ? "The calendar server did not respond within 45 seconds." :
                    [401, 403].includes(e?.status) ? "Your session or appointment permissions do not allow this request." :
                    e?.status >= 500 ? `The calendar server returned an error (HTTP ${e.status}).` :
                    "The calendar request failed. Check the connection or server logs.";
                // Slow background work is quiet; a real failure is still visible and retryable.
                this.$root.find(".ac-notice").text(`${failure} Loaded appointments remain visible. Use Refresh to retry.`).prop("hidden", false);
                this.$root.find(".ac-update").text(this.hasLoadedData ? "Refresh incomplete - showing loaded appointments" : "Appointments could not be loaded");
                this.renderQueue();
            } finally {
                if (serial === this.request) {
                    this.loadingRange = null; this.calendarXHR = null;
                    this.$root.find('[data-action="refresh"]').prop("disabled", false).attr("aria-busy", "false");
                    mobile_app.realtime.flush();
                }
            }
        }
        color(id) {
            if (!this.colors[id]) {
                let hash = 0; for (const c of id) hash = ((hash << 5) - hash + c.charCodeAt(0)) | 0;
                this.colors[id] = colors[Math.abs(hash) % colors.length];
            }
            return this.colors[id];
        }
        matches(r, includeQueue = true, includeDoctor = true) {
            const stages = {Pending:"Pending", Approved:"Approved", Cancelled:"Cancelled", "Checked In":"Checked In"};
            return (!this.range || (r.date >= this.range.start.format("YYYY-MM-DD") && r.date < this.range.end.format("YYYY-MM-DD"))) &&
                (!includeDoctor || !this.doctor || r.doctor_id === this.doctor) &&
                (!includeQueue || this.queue === "All" || r.status === stages[this.queue]) &&
                (this.channel === "All" || (this.channel === "Online") === r.online) &&
                this.matchesSearch(r);
        }
        matchesSearch(r) {
            if (!this.search) return true;
            const text = [r.patient_name, r.patient, r.phone, r.doctor_name, r.doctor_id,
                r.name, r.booking_id, r.encounter, r.email, r.assigned_agent].filter(Boolean).join(" ").toLowerCase();
            const query = this.search.replace(/\s+/g, " ").trim();
            if (text.replace(/\s+/g, " ").includes(query)) return true;
            // Compare phone digits independently of spaces, punctuation or country prefix.
            if (!/^[+\d\s().-]+$/.test(query)) return false;
            const digits = query.replace(/\D/g, "");
            const phone = String(r.phone || "").replace(/\D/g, "");
            return digits.length >= 3 && (phone.includes(digits) ||
                (phone.length >= 10 && digits.endsWith(phone)));
        }
        filtered() { return this.rows.filter(r => this.matches(r)); }
        events() {
            return this.rows.map(r => {
                const start = moment(`${r.date} ${r.time}`, "YYYY-MM-DD HH:mm:ss");
                return {id: r.id, title: r.patient_name, start, end: start.clone().add(r.duration, "minutes"),
                    color: this.color(r.doctor_id), record: r};
            });
        }
        refilter() {
            this.close(); this.renderDoctors(); this.renderQueue();
            this.$cal.fullCalendar("rerenderEvents");
        }
        renderDoctors() {
            const groups = new Map(this.doctors.map(d => [d.id, {name: d.name, count: 0, awaiting: 0}]));
            this.rows.forEach(r => {
                if (!groups.has(r.doctor_id)) groups.set(r.doctor_id, {name: r.doctor_name, count: 0, awaiting: 0});
            });
            if (this.doctor && !groups.has(this.doctor)) this.doctor = "";
            // Sidebar counts cover the period, channel and status, regardless of selected doctor.
            this.rows.filter(r => this.matches(r, true, false)).forEach(r => {
                const d = groups.get(r.doctor_id);
                d.count++;
                if (["Pending", "Approved"].includes(r.status)) d.awaiting++;
            });
            const total = [...groups.values()].reduce((sum, d) => sum + d.count, 0);
            const awaiting = [...groups.values()].reduce((sum, d) => sum + d.awaiting, 0);
            const counts = (count, waiting) => `<span class="ac-doctor-counts" title="${count} displayed appointments; ${waiting} awaiting check-in"><b class="ac-doctor-total">${count}</b><small><span class="ac-doctor-awaiting">${waiting}</span> awaiting</small></span>`;
            this.$root.find(".ac-doctor-list").html(`<button class="ac-doctor ${!this.doctor ? "selected" : ""}" data-doctor=""><span>All doctors</span>${counts(total, awaiting)}</button>` +
                [...groups].sort((a,b) => a[1].name.localeCompare(b[1].name)).map(([id,d]) => `<button class="ac-doctor ${this.doctor === id ? "selected" : ""}" data-doctor="${esc(id)}" title="${esc(d.name)}"><span class="ac-doctor-name"><i style="background:${this.color(id)}"></i>${esc(d.name)}</span>${counts(d.count, d.awaiting)}</button>`).join(""));
        }
        renderQueue() {
            const day = this.date.format("YYYY-MM-DD");
            this.$root.find(".ac-calendar-date").val(day);
            this.rememberView();
            const rows = this.rows.filter(r => this.matches(r, false)).sort((a,b) => a.date.localeCompare(b.date) || a.time.localeCompare(b.time));
            const statuses = {All: rows.length, Pending: rows.filter(r => r.status === "Pending").length,
                Approved: rows.filter(r => r.status === "Approved").length,
                "Checked In": rows.filter(r => r.status === "Checked In").length,
                Cancelled: rows.filter(r => r.status === "Cancelled").length};
            if (this.receptionOnly) { delete statuses.Pending; delete statuses.Cancelled; }
            const map = {Pending:"Pending", Approved:"Approved", "Checked In":"Checked In", Cancelled:"Cancelled"};
            const visible = rows.filter(r => this.queue === "All" || r.status === map[this.queue]);
            this.$root.find(".ac-queue-view").html(`<div class="ac-counts">${Object.entries(statuses).map(([s,n]) => `<button data-queue="${s}" class="${this.queue === s ? "selected" : ""}"><span>${s}</span><b class="ac-count-${statusClass(s)}">${n}</b></button>`).join("")}</div>
                <div class="ac-queue-list">${visible.length ? visible.map(r => `<button class="ac-booking" data-booking="${esc(r.id)}" style="border-left-color:${this.color(r.doctor_id)}"><time><span class="ac-booking-date">${moment(r.date).format("D MMM")}</span>${moment(r.time,"HH:mm:ss").format("h:mm A")}</time><div><strong>${esc(r.patient_name)}</strong><span>${esc(r.doctor_name)}</span><span class="ac-badge ${statusClass(r.status)}">${statusMark(r.status)}${esc(r.status)}</span><small>${r.online ? "Online" : "In clinic"}</small></div>${icon("es-line-right-chevron")}</button>`).join("") : `<div class="ac-empty"><div>${icon("calendar")}</div><strong>${this.loading ? "Loading appointments..." : this.loadError ? "Appointments unavailable" : "No appointments here"}</strong><p>${this.loading ? "Waiting for the calendar server." : this.loadError ? "Use Refresh to retry. Your appointments have not been removed." : "Choose another date or clear your filters. New bookings appear automatically."}</p></div>`}</div>`);
        }
        async open(record, quiet = false) {
            if (!record) return;
            this.date = moment(record.date);
            this.initialLookup = false;
            this.rememberView();
            this.$root.find(".ac-calendar-date").val(this.date.format("YYYY-MM-DD"));
            this.selected = record;
            const serial = ++this.detailRequest;
            if (!quiet) {
                this.$root.find(".ac-queue-view").prop("hidden", true);
                this.$root.find(".ac-detail-view").prop("hidden", false).html('<div class="ac-empty">Loading appointment...</div>');
            }
            try {
                const data = await frappe.xcall(`${API}.get_appointment`, {doctype: record.source_doctype, name: record.name});
                if (serial !== this.detailRequest || !this.selected) return;
                this.selected = data; this.renderDetail(data);
            } catch(e) {if (serial === this.detailRequest) {this.close(); frappe.show_alert({message: "Appointment unavailable. Refresh the calendar.", indicator: "orange"});}}
        }
        renderDetail(r) {
            const field = (label, value) => value ? `<div class="ac-detail-field"><dt>${label}</dt><dd>${esc(value)}</dd></div>` : "";
            const stamp = value => value ? moment(value).format("D MMM, h:mm A") : "";
            // Keep historical consultation audit in the database, not the simplified appointment panel.
            const history = (r.history || []).filter(h => !/Checked In \u2192 In Consultation|In Consultation \u2192 Completed/.test($('<div>').html(h.content).text()));
            const meet = /^https:\/\/meet\.google\.com\//i.test(r.meet_link || "") ? `<a class="ac-btn" target="_blank" rel="noopener noreferrer" href="${esc(r.meet_link)}">Join video consultation</a>` : "";
            this.$root.find(".ac-detail-view").html(`<div class="ac-detail-top">${this.button("close", `${icon("es-line-left-chevron")} Appointments`)}<span class="ac-badge ${statusClass(r.status)}">${statusMark(r.status)}${esc(r.status)}</span></div>
                <div class="ac-patient-heading"><span class="ac-eyebrow">APPOINTMENT DETAILS</span><h2>${esc(r.patient_name)}</h2><p>${esc(moment(r.date).format("dddd, D MMMM"))} &middot; ${esc(moment(r.time,"HH:mm:ss").format("h:mm A"))}</p></div>
                <div class="ac-detail-scroll"><dl>${field("OPD token",r.opd_visit ? String(r.opd_visit.token_number).padStart(3,"0") : "")}${field("Doctor",r.doctor_name)}${field("Appointment mode",r.online ? "Online" : "In clinic")}${field("Phone",r.phone)}${field("Email",r.email)}${field("Patient",r.patient)}${field("Booking",r.name)}${field("Assigned agent",r.assigned_agent || "Unassigned")}${field("Encounter",r.encounter)}${field("Notes",r.notes)}${field("Decision reason",r.reason)}${field("Decision by",r.decision_by)}${field("Decision time",stamp(r.decision_at))}${field("Checked in",stamp(r.checked_in_at))}</dl>
                ${history.length ? `<details class="ac-history"><summary>Activity history</summary>${history.map(h => `<div><small>${esc(stamp(h.creation))} | ${esc(h.comment_by)}</small><p>${esc($('<div>').html(h.content).text())}</p></div>`).join("")}</details>` : ""}</div>
                <div class="ac-detail-actions">${r.actions.map(a => this.button(a,labels[a],a === "cancel" ? "ac-danger" : "ac-primary")).join("")}${this.canAssign && !["Checked In","Cancelled"].includes(r.status) ? this.button("assign","Assign agent") : ""}${meet}${r.can_open_source ? this.button("source",r.encounter ? "Open Patient Encounter" : "Open booking record") : ""}${!r.actions.length && !["Checked In","Cancelled"].includes(r.status) ? '<p class="ac-muted">Actions are available to the responsible agent or assigned doctor.</p>' : ""}</div>`);
        }
        close() {this.selected = null; ++this.detailRequest; this.$root.find(".ac-detail-view").prop("hidden",true);this.$root.find(".ac-queue-view").prop("hidden",false);this.renderQueue();}
        action(action, button) {
            if (action === "opd") return this.directory.leaveDraft(() => this.switchSection("opd"));
            if (action === "encounters" && this.section === "opd" && this.opd.dirty) return frappe.confirm("Discard unsaved OPD setup changes?", () => {this.opd.dirty = false; this.action(action, button);});
            if (action === "check_in" && this.selected && !this.selected.online) return mobile_app.opd_checkin(this.selected, () => { this.close(); this.fetch(true); });
            if (action === "apply-range") return this.applyRange(this.$root.find(".ac-range-from").val(), this.$root.find(".ac-range-to").val());
            if (action === "view" && button.data("view") === "Range") {
                const from = this.customRange?.from || this.range.start.format("YYYY-MM-DD");
                const to = this.customRange?.to || this.range.end.clone().subtract(1, "day").format("YYYY-MM-DD");
                return this.applyRange(from, to);
            }
            if (["prev", "next"].includes(action) && this.viewName === "appointmentRange") {
                const days = (moment(this.customRange.to).diff(moment(this.customRange.from), "days") + 1) * (action === "next" ? 1 : -1);
                return this.applyRange(moment(this.customRange.from).add(days, "days").format("YYYY-MM-DD"), moment(this.customRange.to).add(days, "days").format("YYYY-MM-DD"));
            }
            if (action === "today" && this.viewName === "appointmentRange") {
                this.customRange = null; this.initialLookup = false; this.date = moment(frappe.datetime.get_today());
                this.$cal.fullCalendar("changeView", "agendaWeek", this.date.clone());
                this.$cal.fullCalendar("option", "height", this.height()); return;
            }
            if (action === "zoom-in") return this.changeZoom(1);
            if (action === "zoom-out") return this.changeZoom(-1);
            if (action === "zoom-reset") return this.changeZoom(0);
            if (["prev", "next", "today", "view"].includes(action)) this.initialLookup = false;
            if (["prev","next","today"].includes(action)) {
                if (action === "today") this.date = moment(frappe.datetime.get_today());
                this.close(); this.$cal.fullCalendar(action); this.renderQueue(); return;
            }
            if (action === "view") {this.customRange = null; const v = button.data("view");this.$root.find(".ac-views .ac-btn").removeClass("selected");button.addClass("selected");this.$cal.fullCalendar("changeView",{Day:"agendaDay",Week:"agendaWeek",Month:"month"}[v],this.date.clone());this.$cal.fullCalendar("option", "height", this.height());return;}
            if (action === "refresh") return this.fetch();
            if (action === "close") return this.close();
            if (action === "doctors") return this.switchSection("doctors");
            if (action === "calendar") return this.directory.leaveDraft(() => this.switchSection("calendar"));
            if (action === "encounters") return this.directory.leaveDraft(() => frappe.set_route("List","Patient Encounter"));
            if (action === "new") return frappe.new_doc("Patient Encounter",{sr_encounter_type:"Appointment",pe_appointment_date:this.date.format("YYYY-MM-DD")});
            if (action === "source") {
                this.calendarScroll = this.$cal.find(".fc-time-grid-container").scrollTop() ?? this.calendarScroll;
                return frappe.set_route("Form",this.selected.source_doctype,this.selected.name);
            }
            if (!this.selected || this.mutating) return;
            if (action === "cancel") return frappe.prompt([{fieldname:"reason",label:"Reason for cancellation",fieldtype:"Small Text",reqd:1}],values => this.update(action,values),"Cancel appointment","Cancel appointment");
            if (action === "assign") return frappe.prompt([{fieldname:"agent",label:"Responsible agent",fieldtype:"Link",options:"User",reqd:1,get_query:()=>({query:`${API}.agent_query`})}],values=>this.update(action,values),"Assign appointment","Assign");
            this.update(action);
        }
        async update(action, values = {}) {
            this.mutating = true;this.$root.find(".ac-detail-actions button").prop("disabled",true);
            try {
                const r = this.selected;
                const result = await frappe.xcall(`${API}.update_appointment`,{doctype:r.source_doctype,name:r.name,action,expected_status:r.status,...values});
                ++this.detailRequest;
                this.rows = this.rows.map(row => row.id === result.id ? result : row);
                this.selected = result;this.renderDetail(result);this.renderDoctors();this.renderQueue();this.syncEvents();frappe.show_alert({message:"Appointment updated",indicator:"green"});
            } catch (error) { /* Frappe presents the server validation message. */ } finally {this.mutating = false;this.$root.find(".ac-detail-actions button").prop("disabled",false);this.fetch(true);}
        }
        switchSection(section) {
            if (this.opdOnly && section !== "opd") return;
            if (this.section === 'opd' && this.opd.dirty && section !== 'opd') {
                frappe.confirm('Discard unsaved OPD setup changes?', () => {this.opd.dirty = false; this.switchSection(section);});
                return;
            }
            this.section = section;
            if (section !== "opd" && frappe.get_route()[1] === "room") frappe.set_route("doctor-clinical");
            const doctors = section === "doctors", opd = section === "opd";
            if (doctors || opd) {
                this.calendarXHR?.abort(); ++this.request;
                this.loading = false; this.loadingRange = null;
            }
            this.$root.find(".ac-main").children().not(this.$directory).not(this.$opd).toggleClass("dd-hidden", doctors || opd);
            this.$directory.prop("hidden", !doctors);
            this.$opd.prop("hidden", !opd);
            this.$root.find(".ac-rail-link").removeClass("active").removeAttr("aria-current")
                .filter(`[data-action="${section}"]`).addClass("active").attr("aria-current", "page");
            if (!opd) this.opd.hide();
            if (opd) this.opd.show();
            else if (doctors) this.directory.show();
            else {this.$cal.fullCalendar("render"); this.fetch(true);}
        }
        show() {
            const route=frappe.get_route();
            if(route[1]==='room' && route[2]){
                this.opd.selectedRoom=route[2];this.opd.section='room';
                if(this.section!=='opd')this.switchSection('opd');
            }
            document.body.classList.add("ma-calendar-active");
            clearTimeout(this.showTimer);
            this.showTimer = setTimeout(() => {
                if (!activeRoute()) return;
                if (this.section === "opd") {this.opd.show(); return;}
                if (this.section === "doctors") {this.directory.show(); return;}
                this.drawing = true;
                this.$cal.fullCalendar("render");
                if (this.calendarScroll != null) this.$cal.find(".fc-time-grid-container").scrollTop(this.calendarScroll);
                this.drawing = false;
                this.fetch();
            }, 50);
        }
        hide() {
            this.opd?.hide();
            clearTimeout(this.showTimer);
            ++this.request; ++this.detailRequest;
            this.calendarXHR?.abort();
            this.calendarXHR = null; this.loadingRange = null; this.loading = false;
            this.drawing = false;
            document.body.classList.remove("ma-calendar-active");
        }
    }
    frappe.pages["doctor-clinical"].on_page_load = wrapper => {
        frappe.require(["/assets/frappe/js/lib/fullcalendar/fullcalendar.min.css","/assets/frappe/js/lib/fullcalendar/fullcalendar.min.js","/assets/mobile_app/css/doctor_clinical.css","/assets/mobile_app/js/doctor_directory.js","/assets/mobile_app/css/opd_queue.css","/assets/mobile_app/js/opd_queue.js"],()=>{
            wrapper.appointment_calendar = new AppointmentCalendar(wrapper);
            if(activeRoute()) wrapper.appointment_calendar.show();
        });
    };
    frappe.pages["doctor-clinical"].on_page_show = wrapper => wrapper.appointment_calendar?.show();
    frappe.pages["doctor-clinical"].on_page_hide = wrapper => wrapper.appointment_calendar?.hide();
})();
