// Appointment draft autosave owned by mobile_app.
function setup_appointment_autosave(frm) {
    if (frm._appointment_autosave) return;
    const state = frm._appointment_autosave = { timer: null, saving: false };
    // Only notify when attention is needed; never add status text to the toolbar.
    const notify = (message, indicator) => {
        const key = frm.doc.name + ':' + message;
        if (state.notice === key) return;
        state.notice = key;
        frappe.show_alert({ message, indicator }, 5);
    };
    const eligible = () => window.cur_frm === frm &&
        frappe.get_route()[0] === 'Form' && frappe.get_route()[2] === frm.doc.name &&
        !frm.is_new() && frm.doc.docstatus === 0 && !frm.save_disabled &&
        (frm.doc.sr_encounter_type || '').trim().toLowerCase() === 'appointment';
    // A deliberate Save must reach the server even if autosave already saved
    // all edits, so the doctor can approve that version for checkout.
    const originalSave = frm.save.bind(frm);
    frm.save = function (action, ...args) {
        if ((!action || action === 'Save') && !state.saving && eligible() && !frm.is_dirty()) {
            frm.dirty();
        }
        const savedDoc = frm.doc.name;
        return Promise.resolve(originalSave(action, ...args)).then(result => {
            // Reset notifications only after this encounter is successfully saved.
            if (frm.doc.name === savedDoc && !frm.is_dirty()) {
                clearTimeout(state.timer);
                state.notice = null;
            }
            return result;
        });
    };
    const schedule = () => {
        clearTimeout(state.timer);
        if (!eligible()) { state.notice = null; return; }
        if (state.saving) return;
        const name = frm.doc.name;
        state.timer = setTimeout(() => save(name), 1000);
    };
    const save = async (name) => {
        if (!eligible() || frm.doc.name !== name) return;
        if (!frm.is_dirty()) {
            state.notice = null;
            return;
        }
        // Let link lookups, manual saves and attachment saves finish first.
        if (frappe.ui.form.is_saving || frappe.request.ajax_count || frm._skipNextSaveDueToProof) {
            schedule();
            return;
        }
        if (!appointment_autosave_complete(frm)) {
            notify(__('Autosave is waiting for required fields. Click Save to see which fields are missing.'), 'orange');
            return;
        }
        state.saving = true;
        try {
            // Normal AJAX draft save preserves all validation and permissions.
            // Frappe briefly freezes editing while the request is in flight.
            if (!eligible() || frm.doc.name !== name || !frm.is_dirty()) return;
            frm.doc.__opd_autosave = 1;
            await new Promise((resolve, reject) => {
                frappe.ui.form.close_grid_form();
                frm.validate_and_save('Save', null, null, () => {}, resolve, reject);
            });
            if (frm.doc.name === name && eligible()) {
                if (frm.is_dirty()) {
                    notify(__('Save failed - use Save to retry'), 'red');
                } else {
                    state.notice = null;
                    // The standard savedocs response already shows the Saved notification.
                }
            }
        } catch (error) {
            if (frm.doc.name === name) notify(__('Save failed - use Save to retry'), 'red');
        } finally {
            delete frm.doc.__opd_autosave;
            state.saving = false;
        }
    };
    // Dirty includes parent fields, child edits, row additions and deletions.
    frm.$wrapper.on('dirty.appointment_autosave', schedule);
    // Reset the delay while typing before the control commits its value.
    frm.$wrapper.on('input.appointment_autosave change.appointment_autosave',
        'input, textarea, select, [contenteditable]', schedule);
    frappe.router.on('change', () => {
        clearTimeout(state.timer);
        state.notice = null;
    });
}

function appointment_autosave_complete(frm) {
    return frappe.model.get_all_docs(frm.doc).every(doc =>
        (frappe.meta.docfield_list[doc.doctype] || []).every(field => {
            const df = frappe.meta.get_docfield(doc.doctype, field.fieldname, doc.name);
            if (!df || !df.fieldname) return true;
            let required = df.reqd;
            const expression = df.mandatory_depends_on;
            if (expression) {
                try {
                    if (typeof expression === 'boolean') required = expression;
                    else if (typeof expression === 'function') required = expression(doc);
                    else if (expression.startsWith('eval:')) {
                        required = frappe.utils.eval(expression.slice(5), { doc, parent: frm.doc });
                    } else {
                        const value = doc[expression];
                        required = Array.isArray(value) ? value.length > 0 : !!value;
                    }
                } catch (error) {
                    return false;
                }
            }
            return !required || frappe.model.has_value(doc.doctype, doc.name, df.fieldname);
        })
    );
}

frappe.ui.form.on('Patient Encounter', {
    refresh(frm) {
        setup_appointment_autosave(frm);
    }
});

