/* App Support Ticket list - patient identity first column */

function ast_escape(value) {
	return frappe.utils.escape_html(value == null || value === "" ? "" : String(value));
}

function ast_initials(name) {
	const parts = String(name || "").trim().split(/\s+/).filter(Boolean);
	return ((parts[0]?.[0] || "U") + (parts[1]?.[0] || "")).toUpperCase();
}

function ast_patient_key(doc) {
	return doc.mobile_app_user || doc.user_id || "";
}

function ast_identity_html(doc, image) {
	const patient_name = doc.user_name || doc.mobile_app_user || doc.user_id || __("Unknown User");
	const avatar = image
		? `<img class="ast-ticket-user__avatar"
		 style="width: 35px !important; height: 35px !important; border-radius: 5px;"
		 src="${ast_escape(image)}" alt="">`
		: `<span class="ast-ticket-user__avatar ast-ticket-user__avatar--fallback">${ast_escape(
			ast_initials(patient_name)
		)}</span>`;

	return `
		<div class="ast-ticket-user" data-patient="${ast_escape(ast_patient_key(doc))}">
			${avatar}
			<span class="ast-ticket-user__text">
				<strong>${ast_escape(patient_name)}</strong>
			</span>
		</div>
	`;
}

function ast_subject_column_html(doc) {
	const href = `/app/app-support-ticket/${encodeURIComponent(doc.name)}`;
	return `
		<div class="list-row-col hidden-xs ellipsis ast-ticket-subject-col">
			<a class="filterable ellipsis" href="${ast_escape(href)}" title="${ast_escape(doc.subject)}">
				${ast_escape(doc.subject || "-")}
			</a>
		</div>
	`;
}

function ast_open_ticket_chat(doc) {
	const mobile_app_user = doc.mobile_app_user || doc.user_id;
	if (!mobile_app_user) {
		frappe.msgprint(__("No Mobile App User is linked to this ticket."));
		return;
	}

	frappe.route_options = {
		open_support_ticket_chat: 1,
		support_ticket_name: doc.name,
	};
	frappe.set_route("Form", "Mobile App User", mobile_app_user);
}

function ast_prepare_header(listview) {
	const $header = listview.$result.find(".list-row-head").first();
	const $subject_header = $header.find(".list-row-col.list-subject").first();
	$subject_header.find("span[data-sort-by='subject']").text(__("User"));

	if (!$header.find(".ast-ticket-subject-col").length) {
		$subject_header.after(`
			<div class="list-row-col hidden-xs ellipsis ast-ticket-subject-col">
				<span data-sort-by="subject" title="${__("Click to sort by {0}", [__("Subject")])}">
					${__("Subject")}
				</span>
			</div>
		`);
	}

	$header.find("span[data-sort-by='name']").closest(".list-row-col").hide();
	$header
		.find(".list-row-col")
		.filter(function () {
			return $(this).text().trim() === __("ID");
		})
		.last()
		.hide();
}

function ast_render_identity_cells(listview, images = {}) {
	listview.$result.addClass("ast-ticket-list");
	ast_prepare_header(listview);

	listview.$result.find(".list-row-container").each((index, row) => {
		const doc = listview.data[index];
		if (!doc) return;

		$(row).addClass("ast-ticket-row");
		$(row).data("ast-doc", doc);
		const $subject = $(row).find(".list-row-col.list-subject").first();
		$(row).find(".ast-ticket-subject-col").remove();
		$subject.after(ast_subject_column_html(doc));

		$(row)
			.find(".list-row-col")
			.filter(function () {
				return $(this).text().trim() === doc.name;
			})
			.last()
			.hide();

		const $link = $subject.find('a[data-doctype="App Support Ticket"]').first();
		if (!$link.length) return;

		$link.removeClass("ellipsis");
		$link.html(ast_identity_html(doc, images[doc.mobile_app_user]));
	});

	listview.$result.off("click.ast-ticket-chat").on("click.ast-ticket-chat", ".ast-ticket-row .list-row", function (e) {
		if ($(e.target).closest(".list-row-checkbox, .like-action, .comment-count, .list-assignments").length) {
			return;
		}

		e.preventDefault();
		e.stopPropagation();
		const doc = $(this).closest(".ast-ticket-row").data("ast-doc");
		if (doc) ast_open_ticket_chat(doc);
	});
}

function ast_hydrate_avatars(listview) {
	const docs = listview?.data || [];
	const patient_names = [...new Set(docs.map((doc) => doc.mobile_app_user).filter(Boolean))];
	if (!patient_names.length) return;

	frappe.db
		.get_list("Mobile App User", {
			fields: ["name", "image"],
			filters: [["name", "in", patient_names]],
			limit: patient_names.length,
		})
		.then((rows) => {
			const images = Object.fromEntries((rows || []).map((row) => [row.name, row.image]));
			ast_render_identity_cells(listview, images);
		});
}

frappe.listview_settings["App Support Ticket"] = {
	add_fields: ["user_name", "user_id", "mobile_app_user", "subject"],

	refresh(listview) {
		ast_render_identity_cells(listview);
		ast_hydrate_avatars(listview);
	},
};
