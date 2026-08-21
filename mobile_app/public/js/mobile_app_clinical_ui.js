/* Clinical UI for Mobile App User — doctor-facing patient chart */

frappe.provide("mobile_app.clinical_ui");

const TOOL_LABELS = {
	bp_data: "BP Monitor",
	sugar_data: "Blood Sugar",
	food_data: "Food Notes",
	prescriptions_data: "Prescriptions",
	med_data: "Medicine Log",
	gfr_data: "GFR Calculator",
	hba1c_data: "HbA1c Tracker",
	kft_reports_data: "KFT Reports",
	cbc_reports_data: "CBC Reports",
	liver_body_data: "Liver & Body Log",
	urine_tracker_data: "Urine Tracker",
	lab_reports_data: "Lab Reports",
	skin_daily_snapshot: "General Lifestyle",
	skin_symptoms_tracking: "Symptoms Tracking",
	skin_patch_tracking: "Patch Tracking",
	paralysis_motor_function: "Motor Function",
	paralysis_neuro_function: "Neuro & Function",
	diet_lifestyle_data: "Diet & Lifestyle",
	exercise_support_data: "Exercise & Support",
	fertility_reports_data: "Fertility Reports",
	varicocele_data: "Varicocele / Cycle",
	vaginal_health_data: "Vaginal Health",
	motor_log_data: "Motor Log",
	functional_log_data: "Functional Log",
	cancer_symptoms_tracker: "Symptoms Tracker",
	cancer_energy_recovery: "Energy & Recovery",
	diabetes_health_data: "Diabetes Health",
	bowel_stool_data: "Bowel & Stool",
	ibs_symptoms_data: "IBS Symptoms",
};

const TOOL_COLUMNS = {
	bp_data: [
		{ key: "date", label: "Date" },
		{ key: "time", label: "Time" },
		{ key: "systolic", label: "Systolic (mmHg)", alert: (v) => Number(v) > 140 },
		{ key: "diastolic", label: "Diastolic (mmHg)", alert: (v) => Number(v) > 90 },
		{ key: "pulse", label: "Pulse" },
		{ key: "weight", label: "Weight" },
	],
	sugar_data: [
		{ key: "date", label: "Date" },
		{ key: "time", label: "Time" },
		{ key: "reading", label: "Reading (mg/dL)", warn: (v) => Number(v) > 180 || Number(v) < 70 },
		{ key: "type", label: "Type" },
	],
	food_data: [
		{ key: "date", label: "Date" },
		{ key: "time", label: "Time" },
		{ key: "meal_type", label: "Meal" },
		{ key: "name", label: "Food" },
		{ key: "calories", label: "Calories" },
	],
	med_data: [
		{ key: "date", label: "Date" },
		{ key: "time", label: "Time" },
		{ key: "name", label: "Medicine" },
		{ key: "status", label: "Status" },
		{ key: "time_of_day", label: "Time of day" },
	],
	gfr_data: [
		{ key: "date", label: "Date" },
		{ key: "creatinine", label: "Creatinine" },
		{ key: "age", label: "Age" },
		{ key: "gender", label: "Gender" },
		{ key: "calculated_gfr", label: "eGFR" },
	],
	hba1c_data: [
		{ key: "date", label: "Date" },
		{ key: "percentage", label: "HbA1c %" },
	],
	prescriptions_data: [
		{ key: "date", label: "Date" },
		{ key: "doctor_name", label: "Doctor" },
		{ key: "clinic_name", label: "Clinic" },
		{ key: "file_kind", label: "Type" },
		{ key: "file_url", label: "File", link: true },
	],
};

function tool_label(key) {
	return TOOL_LABELS[key] || key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function parse_data_json(raw) {
	if (raw == null || raw === "") return [];
	if (Array.isArray(raw)) return raw;
	if (typeof raw === "string") {
		try {
			const p = JSON.parse(raw);
			return Array.isArray(p) ? p : p && typeof p === "object" ? [p] : [];
		} catch (e) {
			return [];
		}
	}
	if (typeof raw === "object") return [raw];
	return [];
}

function format_cell(val, col) {
	if (val === null || val === undefined || val === "") return "—";
	if (col?.link && String(val).trim()) {
		const url = String(val).trim();
		const href = /^https?:\/\//i.test(url) ? url : url;
		return `<a href="${frappe.utils.escape_html(href)}" target="_blank" rel="noopener">${__(
			"View file"
		)}</a>`;
	}
	if (typeof val === "boolean") return val ? __("Yes") : __("No");
	if (typeof val === "object") return frappe.utils.escape_html(JSON.stringify(val));
	return frappe.utils.escape_html(String(val));
}

function infer_columns(rows) {
	if (!rows.length) return [];
	const keys = new Set();
	rows.slice(0, 8).forEach((r) => {
		if (r && typeof r === "object") Object.keys(r).forEach((k) => keys.add(k));
	});
	const order = ["date", "time", "timestamp", "logged_at"];
	return [...keys]
		.filter((k) => k !== "id")
		.sort((a, b) => {
			const ai = order.indexOf(a);
			const bi = order.indexOf(b);
			if (ai !== -1 && bi !== -1) return ai - bi;
			if (ai !== -1) return -1;
			if (bi !== -1) return 1;
			return a.localeCompare(b);
		})
		.map((k) => ({
			key: k,
			label: k.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()),
		}));
}

function row_class(columns, row) {
	for (const col of columns) {
		const v = row[col.key];
		if (col.alert && col.alert(v, row)) return "ma-row-alert";
		if (col.warn && col.warn(v, row)) return "ma-row-warn";
	}
	return "";
}

mobile_app.clinical_ui.render_clinical_table = function (columns, rows) {
	const $wrap = $('<div class="table-responsive"></div>');
	if (!rows.length) {
		return $('<div class="ma-empty-state"></div>')
			.append('<div class="ma-empty-state__icon">📋</div>')
			.append($("<p></p>").text(__("No log entries recorded for this tool yet.")));
	}
	const cols = columns.length ? columns : infer_columns(rows);
	const $table = $('<table class="ma-clinical-table"></table>');
	const $thead = $("<thead><tr></tr></thead>");
	cols.forEach((c) => {
		$thead.find("tr").append($("<th></th>").text(c.label));
	});
	const $tbody = $("<tbody></tbody>");
	rows
		.slice()
		.reverse()
		.forEach((row) => {
			const $tr = $(`<tr class="${row_class(cols, row)}"></tr>`);
			cols.forEach((c) => {
				$tr.append($("<td></td>").html(format_cell(row[c.key], c)));
			});
			$tbody.append($tr);
		});
	$table.append($thead).append($tbody);
	$wrap.append($table);
	return $wrap;
};

function get_primary_profile(frm) {
	return (frm.doc.profiles || [])[0] || null;
}

mobile_app.clinical_ui.get_patient_display_name = function (frm) {
	const profile = get_primary_profile(frm);
	return (
		profile?.profile_name ||
		frm.doc.full_name ||
		[frm.doc.first_name, frm.doc.last_name].filter(Boolean).join(" ") ||
		frm.docname
	);
};

function calc_bmi(profile) {
	if (!profile) return null;
	const h = Number(profile.height);
	const w = Number(profile.weight);
	if (!h || !w) return null;
	const hm = h > 3 ? h / 100 : h;
	const bmi = w / (hm * hm);
	return Number.isFinite(bmi) ? bmi.toFixed(1) : null;
}

function section_wrapper(frm, section_fieldname) {
	const f = frm.fields_dict[section_fieldname];
	if (!f?.$wrapper?.length) return $();
	return f.$wrapper.closest(".form-section");
}

function append_vital_card($grid, label, value, modifier, layout) {
	const $card = $('<div class="ma-vital-card"></div>');
	if (modifier) $card.addClass(`ma-vital-card--${modifier}`);
	if (layout) $card.addClass(`ma-vital-card--span-${layout}`);
	$card.append($('<div class="ma-vital-card__label"></div>').text(label));
	const $val = $('<div class="ma-vital-card__value"></div>').text(value);
	if (modifier === "email") $val.attr("title", value);
	$card.append($val);
	$grid.append($card);
}

mobile_app.clinical_ui.render_patient_header = function (frm) {
	frm.$wrapper.find(".ma-clinical-header-mount").remove();
	const profile = get_primary_profile(frm);
	const display_name = mobile_app.clinical_ui.get_patient_display_name(frm);
	const $mount = $('<div class="ma-clinical-header-mount"></div>');
	const $header = $('<div class="ma-clinical-header"></div>');

	const $top = $('<div class="ma-clinical-header__top"></div>');
	const $left = $('<div class="ma-clinical-header__identity"></div>');
	const $nameRow = $('<div class="ma-clinical-header__name-row"></div>');
	const disease = profile?.disease || "—";
	$nameRow.append(
		$('<h2 class="ma-clinical-header__name"></h2>').text(display_name)
	);
	$left.append($nameRow);
	const $meta = $('<p class="ma-clinical-header__meta"></p>');
	$meta.append(`${__("Patient ID")}: `);
	$meta.append(
		$('<span class="ma-value-tag ma-value-tag--inline"></span>').text(
			frm.doc.external_id || frm.docname
		)
	);
	$left.append($meta);

	const $statusRow = $('<div class="ma-clinical-header__status-row"></div>');
	if (disease && disease !== "—") {
		$statusRow.append($('<span class="ma-chip ma-chip--accent"></span>').text(disease));
	}
	if (frm.doc.is_active) {
		const $active = $('<span class="ma-chip ma-chip--active"></span>');
		$active.append($('<span class="ma-chip__dot" aria-hidden="true"></span>'));
		$active.append(document.createTextNode(__("Active")));
		$statusRow.append($active);
	} else {
		$statusRow.append($('<span class="ma-chip ma-chip--warn"></span>').text(__("Inactive")));
	}

	const age = profile?.age != null ? `${profile.age} yrs` : "—";
	const gender = profile?.gender || "—";
	$top.append($left).append($statusRow);

	const phone = frm.doc.phone || profile?.phone || "—";
	const email = frm.doc.email || profile?.email || "—";
	const $vitals = $('<div class="ma-vitals-rows"></div>');

	append_vital_card($vitals, __("Phone"), phone, "phone", "half");
	append_vital_card($vitals, __("Email"), email, "email", "half");
	append_vital_card($vitals, __("Gender"), gender, null, "fifth");
	append_vital_card($vitals, __("Age"), age, null, "fifth");
	append_vital_card(
		$vitals,
		__("Height"),
		profile?.height != null ? `${profile.height} cm` : "—",
		null,
		"fifth"
	);
	append_vital_card(
		$vitals,
		__("Weight"),
		profile?.weight != null ? `${profile.weight} kg` : "—",
		null,
		"fifth"
	);
	append_vital_card(
		$vitals,
		__("Clinical logs"),
		String((frm.doc.health_entries || []).length),
		"count",
		"fifth"
	);
	$header.append($top).append($vitals);
	$mount.append($header);

	const $layout = frm.$wrapper.find(".form-layout").first();
	if ($layout.length) $layout.before($mount);
	else frm.$wrapper.prepend($mount);
};

function render_summary_panel(frm, section_fieldname, title, hint) {
	const $section = section_wrapper(frm, section_fieldname);
	if (!$section.length) return;

	$section.find(".ma-profile-panel-mount").remove();
	const profile = get_primary_profile(frm);
	if (!profile) return;

	const stats = [
		[__("Gender"), profile.gender],
		[__("Disease"), profile.disease],
		[__("Age"), profile.age],
		[__("Height"), profile.height != null ? `${profile.height} cm` : null],
		[__("Weight"), profile.weight != null ? `${profile.weight} kg` : null],
		[__("BMI"), calc_bmi(profile)],
		[__("Membership"), profile.membership_type],
		[__("Doctor assigned"), profile.doctor_assigned],
		[__("Patient ID"), profile.patient_id],
	];

	const $mount = $('<div class="ma-profile-panel-mount"></div>');
	const $panel = $('<div class="ma-profile-panel"></div>');
	$panel.append($("<h4></h4>").text(title));
	const $stats = $('<div class="ma-profile-stats"></div>');
	stats.forEach(([label, value]) => {
		if (value == null || value === "") return;
		const $stat = $('<div class="ma-stat"></div>');
		$stat.append($('<div class="ma-stat__label"></div>').text(label));
		$stat.append($('<div class="ma-stat__value"></div>').text(String(value)));
		$stats.append($stat);
	});
	$panel.append($stats);
	if (hint) {
		$panel.append($('<p class="text-muted small mt-3 mb-0"></p>').text(hint));
	}
	$mount.append($panel);
	$section.prepend($mount);
}

mobile_app.clinical_ui.render_profile_panel = function (frm) {
	render_summary_panel(
		frm,
		"profiles_section",
		__("Clinical profile summary"),
		__("Profile rows are synced from the mobile app.")
	);
};

mobile_app.clinical_ui.render_medical_summary = function (frm) {
	render_summary_panel(
		frm,
		"medical_section",
		__("Patient vitals & context"),
		__("Medical records and prescriptions from the mobile app appear below.")
	);
};

mobile_app.clinical_ui.render_health_workspace = function (frm) {
	const $section = section_wrapper(frm, "health_entries_section");
	$section.find(".ma-health-workspace-mount").remove();

	const entries = (frm.doc.health_entries || []).filter((r) => r.tool_key);
	const $mount = $('<div class="ma-health-workspace-mount"></div>');
	const $workspace = $('<div class="ma-health-workspace"></div>');
	const $nav = $('<div class="ma-tool-nav"></div>');
	const $panel = $('<div class="ma-tool-panel"></div>');
	const $panelHead = $('<div class="ma-tool-panel__head"></div>');
	const $panelTitle = $('<h3 class="ma-tool-panel__title"></h3>');
	const $panelMeta = $('<div class="ma-tool-panel__meta"></div>');
	const $panelBody = $('<div class="ma-tool-panel__body"></div>');

	$panelHead.append($panelTitle).append($panelMeta);
	$panel.append($panelHead).append($panelBody);

	if (!entries.length) {
		$nav.append(
			$('<div class="ma-empty-state p-3"></div>').append(
				$('<p class="mb-0 small"></p>').text(__("No health tools synced yet."))
			)
		);
		$panelBody.append(
			$('<div class="ma-empty-state"></div>').append(
				$("<p></p>").text(
					__(
						"Patient clinical logs will appear here when the mobile app syncs health tools (BP, sugar, etc.)."
					)
				)
			)
		);
	} else {
		let active_key = frm._ma_active_tool_key || entries[0].tool_key;

		function render_tool(key) {
			frm._ma_active_tool_key = key;
			$nav.find(".ma-tool-nav__item").removeClass("is-active");
			$nav.find(`.ma-tool-nav__item[data-tool="${key}"]`).addClass("is-active");

			const row = entries.find((e) => e.tool_key === key) || entries[0];
			const rows = parse_data_json(row.data_json);
			const cols = TOOL_COLUMNS[key] || [];

			$panelTitle.text(tool_label(key));
			const ts = row.entry_timestamp
				? frappe.datetime.str_to_user(row.entry_timestamp)
				: "—";
			$panelMeta.html(
				`${__("Last sync")}: <strong>${frappe.utils.escape_html(ts)}</strong> · ${rows.length} ${__(
					"log entries"
				)}`
			);
			$panelBody.empty().append(mobile_app.clinical_ui.render_clinical_table(cols, rows));
		}

		entries.forEach((row) => {
			const count = parse_data_json(row.data_json).length;
			const $btn = $(`<button type="button" class="ma-tool-nav__item" data-tool="${row.tool_key}"></button>`);
			$btn.append($('<div class="ma-tool-nav__title"></div>').text(tool_label(row.tool_key)));
			$btn.append(
				$('<div class="ma-tool-nav__sub"></div>').text(
					`${count} ${__("log entries")}`
				)
			);
			$btn.on("click", () => render_tool(row.tool_key));
			$nav.append($btn);
		});

		render_tool(active_key);
	}

	$workspace.append($nav).append($panel);
	$mount.append($workspace);

	const $toggle = $(`<p class="ma-section-toggle"></p>`).append(
		$('<a href="#" class="text-muted"></a>').text(__("Show / hide raw data table (ERP)"))
	);
	$toggle.find("a").on("click", (e) => {
		e.preventDefault();
		const $raw = frm.fields_dict.health_entries?.$wrapper;
		if ($raw) $raw.toggle();
	});

	$mount.append($toggle);
	$section.find(".frappe-control[data-fieldname=health_entries]").first().before($mount);

	const $healthGrid = frm.fields_dict.health_entries?.$wrapper;
	if ($healthGrid && !frm._ma_health_grid_visible) {
		$healthGrid.hide();
	}
};

mobile_app.clinical_ui.enhance_appointments = function (frm) {
	if (frm.is_new()) return;
	frm.add_custom_button(
		__("Patient appointments"),
		() => {
			frappe.set_route("List", "Mobile App Appointment", {
				mobile_app_user: frm.doc.name,
			});
		},
		__("Clinical")
	);
};

mobile_app.clinical_ui.setup = function (frm) {
	if (frm.is_new()) return;

	if (mobile_app.desk?.apply_doctor_desk_restrictions) {
		mobile_app.desk.apply_doctor_desk_restrictions(frm);
	}

	mobile_app.clinical_ui.render_patient_header(frm);
	mobile_app.clinical_ui.enhance_appointments(frm);

	// Only mount tab-specific panels when that tab is active (avoids wrong-tab bleed)
	const active_tab = frm.get_active_tab?.()?.df?.fieldname;
	if (active_tab === "medical_tab") {
		mobile_app.clinical_ui.render_medical_summary(frm);
	}
	if (active_tab === "health_entries_tab") {
		mobile_app.clinical_ui.render_health_workspace(frm);
	}
};
