// Mobile App User — clinical doctor-facing form UI + sidebar avatar

const LIVE_POLL_MS = 12000;
const CLINICAL_CSS = "/assets/mobile_app/css/mobile_app_clinical.css";

function clear_live_poll(frm) {
	if (frm._ma_live_poll) {
		clearInterval(frm._ma_live_poll);
		frm._ma_live_poll = null;
	}
}

function route_matches_frm(frm) {
	const r = frappe.get_route();
	return r && r[0] === "Form" && r[1] === frm.doctype && String(r[2]) === String(frm.docname);
}

function start_live_poll(frm) {
	clear_live_poll(frm);
	if (frm.is_new()) return;

	frm._ma_live_poll = setInterval(() => {
		if (!frm.doc || frm.is_dirty()) return;
		if (!route_matches_frm(frm)) {
			clear_live_poll(frm);
			return;
		}
		frappe.db.get_value(frm.doctype, { name: frm.docname }, "modified", (row) => {
			if (!row?.modified || !frm.doc) return;
			if (row.modified !== frm.doc.modified) {
				frm.reload_doc().then(() => {
					frappe.show_alert({ message: __("Record updated"), indicator: "green" }, 3);
				});
			}
		});
	}, LIVE_POLL_MS);
}

function setup_sidebar(frm) {
	if (!frappe.boot.desk_settings.form_sidebar || !frm.sidebar) return;

	const img = frm.fields_dict.image;
	if (img?.df?.fieldtype === "Attach Image" && !frm.meta.image_field) {
		frm.meta.image_field = "image";
	}

	if (frm.page?.sidebar?.length) {
		frm.page.sidebar.removeClass("hide-sidebar").show();
	}

	const $wrap = frm.sidebar.sidebar;
	if ($wrap?.length) $wrap.removeClass("hidden-xs hidden-sm");

	if (frm.meta.image_field && frm.sidebar.image_section?.length) {
		frm.sidebar.image_section.removeClass("hide");
	}

	if (frm.meta.image_field) frappe.ui.form.set_user_image(frm);
}

function setup_clinical_ui(frm) {
	if (frm.is_new()) return;
	if (mobile_app.clinical_ui) {
		mobile_app.clinical_ui.setup(frm);
		return;
	}

	setTimeout(() => {
		if (!frm.is_new() && mobile_app.clinical_ui) {
			mobile_app.clinical_ui.setup(frm);
		}
	}, 250);
}

function get_profile_display_name(frm) {
	if (mobile_app.clinical_ui?.get_patient_display_name) {
		return mobile_app.clinical_ui.get_patient_display_name(frm);
	}
	const profile = (frm.doc.profiles || [])[0] || null;
	return (
		profile?.profile_name ||
		frm.doc.full_name ||
		[frm.doc.first_name, frm.doc.last_name].filter(Boolean).join(" ") ||
		frm.docname
	);
}

function apply_profile_display_name(frm) {
	const display_name = get_profile_display_name(frm);
	if (!display_name || display_name === frm.docname) return;

	if (frm.doc.full_name !== display_name) {
		frm.doc.full_name = display_name;
		frm.fields_dict.full_name?.set_value(display_name);
		frm.refresh_field("full_name");
	}

	if (frm.page?.set_title) {
		frm.page.set_title(display_name);
	}

	frm.$wrapper
		.closest(".page-container")
		.find(".page-title .title-text")
		.first()
		.text(display_name);
}

function clear_clinical_header(frm) {
	frm.$wrapper?.find(".ma-clinical-header-mount").remove();
}

function setup_support_ticket_ui(frm) {
	if (frm.is_new() || !mobile_app.support_ticket_ui) return;
	mobile_app.support_ticket_ui.setup(frm);
}

function open_routed_support_ticket_chat(frm) {
	const opts = frappe.route_options || {};
	if (!opts.open_support_ticket_chat) return;

	const ticket_name = opts.support_ticket_name;
	delete opts.open_support_ticket_chat;
	delete opts.support_ticket_name;

	frm._ma_active_ticket_name = ticket_name || null;

	setTimeout(() => {
		const tab = frm.fields_dict.support_ticket_workspace?.tab;
		if (tab?.set_active) {
			tab.set_active();
		} else {
			frm.$wrapper
				.find('.form-tabs .nav-link[data-fieldname="support_ticket_tab"]')
				.trigger("click");
		}

		setup_support_ticket_ui(frm);
		setTimeout(() => {
			const $mount = frm.$wrapper.find(".ma-support-inbox-mount").first();
			if ($mount.length) {
				$mount[0].scrollIntoView({ behavior: "smooth", block: "start" });
			}
		}, 350);
	}, 120);
}

frappe.ui.form.on("Mobile App User", {
	onload(frm) {
		frappe.require(CLINICAL_CSS);

		if (mobile_app.desk?.block_new_patient_route?.(frm)) {
			return;
		}
	},

	refresh(frm) {
		clear_live_poll(frm);

		if (frm.is_new()) {
			clear_clinical_header(frm);
			mobile_app.desk?.block_new_patient_route?.(frm);
			return;
		}

		if (mobile_app.desk?.apply_doctor_desk_restrictions) {
			mobile_app.desk.apply_doctor_desk_restrictions(frm);
		}

		apply_profile_display_name(frm);
		start_live_poll(frm);
		setup_sidebar(frm);
		setup_clinical_ui(frm);
		setup_support_ticket_ui(frm);
		open_routed_support_ticket_chat(frm);
	},

	medical_tab(frm) {
		setTimeout(() => {
			if (mobile_app.clinical_ui) {
				mobile_app.clinical_ui.render_medical_summary(frm);
			}
		}, 80);
	},

	health_entries_tab(frm) {
		setTimeout(() => {
			if (mobile_app.clinical_ui) {
				mobile_app.clinical_ui.render_health_workspace(frm);
			}
		}, 80);
	},

	support_ticket_tab(frm) {
		setTimeout(() => {
			setup_support_ticket_ui(frm);
		}, 80);
	},

	after_save(frm) {
		apply_profile_display_name(frm);
		setup_clinical_ui(frm);
	},
});
