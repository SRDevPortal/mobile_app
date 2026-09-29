/* Mobile App workspace dashboard */

(function () {
	const WORKSPACE_LABEL = "Mobile App";
	const DASHBOARD_CLASS = "ma-mobile-dashboard-cards";
	const DASHBOARD_SHELL_CLASS = "ma-mobile-dashboard-shell";
	const ACTIVE_BODY_CLASS = "ma-mobile-dashboard-active";
	const STYLE_ID = "ma-mobile-dashboard-runtime-style";
	let render_timer = null;
	let is_rendering = false;
	let metrics_request = null;
	let $dashboard = null;

	const CARDS = [
		{
			key: "users",
			title: "Mobile App User",
			metric: "--",
			doctype: "Mobile App User",
			accent: "green",
			icon: "user",
			footer_label: "Added this month",
			footer_value: "--",
			route: ["List", "Mobile App User"],
		},
		{
			key: "appointments",
			title: "Mobile App Appointments",
			metric: "--",
			doctype: "Mobile App Appointment",
			accent: "blue",
			icon: "appointment",
			footer_label: "Today's Appointments",
			footer_value: "--",
			route: ["List", "Mobile App Appointment"],
		},
		{
			key: "tickets",
			title: "Support Tickets",
			metric: "--",
			doctype: "App Support Ticket",
			accent: "purple",
			icon: "ticket",
			footer_label: "Active Tickets",
			footer_value: "--",
			route: ["List", "App Support Ticket"],
		},
		{
			key: "portal",
			title: "Appointment Calendar",
			metric: "Open",
			accent: "orange",
			icon: "play",
			footer_label: "Clinic schedule",
			footer_value: "Day / Week / Month",
			action_label: "See Details",
			route: ["doctor-clinical"],
		},
	];

	function escape_html(value) {
		return frappe.utils.escape_html(String(value ?? ""));
	}

	function compact_text(value) {
		return String(value || "").replace(/\s+/g, "");
	}

	function current_route_parts() {
		return (frappe.get_route?.() || []).map((part) =>
			decodeURIComponent(String(part)).toLowerCase()
		);
	}

	function is_mobile_workspace() {
		const route = current_route_parts();
		const route_is_workspace = route.some(
			(part) => part === "workspaces" || part === "workspace"
		);
		const route_is_mobile_workspace = route.length === 1 && route[0] === "mobile-app";
		const route_match =
			route_is_mobile_workspace ||
			(route_is_workspace && route.some((part) => part === WORKSPACE_LABEL.toLowerCase()));

		return route_match;
	}

	function inject_runtime_style() {
		if (document.getElementById(STYLE_ID)) return;

		const style = document.createElement("style");
		style.id = STYLE_ID;
		style.textContent = `
			.${DASHBOARD_SHELL_CLASS} { width: 100%; max-width: none; margin: 0 0 28px; }
			.${DASHBOARD_CLASS} { width: 100%; max-width: none; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 24px; margin: 0 0 28px; }
			.ma-mobile-dashboard-card { position: relative; min-height: 192px; border: 0; border-radius: 24px; padding: 24px; display: flex; flex-direction: column; justify-content: space-between; color: #fff; text-align: left; overflow: hidden; cursor: pointer; box-shadow: 0 16px 32px rgba(15, 23, 42, 0.12); transition: transform .25s ease, box-shadow .25s ease; }
			.ma-mobile-dashboard-card:hover { transform: translateY(-4px); box-shadow: 0 24px 46px rgba(15, 23, 42, 0.18); }
			.ma-mobile-dashboard-card--green { background: linear-gradient(135deg, #10b981 0%, #0f766e 100%); }
			.ma-mobile-dashboard-card--blue { background: linear-gradient(135deg, #3b82f6 0%, #0284c7 100%); }
			.ma-mobile-dashboard-card--purple { background: linear-gradient(135deg, #a855f7 0%, #4f46e5 100%); }
			.ma-mobile-dashboard-card--orange { background: linear-gradient(135deg, #f97316 0%, #dc2626 100%); }
			.ma-mobile-dashboard-card__main { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; }
			.ma-mobile-dashboard-card__title { display: block; font-size: .8rem; font-weight: 800; line-height: 1.25; text-transform: uppercase; color: rgba(255,255,255,.82); }
			.ma-mobile-dashboard-card__metric { display: block; margin-top: .85rem; font-size: 2.55rem; font-weight: 850; line-height: 1; color: #fff; }
			.ma-mobile-dashboard-card__icon { width: 48px; height: 48px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; flex: 0 0 auto; background: rgba(255,255,255,.18); color: #fff; }
			.ma-mobile-dashboard-card__icon svg { width: 25px; height: 25px; fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
			.ma-mobile-dashboard-card__action { display: block; margin-bottom: .65rem; text-align: right; font-size: .7rem; font-weight: 800; color: rgba(255,255,255,.9); }
			.ma-mobile-dashboard-card__rule { height: 1px; background: rgba(255,255,255,.18); margin-bottom: .8rem; }
			.ma-mobile-dashboard-card__footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; font-size: .78rem; font-weight: 650; color: rgba(255,255,255,.82); }
			.ma-mobile-dashboard-card__footer strong { color: #fff; font-weight: 850; white-space: nowrap; }
			body.${ACTIVE_BODY_CLASS} .ma-mobile-dashboard-raw-hidden { display: none !important; }
			body.${ACTIVE_BODY_CLASS} .ma-workspace-title { display: none !important; }
			/* The custom cards have their own loading values; the native skeleton shifts them. */
			body.${ACTIVE_BODY_CLASS} .workspace-skeleton { display: none !important; }
			body.${ACTIVE_BODY_CLASS} .ce-block:has(.ma-workspace-title) { display: none !important; }
			@media (max-width: 1199px) { .${DASHBOARD_CLASS} { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
			@media (max-width: 767px) { .${DASHBOARD_CLASS} { grid-template-columns: 1fr; gap: 16px; } .ma-mobile-dashboard-card { min-height: 176px; } }
		`;
		document.head.appendChild(style);
	}

	function icon_svg(name) {
		const icons = {
			user:
				'<path d="M12 12a4 4 0 1 0-4-4 4 4 0 0 0 4 4Z"/><path d="M5 20a7 7 0 0 1 14 0"/><path d="m9 16 3 3 3-3"/>',
			appointment:
				'<rect x="4" y="5" width="16" height="14" rx="2"/><path d="M8 3v4M16 3v4M4 10h16"/><circle cx="9" cy="15" r="2"/><path d="M13 17c.8-1.8 4.2-1.8 5 0"/>',
			ticket:
				'<path d="M5 6h14a2 2 0 0 1 2 2v2a2 2 0 0 0 0 4v2a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-2a2 2 0 0 0 0-4V8a2 2 0 0 1 2-2Z"/><path d="M9 9h6M9 15h6"/>',
			play: '<circle cx="12" cy="12" r="9"/><path d="m10 8 6 4-6 4Z"/>',
		};

		return `<svg viewBox="0 0 24 24" aria-hidden="true">${icons[name] || icons.user}</svg>`;
	}

	function card_html(card) {
		return `
			<button type="button" class="ma-mobile-dashboard-card ma-mobile-dashboard-card--${card.accent}" data-card-key="${card.key}">
				<div class="ma-mobile-dashboard-card__main">
					<div>
						<span class="ma-mobile-dashboard-card__title">${escape_html(card.title)}</span>
						<span class="ma-mobile-dashboard-card__metric" data-card-metric="${card.key}">${escape_html(card.metric)}</span>
					</div>
					<span class="ma-mobile-dashboard-card__icon">${icon_svg(card.icon)}</span>
				</div>
				<div class="ma-mobile-dashboard-card__bottom">
					${card.action_label ? `<span class="ma-mobile-dashboard-card__action">${escape_html(card.action_label)} &gt;</span>` : ""}
					<div class="ma-mobile-dashboard-card__rule"></div>
					<div class="ma-mobile-dashboard-card__footer">
						<span>${escape_html(card.footer_label)}</span>
						<strong data-card-footer="${card.key}">${escape_html(card.footer_value)}</strong>
					</div>
				</div>
			</button>
		`;
	}

	function workspace_root() {
		const $workspace_body = frappe.workspace?.body?.find(".editor-js-container").first();
		if ($workspace_body?.length) return $workspace_body;
		return $('[data-page-route="Workspaces"] .editor-js-container').first();
	}

	function hide_raw_workspace_blocks($root) {
		if (!$root.find(`.${DASHBOARD_SHELL_CLASS}`).length) return;

		$root.find(".ce-block, .ce-header, .widget, .shortcut-widget-box, h3, h4")
			.filter(function () {
				if ($(this).closest(`.${DASHBOARD_SHELL_CLASS}`).length) return false;
				const text = compact_text($(this).text());
				return (
					$(this).find(".shortcut-widget-box").length ||
					$(this).hasClass("shortcut-widget-box") ||
					$(this).hasClass("ma-workspace-title") ||
					($(this).find(".ma-workspace-title").length && text === "MobileApp") ||
					text === "MobileApp" ||
					(text.includes("MobileAppMobileAppUser") && text.includes("DoctorClinicalPortal"))
				);
			})
			.addClass("ma-mobile-dashboard-raw-hidden");
	}

	function bind_cards($dashboard) {
		$dashboard.find(".ma-mobile-dashboard-card").on("click", function () {
			const card = CARDS.find((item) => item.key === $(this).data("card-key"));
			if (!card?.route) return;
			frappe.set_route(...card.route);
		});
	}

	function date_range(days_offset_start, days_offset_end) {
		const now = new Date();
		const start = new Date(now.getFullYear(), now.getMonth(), now.getDate() + days_offset_start);
		const end = new Date(now.getFullYear(), now.getMonth(), now.getDate() + days_offset_end);
		return [frappe.datetime.obj_to_str(start), frappe.datetime.obj_to_str(end)];
	}

	function current_month_range() {
		const now = new Date();
		const start = new Date(now.getFullYear(), now.getMonth(), 1);
		const end = new Date(now.getFullYear(), now.getMonth() + 1, 1);
		return [frappe.datetime.obj_to_str(start), frappe.datetime.obj_to_str(end)];
	}

	function set_value(attribute, key, value) {
		// Keep updates made while another page is open on the retained cards, too.
		const $value = $dashboard.find(`[data-card-${attribute}="${key}"]`);
		const text = String(value ?? "--");
		if ($value.text() !== text) $value.text(text);
	}

	function set_footer(key, value) {
		set_value("footer", key, value);
	}

	function get_count(doctype, filters) {
		return frappe.call({
			method: "frappe.client.get_count",
			args: { doctype, filters },
		}).then((response) => response.message);
	}

	function update_metrics() {
		if (metrics_request) return false;
		const requests = CARDS.filter(card => card.doctype).map((card) => {
			return frappe.db
				.count(card.doctype)
				.then((count) => set_value("metric", card.key, count))
				.catch(() => set_value("metric", card.key, "--"));
		});

		const [this_month_start, next_month_start] = current_month_range();
		requests.push(get_count("Mobile App User", [
			["Mobile App User", "creation", ">=", this_month_start],
			["Mobile App User", "creation", "<", next_month_start],
		])
			.then((count) => set_footer("users", count))
			.catch(() => set_footer("users", "--")));

		const [today_start, tomorrow_start] = date_range(0, 1);
		requests.push(get_count("Mobile App Appointment", [
			["Mobile App Appointment", "creation", ">=", today_start],
			["Mobile App Appointment", "creation", "<", tomorrow_start],
		])
			.then((count) => set_footer("appointments", count))
			.catch(() => set_footer("appointments", "--")));

		requests.push(get_count("App Support Ticket", [["App Support Ticket", "status", "not in", ["Closed", "Resolved"]]])
			.then((count) => set_footer("tickets", count))
			.catch(() => set_footer("tickets", "--")));
		metrics_request = Promise.all(requests).finally(() => {
			metrics_request = null;
			mobile_app.realtime.flush();
		});
		return metrics_request;
	}

	function render_dashboard() {
		if (is_rendering) return;
		is_rendering = true;

		try {
			const active = is_mobile_workspace();
			document.body.classList.toggle(ACTIVE_BODY_CLASS, active);
			if (!active) {
				$dashboard?.detach();
				$(".ma-mobile-dashboard-raw-hidden").removeClass("ma-mobile-dashboard-raw-hidden");
				return;
			}

			inject_runtime_style();
			const $root = workspace_root();
			if (!$root.length) return;
			const first_render = !$dashboard;
			if (first_render) {
				$dashboard = $(`
					<div class="${DASHBOARD_SHELL_CLASS}">
						<div class="${DASHBOARD_CLASS}">${CARDS.map(card_html).join("")}</div>
					</div>
				`);
				bind_cards($dashboard);
			}
			// Stay outside #editorjs: its asynchronous render replaces its contents.
			// Reuse these nodes and counts when returning from a list or workspace.
			if ($dashboard.parent()[0] !== $root[0]) {
				$root.prepend($dashboard);
				mobile_app.realtime.flush();
			}
			hide_raw_workspace_blocks($root);
			if (first_render) update_metrics();
		} finally {
			is_rendering = false;
		}
	}

	function schedule_render() {
		if (render_timer !== null) return;
		render_timer = window.requestAnimationFrame(() => {
			render_timer = null;
			render_dashboard();
		});
	}

	// Apply the route's visibility before the next paint, including while Frappe loads.
	$(document).on("page-change", render_dashboard);
	frappe.router?.on?.("change", render_dashboard);

	$(document).ready(() => {
		schedule_render();
		frappe.after_ajax?.(schedule_render);
		mobile_app.realtime.watch("workspace", CARDS.filter(card => card.doctype).map(card => card.doctype),
			() => is_mobile_workspace() && Boolean($(`.${DASHBOARD_SHELL_CLASS}`).length), update_metrics);
	});

	new MutationObserver(() => {
		if (!is_mobile_workspace()) return;
		schedule_render();
	}).observe(document.body, { childList: true, subtree: true });
})();
