frappe.ui.form.on("Diet Chart", {
    refresh(frm) {
        const field = frm.fields_dict.custom_diet_chart_pdf;
        if (field) {
            field.df.options = {
                restrictions: { allowed_file_types: [".pdf"] },
            };
        }
    },
});
