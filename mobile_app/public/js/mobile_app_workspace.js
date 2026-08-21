/* Mobile App workspace dashboard */

(function () {
	const WORKSPACE_LABEL = "Mobile App";
	const DASHBOARD_CLASS = "ma-mobile-dashboard-cards";
	const DASHBOARD_SHELL_CLASS = "ma-mobile-dashboard-shell";
	const ACTIVE_BODY_CLASS = "ma-mobile-dashboard-active";
	const STYLE_ID = "ma-mobile-dashboard-runtime-style";
	let render_timer = null;
	let is_rendering = false;

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
			title: "Doctor Clinical Portal",
			metric: "10",
			accent: "orange",
			icon: "play",
			footer_label: "SLA Status",
			footer_value: "100% Online",
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

		const selected_workspace =
			frappe.workspace?.current_page?.name === WORKSPACE_LABEL ||
			frappe.workspace?._page?.title === WORKSPACE_LABEL ||
			frappe.workspace?._page?.name === WORKSPACE_LABEL ||
			$(".layout-side-section .selected .sidebar-item-label")
				.filter(function () {
					return $(this).text().trim() === WORKSPACE_LABEL;
				})
				.length > 0;

		const workspace_visible =
			frappe.workspace?.body?.length ||
			$('[data-page-route="Workspaces"]:visible').length ||
			$(".editor-js-container:visible").length;

		return Boolean(route_match || (workspace_visible && selected_workspace));
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

		const $page = $('[data-page-route="Workspaces"]').last();
		const $page_body = $page.find(".editor-js-container, .workspace-body").first();
		if ($page_body.length) return $page_body;

		const $raw_main = $(".layout-main-section, .workspace-body")
			.filter(function () {
				const text = compact_text($(this).text());
				return (
					text.includes("MobileAppMobileAppUser") &&
					text.includes("DoctorClinicalPortal")
				);
			})
			.first();
		if ($raw_main.length) return $raw_main;

		const $main = $(".layout-main-section")
			.filter(function () {
				return (
					$(this).find(".shortcut-widget-box").length ||
					$(this).find(".workspace-title, h3, h4").filter(function () {
						return $(this).text().trim() === WORKSPACE_LABEL;
					}).length
				);
			})
			.first();

		return $main.length ? $main : $(".layout-main-section").first();
	}

	function place_dashboard($root, $dashboard) {
		const $existing = $root.find(`.${DASHBOARD_SHELL_CLASS}`).first();
		if ($existing.length) {
			$existing.replaceWith($dashboard);
			return;
		}

		const $title = $root
			.find(".ce-header, .workspace-title, h3, h4")
			.filter(function () {
				return $(this).text().trim() === WORKSPACE_LABEL;
			})
			.first();

		const $title_block = $title.closest(".ce-block, .widget, .workspace-title, .row, .flex");
		if ($title_block.length && $title_block.closest($root).length) {
			$title_block.after($dashboard);
		} else {
			$root.prepend($dashboard);
		}
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

	function set_footer(key, value) {
		$(`[data-card-footer="${key}"]`).text(value ?? "--");
	}

	function get_count(doctype, filters) {
		return frappe.call({
			method: "frappe.client.get_count",
			args: { doctype, filters },
		}).then((response) => response.message);
	}

	function update_metrics() {
		CARDS.forEach((card) => {
			if (!card.doctype) return;

			frappe.db
				.count(card.doctype)
				.then((count) => $(`[data-card-metric="${card.key}"]`).text(count))
				.catch(() => $(`[data-card-metric="${card.key}"]`).text("--"));
		});

		const [this_month_start, next_month_start] = current_month_range();
		get_count("Mobile App User", [
			["Mobile App User", "creation", ">=", this_month_start],
			["Mobile App User", "creation", "<", next_month_start],
		])
			.then((count) => set_footer("users", count))
			.catch(() => set_footer("users", "--"));

		const [today_start, tomorrow_start] = date_range(0, 1);
		get_count("Mobile App Appointment", [
			["Mobile App Appointment", "creation", ">=", today_start],
			["Mobile App Appointment", "creation", "<", tomorrow_start],
		])
			.then((count) => set_footer("appointments", count))
			.catch(() => set_footer("appointments", "--"));

		get_count("App Support Ticket", [["App Support Ticket", "status", "not in", ["Closed", "Resolved"]]])
			.then((count) => set_footer("tickets", count))
			.catch(() => set_footer("tickets", "--"));
	}

	function render_dashboard() {
		if (is_rendering) return;
		is_rendering = true;

		try {
			const active = is_mobile_workspace();
			document.body.classList.toggle(ACTIVE_BODY_CLASS, active);
			if (!active) {
				$(`.${DASHBOARD_SHELL_CLASS}`).remove();
				$(".ma-mobile-dashboard-raw-hidden").removeClass("ma-mobile-dashboard-raw-hidden");
				return;
			}

			inject_runtime_style();
			const $root = workspace_root();
			if (!$root.length) return;

			const $dashboard = $(`
				<div class="${DASHBOARD_SHELL_CLASS}">
					<div class="${DASHBOARD_CLASS}">${CARDS.map(card_html).join("")}</div>
				</div>
			`);
			place_dashboard($root, $dashboard);
			hide_raw_workspace_blocks($root);
			bind_cards($dashboard);
			update_metrics();
		} finally {
			is_rendering = false;
		}
	}

	function schedule_render() {
		window.clearTimeout(render_timer);
		render_timer = window.setTimeout(render_dashboard, 80);
	}

	function schedule_render_burst() {
		schedule_render();
		[250, 600, 1200, 2200, 4000].forEach((delay) => {
			window.setTimeout(schedule_render, delay);
		});
	}

	$(document).on("page-change", () => {
		schedule_render_burst();
	});

	frappe.router?.on?.("change", () => {
		schedule_render_burst();
	});

	$(document).ready(() => {
		schedule_render_burst();
		frappe.after_ajax?.(schedule_render_burst);
	});

	new MutationObserver(() => {
		if (!is_mobile_workspace()) return;
		if (!$(`.${DASHBOARD_SHELL_CLASS}`).length) schedule_render();
	}).observe(document.body, { childList: true, subtree: true });

	window.setInterval(() => {
		if (is_mobile_workspace() && !$(`.${DASHBOARD_SHELL_CLASS}`).length) {
			schedule_render();
		}
	}, 1500);
})();
