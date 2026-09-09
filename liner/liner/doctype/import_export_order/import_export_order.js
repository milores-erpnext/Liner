// Copyright (c) 2026, Milores and contributors
// For license information, please see license.txt

const status_colors = {
    "Draft": "gray",
    "Ordered": "orange",
    "Shipped": "blue",
    "Completed": "green",
    "Cancelled": "red"
    // IMPORTANT: these keys must exactly match the options in your
    // "status" Select field (case-sensitive). Adjust as needed.
};

frappe.ui.form.on("Import-Export Order", {
    refresh(frm) {
        // frm.add_custom_button(__("Attach XML"), () => {
        //     open_xml_upload_dialog(frm);
        // });
        frm.add_custom_button(__("Sales Invoice"), () => make_sales_invoice(frm), __("Create"));
        frm.add_custom_button(__("Payment Entry"), () => make_payment_entry(frm), __("Create"));
        frm.add_custom_button(__("Delivery Note"), () => make_delivery_note(frm), __("Create"));
        frm.add_custom_button(__("Gate Pass"), () => make_gate_pass(frm), __("Create"));
        frm.add_custom_button(__("Purchase Invoice"), () => make_purchase_invoice(frm), __("Create"));

        // standard native indicator — left untouched, not clickable,
        // behaves exactly like any other Frappe doctype
        set_status_indicator(frm);

        // separate custom dropdown button group for changing status,
        // grouped under "Status" the same way "Create" groups its buttons
        add_status_dropdown(frm);
    },
    onload(frm) {
        if (frm.is_new()) {
            frm.set_value("company", null);

            // default status on a new doc, same as CRM Lead defaulting to "New"
            if (!frm.doc.status) {
                frm.set_value("status", "Draft");
            }
        }
    },
    setup(frm) {
        frm.set_query("project", function () {
            return {
                filters: {
                    company: frm.doc.company
                }
            };
        });
    },
    get_local_freight_charges(frm) {
        frappe.call({
            method: "liner.liner.doctype.import_export_order.import_export_order.get_local_freight_charges",
            args: {
                import_export_order: frm.doc.name
            },
            freeze: true,
            freeze_message: __("Fetching Local Freight Charges..."),
            callback: function (r) {
                if (!r.exc) {
                    frm.reload_doc();

                    frappe.show_alert({
                        message: __("Local Freight Charges imported successfully."),
                        indicator: "green"
                    });
                }
            }
        });
    }
});

function open_xml_upload_dialog(frm) {
    const dialog = new frappe.ui.Dialog({
        title: __("Attach XML"),
        fields: [
            {
                fieldname: "xml_file",
                fieldtype: "HTML",
                options: `
                    <input type="file" id="xml_file_input" accept=".xml,text/xml">
                `,
            },
        ],
        primary_action_label: __("Import"),
        primary_action() {
            const input = dialog.$wrapper.find("#xml_file_input")[0];
            const file = input.files[0];

            if (!file) {
                frappe.msgprint(__("Please select an XML file."));
                return;
            }

            const reader = new FileReader();

            reader.onload = function (e) {
                frappe.call({
                    method: "liner.liner.doctype.import_export_order.import_export_order.import_xml",
                    args: {
                        xml_text: e.target.result,
                    },
                    callback(r) {
                        if (r.message) {
                            Object.entries(r.message).forEach(([field, value]) => {
                                frm.set_value(field, value);
                            });

                            frm.refresh_fields();

                            frappe.show_alert({
                                message: __("XML imported successfully."),
                                indicator: "green",
                            });
                        }

                        dialog.hide();
                    },
                });
            };

            reader.onerror = function () {
                frappe.msgprint(__("Could not read the XML file."));
            };

            reader.readAsText(file);
        },
    });

    dialog.show();
}

function set_status_indicator(frm) {
    if (!frm.doc.status) return;
    let color = status_colors[frm.doc.status] || "gray";
    frm.page.set_indicator(__(frm.doc.status), color);
}

function add_status_dropdown(frm) {
    // clear any previously added buttons in this group so refresh()
    // running again doesn't duplicate entries. Wrapped in try/catch since
    // some Frappe versions throw if the group doesn't exist yet.
    try {
        Object.keys(status_colors).forEach((s) => {
            frm.page.remove_inner_button(__(s), __("Status"));
        });
    } catch (e) {
        console.warn("remove_inner_button failed (safe to ignore on first load):", e);
    }

    Object.keys(status_colors).forEach((s) => {
        frm.add_custom_button(
            __(s),
            () => set_status(frm, s),
            __("Status")
        );
    });

    // NOTE: relabeling the group button to show current status is removed
    // for now to isolate whether the dropdown itself renders correctly.
    // We'll add that back once this is confirmed working.
}

function set_status(frm, new_status) {
    if (frm.is_new()) {
        // doc doesn't exist in the DB yet — just update the field locally,
        // don't force a full save (which would trigger mandatory-field
        // validation before the user is ready)
        frm.set_value("status", new_status);
    } else {
        frm.set_value("status", new_status).then(() => frm.save());
    }
}

function make_sales_invoice(frm) {
    frappe.model.open_mapped_doc({
        method: "liner.liner.doctype.import_export_order.import_export_order.make_sales_invoice",
        frm: frm
    });
}

function make_payment_entry(frm) {
    frappe.model.open_mapped_doc({
        method: "liner.liner.doctype.import_export_order.import_export_order.make_payment_entry",
        frm: frm
    });
}
