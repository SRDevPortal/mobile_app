/* Doctor details, recurring schedules and date-specific availability. */
frappe.provide("mobile_app");
(() => {
    const API = "mobile_app.api.doctor_directory";
    const esc = value => frappe.utils.escape_html(String(value ?? ""));
    const days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
    const time = value => String(value || "").split(":").map(v => v.padStart(2, "0")).join(":").slice(0, 5);

    mobile_app.DoctorDirectory = class {
        constructor(owner, root) {
            this.owner = owner; this.$root = root; this.date = owner.date.format("YYYY-MM-DD");
            this.serial = 0; this.doctors = []; this.mode = "weekly"; this.dirty = false;
            root.html(`<header class="ac-topbar"><div><h1>Doctors</h1><span class="ac-subtitle">Doctor details and availability</span></div>
                <button class="ac-btn" data-dd="refresh">Refresh doctors</button></header>
                <div class="dd-body"><aside class="dd-sidebar"><label for="dd-search">Find a doctor</label>
                    <input id="dd-search" class="ac-search" type="search" placeholder="Name, disease or phone"><div class="dd-list" aria-label="Doctors"></div></aside>
                <section class="dd-content" aria-label="Doctor details"><p class="dd-message" role="status">Select a doctor to view availability.</p>
                    <div class="dd-detail" hidden></div></section></div>`);
            root.on("input", "#dd-search", () => this.renderList());
            root.on("click", "[data-practitioner]", e => this.leaveDraft(() => {
                this.selected = $(e.currentTarget).attr("data-practitioner"); this.scheduleId = null; this.leaveStart = null; this.leaveEnd = null; this.load();
            }));
            root.on("click", "[data-dd]", e => this.action($(e.currentTarget).attr("data-dd")));
            root.on("change", ".dd-date", e => {
                const value = e.target.value;
                if (moment(value, "YYYY-MM-DD", true).isValid()) this.leaveDraft(() => {this.date = value; this.load();});
            });
            root.on("change", ".dd-leave-mode", e => {
                this.leaveRange = e.target.value === "range";
                this.$root.find(".dd-leave-end-label").prop("hidden", !this.leaveRange);
            });
            root.on("input change", ".dd-editor input, .dd-editor select", e => {
                this.dirty = true;
                if ($(e.target).closest(".dd-hours-builder").length) {
                    this.hoursChanged = true;
                    this.$root.find('.dd-schedule-state').text('Unsaved changes. Save working hours to update the calendar.');
                }
            });
        }
        leaveDraft(fn) {
            if (this.saving) return;
            if (this.dirty) frappe.confirm("Discard unsaved availability changes?", () => {this.dirty = false; fn();}, () => {
                this.$root.find(".dd-date").val(this.date);
            });
            else fn();
        }
        async show() { if (!this.saving && !this.dirty) await this.refresh(); }
        async refresh() {
            const serial = ++this.serial;
            this.$root.find(".dd-message").prop("hidden", false).text("Loading doctors...");
            try {
                const data = await frappe.xcall(`${API}.list_doctors`);
                if (serial !== this.serial) return;
                this.doctors = data.doctors;
                if (!this.doctors.some(d => d.id === this.selected)) this.selected = this.doctors[0]?.id;
                this.renderList();
                if (this.selected) await this.load();
                else {this.$root.find(".dd-detail").prop("hidden", true); this.$root.find(".dd-message").text("No doctors are available to your account.");}
            } catch (e) {if (serial === this.serial) this.$root.find(".dd-message").text("Could not load doctors. Check your connection and access, then refresh.");}
        }
        renderList() {
            const query = this.$root.find("#dd-search").val().trim().toLowerCase();
            const doctors = this.doctors.filter(d => [d.name, ...(d.diseases || []), d.phone].join(" ").toLowerCase().includes(query));
            this.$root.find(".dd-list").html(doctors.length ? doctors.map(d => `<button class="dd-person ${d.id === this.selected ? "selected" : ""}" data-practitioner="${esc(d.id)}" aria-pressed="${d.id === this.selected}">
                <span class="dd-avatar" aria-hidden="true">${esc(d.name.replace(/^Dr\.?\s*/i, "").split(" ").map(p => p[0]).slice(0, 2).join(""))}</span>
                <span><strong>${esc(d.name)}</strong><small>${esc((d.diseases || []).join(", ") || "No diseases selected")}</small><small>${esc(d.status)}</small></span></button>`).join("") : '<p class="dd-empty">No matching doctors.</p>');
        }
        async load() {
            if (!this.selected) return;
            const serial = ++this.serial;
            this.$root.find(".dd-message").prop("hidden", false).text("Loading doctor availability...");
            this.$root.find(".dd-detail").prop("hidden", true);
            try {
                const period = moment(this.date);
                const start = this.mode === "date" ? period.clone().startOf("month").startOf("isoWeek") : period.clone().startOf("isoWeek");
                const end = this.mode === "date" ? start.clone().add(41,"days") : period.clone().endOf("isoWeek");
                const data = await frappe.xcall(`${API}.get_doctor`, {practitioner_id: this.selected, date: this.date,
                    range_start: start.format("YYYY-MM-DD"), range_end: end.format("YYYY-MM-DD")});
                if (!Array.isArray(data.calendar_days)) throw new Error('Calendar response missing saved working hours');
                if (serial !== this.serial) return;
                this.data = data; this.dirty = false;
                this.renderList(); this.renderDetail();
                this.$root.find(".dd-message").prop("hidden", true);
            } catch (e) {if (serial === this.serial) this.$root.find(".dd-message").text("Could not load availability. Refresh to try again.");}
        }
        renderDetail() {
            const d = this.data.doctor;
            this.$root.find(".dd-calendar").fullCalendar("destroy");
            this.$root.find(".dd-detail").prop("hidden", false).html(`<div class="dd-profile"><div><h2>${esc(d.name)}</h2>
                <p>${esc([d.qualification, d.hospital].filter(Boolean).join(" · ") || "Healthcare practitioner")}</p>
                <p>Diseases: ${esc((d.diseases || []).join(", ") || "No diseases selected")}</p></div>
                <span class="dd-status">${esc(d.status)}</span><button class="ac-btn" data-dd="profile">${d.can_edit ? "Edit doctor details" : "View doctor details"}</button></div>
                <div class="dd-contact"><span>Phone: ${esc(d.phone || "Not provided")}</span><span>Email: ${esc(d.email || "Not provided")}</span></div>
                <div class="dd-mode" role="group" aria-label="Availability editing mode">
                    <button class="ac-btn dd-setting" data-dd="weekly"><strong>Working Schedule</strong><span>Set weekly days and working hours</span></button>
                    <button class="ac-btn dd-setting" data-dd="date"><strong>Leave / Holiday</strong><span>Mark a single day or a date range</span></button>
                </div>
                <div class="dd-editor"></div>
                <div class="dd-toolbar"><div><h2>${this.mode === "date" ? `Leave & availability · ${esc(moment(this.date).format("MMMM YYYY"))}` : "Working schedule calendar"}</h2><small>${esc(this.data.timezone)} · ${this.mode === "date" ? "Click a day or drag across dates to select leave" : "Saved weekly working hours"}</small></div>
                    <div class="dd-date-actions"><button class="ac-btn" data-dd="prev" aria-label="Previous ${this.mode === "date" ? "month" : "week"}">‹</button><input class="dd-date ac-queue-date" type="date" value="${this.date}" aria-label="Availability date">
                    <button class="ac-btn" data-dd="next" aria-label="Next ${this.mode === "date" ? "month" : "week"}">›</button><button class="ac-btn" data-dd="today">Today</button></div></div>
                ${d.status !== "Active" ? '<p class="dd-note">This doctor is inactive. Their time slots cannot be booked.</p>' : ""}
                <div class="dd-calendar"></div><div class="dd-key"><span>Available</span><span class="dd-leave-key">Leave / Holiday</span><span class="dd-off-key">Weekly off</span></div>
                ${!this.data.schedules.some(s => !s.disabled && s.slots.length) && !this.data.week_exceptions.some(e => e.slots.length) ? '<p class="dd-note">No working hours configured. Add a working schedule above.</p>' : ""}`);
            const events = this.data.calendar_days.map(day => ({
                title: day.status === 'working' ? 'Available' : day.status === 'leave' ? 'Leave / Holiday' : day.status === 'inactive' ? 'Inactive' : 'Weekly off',
                start: day.date, allDay: true,
                color: day.status === 'working' ? '#176b63' : ['leave', 'off'].includes(day.status) ? '#a33d50' : '#60767b'
            }));
            const choose = (value, last=value) => this.leaveDraft(() => {
                this.date = value.format("YYYY-MM-DD"); this.leaveStart = this.date; this.leaveEnd = last.format("YYYY-MM-DD");
                this.leaveRange = this.leaveStart !== this.leaveEnd; this.mode = "date"; this.load();
            });
            this.$root.find(".dd-calendar").fullCalendar({header: false, defaultView: this.mode === "date" ? "month" : "basicWeek", defaultDate: this.date, firstDay: 1,
                height: this.mode === "date" ? 520 : 230, editable: false, displayEventTime: false,
                selectable: this.mode === "date", select: (first,last)=>choose(first,last.clone().subtract(1,"day")),
                columnFormat: this.mode === "date" ? "ddd" : "ddd D MMM", timeFormat: "h:mm A", events,
                dayClick: this.mode === "date" ? undefined : value=>choose(value), eventClick: e => choose(e.start),
                dayRender: (date, cell) => {
                    const value=date.format("YYYY-MM-DD"), day=this.data.calendar_days.find(item=>item.date===value);
                    const event=events.find(item=>item.start===value);
                    if (day && event) {
                        cell.addClass(`dd-status-${day.status}`).attr({tabindex:0,role:'button',
                            'aria-label':`${event.title}, ${date.format('D MMM YYYY')}`});
                        $('<span class="dd-date-status" aria-hidden="true"></span>').text(event.title).appendTo(cell);
                        cell.on('keydown',e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();choose(date);}});
                    }
                    if (this.mode === "date" && this.leaveStart ? value>=this.leaveStart && value<=(this.leaveRange?this.leaveEnd:this.leaveStart) : value===this.date) cell.addClass("dd-selected-day");
                },
                // Status belongs to the whole date cell, not a slot/event bar.
                eventRender: () => false});
            // Month date numbers sit in a separate layer above the coloured cells.
            for (const day of this.data.calendar_days) {
                this.$root.find(`.dd-calendar .fc-day-top[data-date="${day.date}"]`).addClass(`dd-status-${day.status}`);
            }
            this.renderEditor();
        }
        renderEditor() {
            if (this.mode === "date") return this.renderLeaveEditor();
            const data = this.data;
            this.hoursChanged = false;
            if (!data.schedules.some(s => s.id === this.scheduleId)) {
                this.scheduleId = (data.schedules.find(s => !s.disabled) || data.schedules[0])?.id;
            }
            const schedule = data.schedules.find(s => s.id === this.scheduleId);
            const pattern = this.workingHours(schedule);
            const canEdit = schedule ? schedule.can_edit : data.can_create_schedule;
            this.draftSlots = schedule?.slots || [];
            this.draftDisabled = schedule?.disabled || 0;
            this.$root.find(".dd-mode .ac-btn").removeClass("ac-primary").filter('[data-dd="weekly"]').addClass("ac-primary");
            this.$root.find(".dd-editor").html(`<fieldset ${canEdit ? "" : "disabled"}>
                <div class="dd-hours-builder"><h4>Set working days and hours</h4>
                    <p class="dd-note dd-schedule-state">${pattern.custom ? 'Saved hours include breaks or vary by day, as shown in the calendar. Enter new hours below only to replace them.' : schedule?.slots.length ? 'Saved working hours are shown in the calendar.' : 'No working schedule saved yet. Choose your hours and save to update the calendar.'}</p>
                    <div class="dd-working-days" role="group" aria-label="Working days">${days.map(day => `<label><input type="checkbox" class="dd-working-day" value="${day}" ${pattern.days.includes(day) ? "checked" : ""}>${day.slice(0, 3)}</label>`).join("")}</div>
                    <div class="dd-hours-fields"><label>Start time<input type="time" class="dd-hours-from" value="${pattern.from}" required></label>
                        <label>End time<input type="time" class="dd-hours-to" value="${pattern.to}" required></label>
                        <label>Appointment length<select class="dd-slot-minutes">${[...new Set([10, 15, 20, 30, 60, pattern.minutes])].sort((a,b)=>a-b).map(n => `<option value="${n}" ${n === pattern.minutes ? "selected" : ""}>${n} minutes</option>`).join("")}</select></label>
                    </div>
                </div></fieldset>
                ${canEdit ? "" : '<p class="dd-note">You have view-only access to this availability.</p>'}
                <p class="dd-note">Availability changes affect new bookings; existing appointments are kept.</p>
                <div class="dd-error" role="alert"></div><div class="dd-save-actions">${canEdit ? '<button class="ac-btn ac-primary" data-dd="save">Save weekly schedule</button>' : ""}
                <button class="ac-btn" data-dd="reset">Reset changes</button></div>`);
        }
        renderLeaveEditor() {
            const today = frappe.datetime.get_today(), start = this.leaveStart || (this.date < today ? today : this.date);
            this.$root.find(".dd-mode .ac-btn").removeClass("ac-primary").filter('[data-dd="date"]').addClass("ac-primary");
            this.$root.find(".dd-editor").html(`<h3>Leave / Holiday</h3><p class="dd-note">Select dates below or mark them on the calendar. This doctor will be unavailable for the entire selected period.</p>
                <fieldset ${this.data.can_manage_leave ? "" : "disabled"}><div class="dd-hours-fields">
                    <label>Leave period<select class="dd-leave-mode"><option value="single" ${!this.leaveRange?"selected":""}>Single day</option><option value="range" ${this.leaveRange?"selected":""}>Date range</option></select></label>
                    <label>Date / From<input class="dd-leave-start" type="date" min="${today}" value="${start}" required></label>
                    <label class="dd-leave-end-label" ${!this.leaveRange?"hidden":""}>To (inclusive)<input class="dd-leave-end" type="date" min="${today}" value="${this.leaveEnd || start}" required></label>
                    <button class="ac-btn ac-primary" data-dd="save-leave">Mark Leave / Holiday</button>
                    <button class="ac-btn" data-dd="remove-leave">Remove leave</button>
                </div></fieldset><p class="dd-note">Leave overrides working hours. Existing appointments are kept. Removing leave restores the regular schedule.</p>
                ${this.data.can_manage_leave?"":'<p class="dd-note">You have view-only access to leave settings.</p>'}<div class="dd-error" role="alert"></div>`);
        }
        async saveLeave(remove=false) {
            if (this.saving) return;
            const inputs=this.$root.find('.dd-leave-start, .dd-leave-end:visible').toArray();
            if (!inputs.every(input=>input.reportValidity())) return;
            const start=this.$root.find('.dd-leave-start').val(), end=this.leaveRange?this.$root.find('.dd-leave-end').val():start;
            if(end<start){this.$root.find('.dd-error').text('End date must be on or after start date.');return;}
            this.saving=true; this.$root.find('button, input, select').prop('disabled',true);
            try {
                const range={practitioner_id:this.selected,start_date:start,end_date:end};
                const preview=await frappe.xcall(`${API}.get_leave_range`,range);
                await frappe.xcall(`${API}.save_leave_range`,{...range,doctor_modified:preview.doctor_modified,expected_versions:preview.versions,remove:remove?1:0});
                this.date=start; this.leaveStart=start; this.leaveEnd=end; this.dirty=false;
                frappe.show_alert({message:remove?'Leave removed':'Leave / holiday saved',indicator:'green'});
                await this.load();
            } catch(e){this.$root.find('.dd-error').text('Leave was not saved. Review the error and try again.');}
            finally{this.saving=false;this.$root.find('button, input, select').prop('disabled',false);}
        }
        workingHours(schedule) {
            const fallback = {days: days.slice(0, 5), from: "10:00", to: "17:00", minutes: 30};
            if (!schedule?.slots.length) return fallback;
            const minutes = value => {const [h,m] = time(value).split(":").map(Number); return h*60+m;};
            const groups = days.map(day => schedule.slots.filter(row => row.day === day).sort((a,b)=>time(a.from_time).localeCompare(time(b.from_time)))).filter(rows=>rows.length);
            const first = groups[0], step = minutes(first[0].to_time)-minutes(first[0].from_time);
            if (step < 5 || step > 240 || groups.some(rows =>
                time(rows[0].from_time) !== time(first[0].from_time) || time(rows.at(-1).to_time) !== time(first.at(-1).to_time) ||
                rows.some((row,i) => minutes(row.to_time)-minutes(row.from_time) !== step ||
                    (row.maximum_appointments || 1) !== 1 || (i && time(rows[i-1].to_time) !== time(row.from_time))))) return {
                        days:groups.map(rows=>rows[0].day),from:'',to:'',minutes:30,custom:true};
            return {days: groups.map(rows=>rows[0].day), from: time(first[0].from_time), to: time(first.at(-1).to_time), minutes: step};
        }
        async applyHours() {
            if (this.saving) return;
            const $builder = this.$root.find(".dd-hours-builder");
            if (!$builder.find("input[type=time]").toArray().every(input => input.reportValidity())) return;
            const weekdays = $builder.find(".dd-working-day:checked").toArray().map(input => input.value);
            if (!weekdays.length) {this.$root.find(".dd-error").text("Select at least one working day."); return;}
            this.saving = true;
            this.$root.find("button, input, select").prop("disabled", true);
            try {
                const result = await frappe.xcall(`${API}.preview_weekly`, {practitioner_id: this.selected, weekdays,
                    from_time: $builder.find(".dd-hours-from").val(), to_time: $builder.find(".dd-hours-to").val(),
                    slot_minutes: Number($builder.find(".dd-slot-minutes").val())});
                this.draftSlots = result.slots;
                this.draftDisabled = 0;
                this.$root.find(".dd-error").text("");
                this.hoursChanged = false;
                this.dirty = true;
                return true;
            } catch (e) {this.$root.find(".dd-error").text("Could not prepare weekly hours. Check that the hours fit complete appointments and no more than 350 slots."); return false;}
            finally {this.saving = false; this.$root.find("button, input, select").prop("disabled", false);}
        }
        action(action) {
            if (this.saving) return;
            if (action === "refresh") return this.leaveDraft(() => this.refresh());
            if (action === "profile") return this.leaveDraft(() => frappe.set_route("Form", "Healthcare Practitioner", this.selected));
            if (["prev", "next", "today"].includes(action)) return this.leaveDraft(() => {
                this.date = action === "today" ? frappe.datetime.get_today() : moment(this.date).add(action === "next" ? 1 : -1, this.mode === "date" ? "months" : "weeks").format("YYYY-MM-DD"); this.load();
            });
            if (["weekly", "date"].includes(action)) return this.leaveDraft(() => {this.mode = action; this.load();});
            if (action === "save-leave") return this.saveLeave();
            if (action === "remove-leave") return this.saveLeave(true);
            if (action === "reset") return this.leaveDraft(() => this.load());
            if (action === "save") return this.save();
        }
        async save() {
            if (this.saving || this.mode !== "weekly") return;
            const schedule = this.data.schedules.find(s => s.id === this.scheduleId);
            if ((this.hoursChanged || !schedule) && !(await this.applyHours())) return;
            this.$root.find(".dd-error").text("");
            this.saving = true;
            this.$root.find("button, input, select").prop("disabled", true);
            try {
                const args = {practitioner_id: this.selected, doctor_modified: this.data.doctor.modified,
                    slots: this.draftSlots, schedule_id: schedule?.id, schedule_modified: schedule?.modified, disabled: this.draftDisabled};
                const result = await frappe.xcall(`${API}.save_weekly`, args);
                this.scheduleId = result.schedule_id;
                this.dirty = false;
                frappe.show_alert({message: "Doctor availability saved", indicator: "green"});
                await this.load();
            } catch (e) {this.$root.find(".dd-error").text("Availability was not saved. Review the error and try again. Refresh if another user changed this schedule.");}
            finally {
                this.saving = false;
                this.$root.find("button, input, select").prop("disabled", false);
                // A failed request must retain the unsaved form; fieldsets preserve read-only access.
            }
        }
    };
})();
