/* Support Ticket inbox tab — Mobile App User */

frappe.provide("mobile_app.support_ticket_ui");

const SUPPORT_TICKET_CSS = "/assets/mobile_app/css/mobile_app_support_ticket.css";
const SUPPORT_TICKET_REFRESH_MS = 10000;

function time_ago(ts) {
	if (!ts) return "";
	try {
		return frappe.datetime.prettyDate(ts);
	} catch (e) {
		return "";
	}
}

function initials(text) {
	const parts = String(text || "?")
		.trim()
		.split(/\s+/)
		.filter(Boolean);
	if (!parts.length) return "?";
	if (parts.length === 1) return parts[0].slice(0, 1).toUpperCase();
	return (parts[0][0] + parts[1][0]).toUpperCase();
}

function section_wrapper(frm, section_fieldname) {
	const f = frm.fields_dict[section_fieldname];
	if (!f?.$wrapper?.length) return $();
	return f.$wrapper.closest(".form-section");
}

function status_class(status) {
	return String(status || "Open").toLowerCase().replace(/\s+/g, "-");
}

function ticket_matches_filters(frm, ticket) {
	const filters = frm._ma_support_ticket_filters || {};
	if (filters.status && filters.status !== "__all" && ticket.status !== filters.status) return false;
	if (filters.category && filters.category !== "__all" && (ticket.label || "") !== filters.category) return false;
	return true;
}

function update_category_filter_options(frm, tickets) {
	const $root = frm._ma_support_inbox;
	const $category = $root.find(".ma-support-inbox__filter--category");
	if (!$category.length) return;

	const current = $category.val() || "__all";
	const categories = [...new Set(tickets.map((ticket) => ticket.label).filter(Boolean))].sort();
	$category.empty().append($("<option></option>").val("__all").text(__("All Categories")));
	categories.forEach((category) => {
		$category.append($("<option></option>").val(category).text(category));
	});
	const next_value = categories.includes(current) ? current : "__all";
	$category.val(next_value);
	frm._ma_support_ticket_filters = frm._ma_support_ticket_filters || {};
	frm._ma_support_ticket_filters.category = next_value;
}

function apply_ticket_filters(frm, opts = {}) {
	const tickets = frm._ma_support_tickets || [];
	render_ticket_list(frm, tickets.filter((ticket) => ticket_matches_filters(frm, ticket)), opts);
}

function render_messages($messages, ticket, opts = {}) {
	const node = $messages[0];
	const was_near_bottom = node
		? node.scrollHeight - node.scrollTop - node.clientHeight < 80
		: true;

	$messages.empty();
	const messages = ticket?.messages || [];
	if (!messages.length) {
		$messages.append(
			$('<div class="ma-support-inbox__thread-empty"></div>').append(
				$("<p></p>").text(__("No messages in this ticket yet."))
			)
		);
		return;
	}
	messages.forEach((msg) => {
			const from = (msg.from || msg.sender || "customer").toLowerCase();
			const is_agent = from === "agent" || from === "admin" || from === "staff" || from === "support";
			const body = msg.body || msg.text || msg.message || "";
			const $row = $(`<div class="ma-support-inbox__bubble-row ma-support-inbox__bubble-row--${is_agent ? "agent" : "customer"}"></div>`);
			const $stack = $('<div class="ma-support-inbox__bubble-stack"></div>');
			const $bubble = $('<div class="ma-support-inbox__bubble"></div>').text(body);
			$stack.append($bubble);
			const meta = msg.timestamp ? time_ago(msg.timestamp) : "";
			if (meta) {
				$stack.append($('<div class="ma-support-inbox__bubble-meta"></div>').text(meta));
			}
			$row.append($stack);
			$messages.append($row);
		});
	if (!opts.preserve_scroll || was_near_bottom) {
		$messages.scrollTop($messages[0].scrollHeight);
	}
}

function select_ticket(frm, ticket, opts = {}) {
	frm._ma_active_ticket_name = ticket?.name;
	const $root = frm._ma_support_inbox;
	if (!$root?.length) return;

	$root.find(".ma-support-inbox__item").removeClass("is-active");
	$root.find(`.ma-support-inbox__item[data-ticket="${ticket.name}"]`).addClass("is-active");

	const $pane = $root.find(".ma-support-inbox__thread-pane");
	const $title = $root.find(".ma-support-inbox__thread-title");
	const $description = $root.find(".ma-support-inbox__thread-description");
	const $status = $root.find(".ma-support-inbox__thread-status");
	const $messages = $root.find(".ma-support-inbox__messages");
	const $input = $root.find(".ma-support-inbox__composer-input");
	const is_closed = ticket.status === "Closed";
	const description = (ticket.description || "").trim();

	$pane.removeClass("is-empty");
	$title.text(ticket.subject);
	$description.text(description).toggle(Boolean(description));
	$status.text(ticket.status || __("Open"));
	$root.find(".ma-support-inbox__status-btn").removeClass("is-active");
	$root.find(`.ma-support-inbox__status-btn[data-status="${ticket.status}"]`).addClass("is-active");
	render_messages($messages, ticket, { preserve_scroll: opts.preserve_scroll });
	if (!opts.preserve_composer) {
		$input.val("");
	}
	$input.prop("disabled", is_closed);
	$root.find(".ma-support-inbox__send-btn").prop("disabled", is_closed);
}

function render_ticket_list(frm, tickets, opts = {}) {
	const $root = frm._ma_support_inbox;
	const $list = $root.find(".ma-support-inbox__list");
	$list.empty();

	if (!tickets.length) {
		frm._ma_active_ticket_name = null;
		$list.append(
			$('<div class="ma-support-inbox__thread-empty p-4"></div>').append(
				$('<p class="mb-0"></p>').text(
					__(
						"No support tickets yet for this mobile app user."
					)
				)
			)
		);
		$root.find(".ma-support-inbox__thread-pane").addClass("is-empty");
		if (opts.clear_thread !== false) {
			frm._ma_active_ticket_name = null;
		}
		$root.find(".ma-support-inbox__messages").empty().append(
			$('<div class="ma-support-inbox__thread-empty"></div>').append(
				$("<p></p>").text(__("No support tickets match these filters."))
			)
		);
		return;
	}

	const patient = mobile_app.clinical_ui?.get_patient_display_name
		? mobile_app.clinical_ui.get_patient_display_name(frm)
		: frm.doc.full_name || frm.docname;
	tickets.forEach((ticket) => {
		const $item = $(`<div class="ma-support-inbox__item" data-ticket="${ticket.name}"></div>`);
		const $avatar = $('<div class="ma-support-inbox__avatar"></div>');
		if (ticket.profile_image_url) {
			const $img = $("<img />", {
					alt: patient,
					src: ticket.profile_image_url,
				})
				.on("error", () => {
					$avatar.removeClass("has-image").empty().text(initials(patient));
				});
			$avatar.addClass("has-image").append($img);
		} else {
			$avatar.text(initials(patient));
		}
		$item.append($avatar);
		const $main = $('<div class="ma-support-inbox__item-main"></div>');
		$main.append($('<div class="ma-support-inbox__item-name"></div>').text(patient));
		$main.append($('<div class="ma-support-inbox__item-preview"></div>').text(ticket.preview || "—"));
		if (ticket.label) {
			$main.append($('<span class="ma-support-inbox__item-label"></span>').text(ticket.label));
		}
		$item.append($main);
		const $meta = $('<div class="ma-support-inbox__item-meta"></div>').append(
			$('<span class="ma-support-inbox__item-time"></span>').text(time_ago(ticket.recorded_at) || ticket.status)
		);
		$meta.append(
			$(`<span class="ma-support-inbox__item-status ma-support-inbox__item-status--${status_class(
				ticket.status
			)}"></span>`).text(String(ticket.status || __("Open")).toUpperCase())
		);
		$item.append($meta);
		if (ticket.unread_count) {
			$item.append(
				$('<span class="ma-support-inbox__unread-badge"></span>').text(ticket.unread_count)
			);
		}
		$item.on("click", () => select_ticket(frm, ticket));
		$list.append($item);
	});

	const active_name = frm._ma_active_ticket_name;
	const active = tickets.find((t) => t.name === active_name) || (opts.select_first === false ? null : tickets[0]);
	if (active) {
		select_ticket(frm, active, opts);
	}
}

function refresh_tickets(frm, opts = {}) {
	if (frm.is_new() || frm._ma_support_ticket_refreshing) return;
	const $root = frm._ma_support_inbox;
	if (!$root?.length || !document.body.contains($root[0])) return;

	frm._ma_support_ticket_refreshing = true;
	frappe.call({
		method: "mobile_app.api.support_ticket.get_support_tickets",
		args: { mobile_app_user: frm.doc.name },
		callback(r) {
			try {
				frm._ma_support_tickets = r.message?.tickets || [];
				update_category_filter_options(frm, frm._ma_support_tickets);
				apply_ticket_filters(frm, opts);
			} finally {
				frm._ma_support_ticket_refreshing = false;
			}
		},
		error() {
			frm._ma_support_ticket_refreshing = false;
		},
	});
}

function start_ticket_refresh(frm) {
	if (frm._ma_support_ticket_refresh_timer) {
		clearInterval(frm._ma_support_ticket_refresh_timer);
	}
	frm._ma_support_ticket_refresh_timer = setInterval(() => {
		const $root = frm._ma_support_inbox;
		if (!$root?.length || !document.body.contains($root[0])) {
			clearInterval(frm._ma_support_ticket_refresh_timer);
			frm._ma_support_ticket_refresh_timer = null;
			return;
		}
		refresh_tickets(frm, {
			preserve_composer: true,
			preserve_scroll: true,
		});
	}, SUPPORT_TICKET_REFRESH_MS);
}

function send_reply(frm) {
	const $root = frm._ma_support_inbox;
	const $input = $root.find(".ma-support-inbox__composer-input");
	const body = ($input.val() || "").trim();
	const ticket_name = frm._ma_active_ticket_name;
	if (body === "" || !ticket_name) return;

	$root.find(".ma-support-inbox__send-btn").prop("disabled", true);
	frappe.call({
		method: "mobile_app.api.support_ticket.send_support_reply",
		args: {
			mobile_app_user: frm.doc.name,
			ticket_name,
			message: body,
		},
		callback(r) {
			$root.find(".ma-support-inbox__send-btn").prop("disabled", false);
			if (!r.message) return;
			$input.val("");
			frm._ma_support_tickets = r.message?.tickets || [];
			update_category_filter_options(frm, frm._ma_support_tickets);
			apply_ticket_filters(frm, { preserve_scroll: false });
		},
		error() {
			$root.find(".ma-support-inbox__send-btn").prop("disabled", false);
		},
	});
}

function set_ticket_status(frm, status) {
	const ticket_name = frm._ma_active_ticket_name;
	if (!ticket_name) return;

	const $root = frm._ma_support_inbox;
	$root.find(".ma-support-inbox__status-btn").prop("disabled", true);
	frappe.call({
		method: "mobile_app.api.support_ticket.set_support_ticket_status",
		args: {
			mobile_app_user: frm.doc.name,
			ticket_name,
			status,
		},
		callback(r) {
			$root.find(".ma-support-inbox__status-btn").prop("disabled", false);
			if (!r.message) return;
			frm._ma_support_tickets = r.message?.tickets || [];
			update_category_filter_options(frm, frm._ma_support_tickets);
			apply_ticket_filters(frm, {
				preserve_composer: true,
				preserve_scroll: true,
			});
			frappe.show_alert({ message: __("Ticket status updated"), indicator: "green" }, 3);
		},
		error() {
			$root.find(".ma-support-inbox__status-btn").prop("disabled", false);
		},
	});
}

function delete_ticket(frm) {
	const ticket_name = frm._ma_active_ticket_name;
	if (!ticket_name) return;

	frappe.confirm(__("Delete this support ticket and its chat messages?"), () => {
		const $root = frm._ma_support_inbox;
		$root.find(".ma-support-inbox__delete-btn").prop("disabled", true);
		frappe.call({
			method: "mobile_app.api.support_ticket.delete_support_ticket",
			args: {
				mobile_app_user: frm.doc.name,
				ticket_name,
			},
			callback(r) {
				frm._ma_active_ticket_name = null;
				frm._ma_support_tickets = r.message?.tickets || [];
				update_category_filter_options(frm, frm._ma_support_tickets);
				apply_ticket_filters(frm);
				$root.find(".ma-support-inbox__delete-btn").prop("disabled", false);
				frappe.show_alert({ message: __("Support ticket deleted"), indicator: "green" }, 3);
			},
			error() {
				$root.find(".ma-support-inbox__delete-btn").prop("disabled", false);
			},
		});
	});
}

mobile_app.support_ticket_ui.render_workspace = function (frm) {
	if (frm.is_new()) return;

	const html_field = frm.fields_dict.support_ticket_workspace;
	let $host = html_field?.$wrapper?.find(".ma-support-ticket-html-root");
	if (!$host?.length) {
		$host = section_wrapper(frm, "support_ticket_section").find(".form-section-body, .section-body").first();
	}
	if (frm._ma_support_ticket_refresh_timer) {
		clearInterval(frm._ma_support_ticket_refresh_timer);
		frm._ma_support_ticket_refresh_timer = null;
	}
	$host.find(".ma-support-inbox-mount").remove();
	if (html_field?.$wrapper) {
		html_field.$wrapper.closest(".frappe-control").removeClass("hide-control");
	}

	const $mount = $('<div class="ma-support-inbox-mount"></div>');
	const $inbox = $(`
		<div class="ma-support-inbox">
			<aside class="ma-support-inbox__list-pane">
				<div class="ma-support-inbox__list-head">
					<div class="ma-support-inbox__filters">
						<select class="ma-support-inbox__filter ma-support-inbox__filter--status">
							<option value="__all">${__("All Status")}</option>
							<option value="Open">${__("Open")}</option>
							<option value="In Progress">${__("In Progress")}</option>
							<option value="Resolved">${__("Resolved")}</option>
							<option value="Closed">${__("Closed")}</option>
						</select>
						<select class="ma-support-inbox__filter ma-support-inbox__filter--category">
							<option value="__all">${__("All Categories")}</option>
						</select>
					</div>
				</div>
				<div class="ma-support-inbox__list"></div>
			</aside>
			<section class="ma-support-inbox__thread-pane is-empty">
				<div class="ma-support-inbox__thread-head">
					<div class="ma-support-inbox__thread-heading">
						<div class="ma-support-inbox__thread-title-row">
							<h4 class="ma-support-inbox__thread-title">${__("Support Ticket")}</h4>
							<span class="ma-support-inbox__thread-status">${__("Open")}</span>
						</div>
						<p class="ma-support-inbox__thread-description"></p>
					</div>
					<div class="ma-support-inbox__thread-actions">
						<button type="button" class="ma-support-inbox__action-btn ma-support-inbox__status-btn" data-status="In Progress">${__("In Progress")}</button>
						<button type="button" class="ma-support-inbox__action-btn ma-support-inbox__status-btn" data-status="Resolved">${__("Resolved")}</button>
						<button type="button" class="ma-support-inbox__action-btn ma-support-inbox__delete-btn">${__("Delete")}</button>
						<button type="button" class="ma-support-inbox__action-btn ma-support-inbox__action-btn--primary ma-support-inbox__status-btn" data-status="Closed">${__("Close")}</button>
					</div>
				</div>
				<div class="ma-support-inbox__messages">
					<div class="ma-support-inbox__thread-empty">
						<p>${__("Select a support ticket to view the conversation.")}</p>
					</div>
				</div>
				<div class="ma-support-inbox__composer">
					<div class="ma-support-inbox__composer-box">
						<div class="ma-support-inbox__composer-top">
							<span>${__("Chat")}</span>
							<span class="text-muted">${__("Reply to patient")}</span>
						</div>
						<textarea class="ma-support-inbox__composer-input" placeholder="${__(
							"Type your reply…"
						)}"></textarea>
						<div class="ma-support-inbox__composer-foot">
							<div class="ma-support-inbox__composer-tools">
								<span>⚡</span><span>🔖</span><span>😊</span>
							</div>
							<button type="button" class="ma-support-inbox__send-btn">${__("Send")}</button>
						</div>
					</div>
				</div>
			</section>
		</div>
	`);

	$mount.append($inbox);
	$host.append($mount);
	frm._ma_support_inbox = $inbox;
	frm._ma_support_ticket_filters = { status: "__all", category: "__all" };

	$inbox.find(".ma-support-inbox__send-btn").on("click", () => send_reply(frm));
	$inbox.find(".ma-support-inbox__delete-btn").on("click", () => delete_ticket(frm));
	$inbox.find(".ma-support-inbox__status-btn").on("click", (e) => {
		set_ticket_status(frm, $(e.currentTarget).data("status"));
	});
	$inbox.find(".ma-support-inbox__filter--status").on("change", (e) => {
		frm._ma_support_ticket_filters.status = $(e.currentTarget).val();
		apply_ticket_filters(frm);
	});
	$inbox.find(".ma-support-inbox__filter--category").on("change", (e) => {
		frm._ma_support_ticket_filters.category = $(e.currentTarget).val();
		apply_ticket_filters(frm);
	});
	$inbox.find(".ma-support-inbox__composer-input").on("keydown", (e) => {
		if (e.key === "Enter" && !e.shiftKey) {
			e.preventDefault();
			send_reply(frm);
		}
	});

	refresh_tickets(frm);
	start_ticket_refresh(frm);
};

mobile_app.support_ticket_ui.setup = function (frm) {
	frappe.require(SUPPORT_TICKET_CSS, () => {
		mobile_app.support_ticket_ui.render_workspace(frm);
	});
};
