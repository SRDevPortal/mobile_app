/* Shared support conversation UI, mounted on the App Support Ticket list. */

frappe.provide("mobile_app.support_ticket_ui");

const SUPPORT_TICKET_CSS = "/assets/mobile_app/css/mobile_app_support_ticket.css";
const SUPPORT_TICKET_REFRESH_MS = 60000;

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
	const values = frm._ma_global_inbox && frm._ma_support_categories
		? frm._ma_support_categories : tickets.map((ticket) => ticket.label);
	// Keep a chosen category selected even when the search has no matches.
	const categories = [...new Set([...values, ...(frm._ma_global_inbox && current !== "__all" ? [current] : [])].filter(Boolean))].sort();
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

	const previous_top = node?.scrollTop || 0;
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
	} else $messages.scrollTop(previous_top);
}


function conversation_key(ticket) {
    return `${ticket.ticket_doctype || "App Support Ticket"}:${ticket.name}`;
}

function conversation_state(frm, ticket) {
    frm._ma_conversations ||= new Map();
    const key = conversation_key(ticket);
    if (!frm._ma_conversations.has(key)) frm._ma_conversations.set(key, {messages: [], loaded: false, draft: ""});
    return frm._ma_conversations.get(key);
}

function active_is(frm, ticket) {
    return frm._ma_active_ticket_name === ticket.name && frm._ma_active_ticket_doctype === ticket.ticket_doctype;
}

function merge_summary(frm, summary) {
    if (!summary) return;
    const ticket = (frm._ma_support_tickets || []).find(t => conversation_key(t) === conversation_key(summary));
    if (ticket) Object.assign(ticket, summary);
}

function show_conversation(frm, ticket, state, older = false) {
    if (!active_is(frm, ticket)) return;
    const $messages = frm._ma_support_inbox.find(".ma-support-inbox__messages");
    const node = $messages[0], top = node.scrollTop, height = node.scrollHeight;
    render_messages($messages, {...ticket, messages: state.messages}, {preserve_scroll: state.shown});
    if (state.has_older) {
        $('<button type="button" class="btn btn-default btn-sm ma-load-older"></button>')
            .text(__("Load older messages")).prop("disabled", Boolean(state.loading))
            .on("click", () => load_conversation(frm, ticket, true)).prependTo($messages);
    }
    if (older) node.scrollTop = top + node.scrollHeight - height;
    else if (state.restore_scroll != null) {node.scrollTop = state.restore_scroll; delete state.restore_scroll;}
    state.shown = true;
}

function load_conversation(frm, ticket, older = false) {
    const state = conversation_state(frm, ticket);
    if (state.loading) {if (!older) state.refresh_again = true; return;}
    if (older && !state.has_older) return;
    const was_loaded = state.loaded;
    const cursor = older ? state.messages[0]?.name : (state.loaded ? state.messages.at(-1)?.name : null);
    state.loading = true;
    frm._ma_support_inbox.find(".ma-load-older").prop("disabled", true);
    frappe.call({
        method: "mobile_app.api.desk_support.messages",
        args: {mobile_app_user: ticket.mobile_app_user, ticket_name: ticket.name, ticket_doctype: ticket.ticket_doctype,
            before: older ? cursor : null, after: older ? null : cursor},
        callback(r) {
            state.loading = false;
            if (!r.message) return;
            const data = r.message, rows = data.messages || [];
            const merged = new Map(state.messages.map(message => [message.name, message]));
            rows.forEach(message => merged.set(message.name, message));
            state.messages = [...merged.values()].sort((a,b) =>
                String(a.creation).localeCompare(String(b.creation)) || a.name.localeCompare(b.name));
            if (older || !was_loaded) state.has_older = data.has_more;
            state.loaded = true;
            merge_summary(frm, data.ticket);
            if (active_is(frm, ticket)) {
                show_conversation(frm, ticket, state, older);
                mark_ticket_seen(frm, ticket);
            }
            // Drain a burst in bounded pages without dropping messages or replacing older pages.
            if ((cursor && !older && data.has_more) || state.refresh_again) {
                state.refresh_again = false;
                if (active_is(frm, ticket) && !document.hidden) load_conversation(frm, ticket);
            }
        },
        error() {
            state.loading = false;
            state.refresh_again = false;
            if (!active_is(frm, ticket)) return;
            show_conversation(frm, ticket, state);
            $('<button type="button" class="btn btn-default btn-sm"></button>')
                .text(__("Could not load messages. Retry")).on("click", () => load_conversation(frm, ticket, older))
                .prependTo(frm._ma_support_inbox.find(".ma-support-inbox__messages"));
        }
    });
}

function mark_ticket_seen(frm, ticket) {
    if (!frm._ma_global_inbox || !ticket.can_mark_read || !ticket.unread_count || document.hidden) return;
    const state = conversation_state(frm, ticket);
    const through = state.messages.at(-1)?.name;
    if (!through || state.reading || state.read_through === through) return;
    state.reading = true;
    frappe.call({
        method: "mobile_app.api.desk_support.read",
        args: {mobile_app_user: ticket.mobile_app_user, ticket_name: ticket.name, ticket_doctype: ticket.ticket_doctype, through},
        callback(r) {
            state.reading = false;
            state.read_through = through;
            merge_summary(frm, r.message?.ticket);
            const $item = frm._ma_support_inbox.find(".ma-support-inbox__item").filter((_, node) =>
                $(node).data("ticket") === ticket.name && $(node).data("ticket-doctype") === ticket.ticket_doctype);
            $item.find(".ma-support-inbox__unread-badge").remove();
            if (ticket.unread_count) $item.append($('<span class="ma-support-inbox__unread-badge"></span>').text(ticket.unread_count));
            if (active_is(frm, ticket) && state.messages.at(-1)?.name !== through) mark_ticket_seen(frm, ticket);
        },
        error() {state.reading = false;}
    });
}

function select_ticket(frm, ticket, opts = {}) {
	if (frm._ma_support_sending) return;
    const changed = frm._ma_displayed_key !== conversation_key(ticket);
    if (frm._ma_global_inbox && changed && frm._ma_displayed_ticket) {
        const previous = frm._ma_displayed_ticket;
        const previous_state = conversation_state(frm, previous);
        previous_state.draft = frm._ma_support_inbox.find(".ma-support-inbox__composer-input").val() || "";
        previous_state.scroll = frm._ma_support_inbox.find(".ma-support-inbox__messages").scrollTop();
    }
	if (frm._ma_active_ticket_name !== ticket?.name || frm._ma_active_ticket_doctype !== ticket?.ticket_doctype) opts = {...opts, preserve_composer: false};
	frm._ma_displayed_key = conversation_key(ticket);
    frm._ma_displayed_ticket = ticket;
	frm._ma_active_ticket_user = ticket?.mobile_app_user || frm.doc.name;
	frm._ma_active_ticket_name = ticket?.name;
	frm._ma_active_ticket_doctype = ticket?.ticket_doctype || "App Support Ticket";
	const $root = frm._ma_support_inbox;
	if (!$root?.length) return;

	$root.find(".ma-support-inbox__item").removeClass("is-active");
	$root.find(".ma-support-inbox__item").filter((_, node) =>
		$(node).data("ticket") === ticket.name && $(node).data("ticket-doctype") === frm._ma_active_ticket_doctype
	).addClass("is-active");

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
    if (frm._ma_global_inbox) {
        const state = conversation_state(frm, ticket);
        if (changed) {
            state.shown = false;
            state.restore_scroll = state.scroll;
            $input.val(state.draft);
        }
        if (state.loaded) show_conversation(frm, ticket, state);
        else $messages.empty().append($("<p class='p-3 text-muted'></p>").text(__("Loading messages…")));
        load_conversation(frm, ticket);
    } else render_messages($messages, ticket, { preserve_scroll: opts.preserve_scroll });
	if (!frm._ma_global_inbox && !opts.preserve_composer) {
		$input.val("");
	}
	$input.prop("disabled", is_closed);
	$root.find(".ma-support-inbox__send-btn").prop("disabled", is_closed);
	mark_ticket_seen(frm, ticket);
}

function render_ticket_list(frm, tickets, opts = {}) {
	const $root = frm._ma_support_inbox;
	const $list = $root.find(".ma-support-inbox__list");
	$list.empty();

    if (!tickets.length) {
        if (frm._ma_global_inbox && frm._ma_active_ticket_name) {
            const state = conversation_state(frm, {name: frm._ma_active_ticket_name, ticket_doctype: frm._ma_active_ticket_doctype});
            state.draft = $root.find(".ma-support-inbox__composer-input").val() || "";
            state.scroll = $root.find(".ma-support-inbox__messages").scrollTop();
        }
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

	const profile_patient = mobile_app.clinical_ui?.get_patient_display_name
		? mobile_app.clinical_ui.get_patient_display_name(frm)
		: frm.doc.full_name || frm.docname;
	tickets.forEach((ticket) => {
		const patient = ticket.user_name || profile_patient;
		const $item = $('<div class="ma-support-inbox__item"></div>').data("ticket", ticket.name)
			.data("ticket-doctype", ticket.ticket_doctype || "App Support Ticket");
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
	const active = tickets.find((t) => t.name === active_name && (!frm._ma_active_ticket_doctype || t.ticket_doctype === frm._ma_active_ticket_doctype)) || (opts.select_first === false ? null : tickets[0]);
	if (active) {
		select_ticket(frm, active, opts);
	}
}

function refresh_tickets(frm, opts = {}) {
	if (frm.is_new() || frm._ma_support_sending) return;
	if (frm._ma_support_ticket_refreshing) { frm._ma_refresh_again = true; return; }
	const $root = frm._ma_support_inbox;
	if (!$root?.length || !document.body.contains($root[0])) return;

    const query_key = () => JSON.stringify([frm._ma_start, frm._ma_search, frm._ma_ticket_name, frm._ma_ticket_doctype, frm._ma_support_ticket_filters]);
    const requested_key = query_key();
	frm._ma_support_ticket_refreshing = true;
	frappe.call({
		method: frm._ma_global_inbox ? "mobile_app.api.desk_support.inbox" : "mobile_app.api.support_ticket.get_support_tickets",
		args: frm._ma_global_inbox ? {start: frm._ma_start || 0, status: frm._ma_support_ticket_filters?.status, category: frm._ma_support_ticket_filters?.category, search: frm._ma_search || "", ticket_name: frm._ma_ticket_name || null, ticket_doctype: frm._ma_ticket_doctype || null} : { mobile_app_user: frm.doc.name },
		callback(r) {
			try {
                if (requested_key !== query_key()) {frm._ma_refresh_again = true; return;}
				$root.find(".ma-support-inbox__load-error").remove();
				frm._ma_page_update?.(r.message);
				frm._ma_support_tickets = r.message?.tickets || [];
				frm._ma_support_categories = r.message?.categories;
				update_category_filter_options(frm, frm._ma_support_tickets);
				apply_ticket_filters(frm, opts);
			} finally {
				frm._ma_support_ticket_refreshing = false;
				if (frm._ma_refresh_again) { frm._ma_refresh_again = false; refresh_tickets(frm, {...opts, preserve_composer: true, preserve_scroll: true}); }
			}
		},
		error() {
			frm._ma_support_ticket_refreshing = false;
			if (!$root.find(".ma-support-inbox__load-error").length) {
				$root.find(".ma-support-inbox__list-pane").prepend($('<div class="ma-support-inbox__load-error text-danger p-3"></div>').text(__("Could not load tickets. Retrying?")));
			}
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
		if (document.hidden || !$root.is(":visible")) return;
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
	if (body === "" || !ticket_name || frm._ma_support_sending) return;
	frm._ma_support_sending = true;

	$root.find(".ma-support-inbox__send-btn").prop("disabled", true);
	frappe.call({
		method: frm._ma_global_inbox ? "mobile_app.api.desk_support.reply" : "mobile_app.api.support_ticket.send_support_reply",
		args: {
			mobile_app_user: frm._ma_active_ticket_user || frm.doc.name,
			ticket_name,
			ticket_doctype: frm._ma_active_ticket_doctype || "App Support Ticket",
			message: body,
		},
		callback(r) {
			frm._ma_support_sending = false;
			$root.find(".ma-support-inbox__send-btn").prop("disabled", false);
			if (!r.message) return;
			$input.val("");
			if (frm._ma_global_inbox) {
                merge_summary(frm, r.message.ticket);
                const ticket = frm._ma_support_tickets.find(t => t.name === ticket_name && t.ticket_doctype === frm._ma_active_ticket_doctype);
                if (ticket) {conversation_state(frm, ticket).draft = ""; apply_ticket_filters(frm, {preserve_composer: true, preserve_scroll: true});}
                return;
            }
			frm._ma_support_tickets = r.message?.tickets || [];
			update_category_filter_options(frm, frm._ma_support_tickets);
			apply_ticket_filters(frm, { preserve_scroll: false });
		},
		error() {
			frm._ma_support_sending = false;
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
		method: frm._ma_global_inbox ? "mobile_app.api.desk_support.status" : "mobile_app.api.support_ticket.set_support_ticket_status",
		args: {
			mobile_app_user: frm._ma_active_ticket_user || frm.doc.name,
			ticket_name,
			ticket_doctype: frm._ma_active_ticket_doctype || "App Support Ticket",
			status,
		},
		callback(r) {
			$root.find(".ma-support-inbox__status-btn").prop("disabled", false);
			if (!r.message) return;
			if (frm._ma_global_inbox) {
                merge_summary(frm, r.message.ticket);
                apply_ticket_filters(frm, {preserve_composer: true, preserve_scroll: true});
                return;
            }
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
				mobile_app_user: frm._ma_active_ticket_user || frm.doc.name,
				ticket_name,
				ticket_doctype: frm._ma_active_ticket_doctype || "App Support Ticket",
			},
			callback(r) {
				frm._ma_active_ticket_name = null;
				if (frm._ma_global_inbox) {
					$root.find(".ma-support-inbox__delete-btn").prop("disabled", false);
					return refresh_tickets(frm);
				}
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
	let $host = frm._ma_inbox_host || html_field?.$wrapper?.find(".ma-support-ticket-html-root");
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
							<option value="Waiting for Customer">${__("Waiting for Customer")}</option>
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
		if (frm._ma_global_inbox) { frm._ma_start = 0; refresh_tickets(frm); }
		else apply_ticket_filters(frm);
	});
	$inbox.find(".ma-support-inbox__filter--category").on("change", (e) => {
		frm._ma_support_ticket_filters.category = $(e.currentTarget).val();
		if (frm._ma_global_inbox) { frm._ma_start = 0; refresh_tickets(frm); }
		else apply_ticket_filters(frm);
	});
	$inbox.find(".ma-support-inbox__composer-input").on("keydown", (e) => {
		if (e.key === "Enter" && !e.shiftKey) {
			e.preventDefault();
			send_reply(frm);
		}
	});

	refresh_tickets(frm);
	start_ticket_refresh(frm);
    if (frm._ma_global_inbox) mobile_app.realtime?.watch?.("support-conversations",
        ["Support Ticket", "Support Ticket Message", "App Support Ticket", "App Support Ticket Message"],
        () => !document.hidden && Boolean(frm._ma_support_inbox?.is(":visible")),
        () => {
            if (frm._ma_support_sending || frm._ma_support_ticket_refreshing) return false;
            refresh_tickets(frm, {preserve_composer: true, preserve_scroll: true});
        });

};

mobile_app.support_ticket_ui.setup = function (frm) {
	frappe.require(SUPPORT_TICKET_CSS, () => {
		mobile_app.support_ticket_ui.render_workspace(frm);
	});
};

mobile_app.support_ticket_ui.refresh = refresh_tickets;
