// Copyright (c) 2026, Milores and contributors
// For license information, please see license.txt

frappe.listview_settings["Import-Export Order"] = {
    onload(listview) {
        listview.page.add_inner_button(__("Attach XML"), () => {
            open_xml_upload_dialog(listview);
        });
    },
};

function open_xml_upload_dialog(listview) {
    const dialog = new frappe.ui.Dialog({
        title: __("Attach XML"),
        fields: [
            {
                fieldname: "order_type",
                label: __("Order Type"),
                fieldtype: "Select",
                options: "\nImport\nExport",
                reqd: 1,
            },
            {
                fieldname: "xml_file",
                fieldtype: "HTML",
                options: `<input type="file" id="xml_file_input" accept=".xml,text/xml">`,
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
                    method: "liner.liner.doctype.import_export_order.import_export_order.import_xml_bulk",
                    args: { xml_text: e.target.result, order_type: dialog.get_value("order_type") },
                    freeze: true,
                    freeze_message: __("Importing bookings..."),
                    callback(r) {
                        dialog.hide();
                        if (!r.message) return;

                        const { created = [], skipped = [], errors = [] } = r.message;
                        let summary = __("Created {0} Import-Export Order(s).", [created.length]);
                        if (skipped.length) {
                            summary += " " + __("Skipped {0} already-imported booking(s): {1}", [
                                skipped.length, skipped.join(", "),
                            ]);
                        }
                        if (errors.length) {
                            summary += " " + __("{0} booking(s) failed, check Error Log: {1}", [
                                errors.length, errors.join(", "),
                            ]);
                        }

                        frappe.msgprint({
                            title: __("XML Import Complete"),
                            message: summary,
                            indicator: errors.length ? "orange" : "green",
                        });

                        listview.refresh();
                    },
                });
            };
            reader.onerror = () => frappe.msgprint(__("Could not read the XML file."));
            reader.readAsText(file);
        },
    });

    dialog.show();
}
