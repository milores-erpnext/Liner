frappe.ui.form.on("Payment Entry", {
    refresh(frm) {
        // Only for draft documents, and only if the field is empty
        if (frm.doc.docstatus !== 0) return;
        if (frm.doc.custom_importexport_order) return;
        if (!frm.doc.references?.length) return;

        const allowed = ["Sales Invoice", "Purchase Invoice", "Sales Order", "Purchase Order"];
        const ref = frm.doc.references.find(r => allowed.includes(r.reference_doctype));
        if (!ref) return;

        frappe.db
            .get_value(ref.reference_doctype, ref.reference_name, "custom_importexport_order")
            .then(r => {
                const value = r.message && r.message.custom_importexport_order;
                if (value) {
                    frm.set_value("custom_importexport_order", value);
                }
            });
    }
});