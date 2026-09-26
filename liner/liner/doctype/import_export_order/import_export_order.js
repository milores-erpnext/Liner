// Copyright (c) 2026, Milores and contributors
// For license information, please see license.txt

const status_colors = {
    "Draft": "gray",       // Not started
    "IFD": "blue",        // Discharged from vessel
    "DCO": "orange",      // Delivery order collected
    "EMM": "green",       // Empty returned
    "DSO": "purple",      // Released for export
    "BFF": "darkgreen"    // Loaded on vessel
};

frappe.ui.form.on("Import-Export Order", {
	refresh(frm) {
		// frm.add_custom_button(__("Attach XML"), () => {
		//     open_xml_upload_dialog(frm);
		// });
		frm.add_custom_button(__("Manifest Download"), () => download_manifest_xml(frm));
		frm.add_custom_button(__("Arrival Notice"), () => {
			make_arrival_notice(frm);
		});
		frm.add_custom_button(__("EDI Connect with CTC"));
		frm.add_custom_button(__("Delivery Order"), function () {
			show_delivery_order_dialog(frm);
		}, __("Print"));
		frm.add_custom_button(__("Gate Pass"), function () {
			make_gate_pass(frm);
		}, __("Print"));
		frm.add_custom_button(__("Payment Entry"), () => make_payment_entry(frm),__("Create"));
		// frm.add_custom_button(__("Detection sales invoice"), () => make_payment_entry1(frm),__("Create"));
		// frm.add_custom_button(__("Other Sales Invoice"), () => make_payment_entry1(frm),__("Create"));
		// frm.add_custom_button(__("Freight Sales Invoice"), () => make_payment_entry1(frm),__("Create"));
		frm.add_custom_button(__("Purchase Invoice"), () => make_payment_entry1(frm),__("Create"));
		frm.add_custom_button(__("Liner Purchase Invoice"), () => make_payment_entry1(frm),__("Create"));
		frm.add_custom_button(__("Inter-Company Purchase Invoice"), () => make_payment_entry1(frm),__("Create"));

		// standard native indicator — left untouched, not clickable,
		// behaves exactly like any other Frappe doctype
		set_status_indicator(frm);

		// separate custom dropdown button group for changing status,
		// grouped under "Status" the same way "Create" groups its buttons
		add_status_dropdown(frm);
	},
	onload(frm) {
	    if (frm.is_new()) {
	        // frm.set_value("company", null);

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
	},
	create_freight_charges: function(frm) {

		if (!frm.doc.customer) {
			frappe.msgprint("Customer is missing.");
			return;
		}

		if (!frm.doc.company) {
			frappe.msgprint("Company is missing.");
			return;
		}

		if (!frm.doc.project) {
			frappe.msgprint("Project is missing.");
			return;
		}

		if (!frm.doc.grand_total || flt(frm.doc.grand_total) <= 0) {
			frappe.msgprint("Grand Total must be greater than 0.");
			return;
		}

		if (!frm.doc.contract_agreement || !frm.doc.contract_agreement.length) {
			frappe.msgprint(
				"No Contract Agreement found."
			);
			return;
		}

		let contract_row = frm.doc.contract_agreement.find(
			row => row.item_code
		);

		if (!contract_row) {
			frappe.msgprint(
				"Item Code is missing in Contract Agreement table."
			);
			return;
		}

		frappe.confirm(
			`Create Sales Invoice for freight charges`,
			function() {

				frappe.call({
					method: "liner.liner.doctype.import_export_order.import_export_order.create_freight_charges_sales_invoice",

					args: {
						import_export_order: frm.doc.name
					},

					freeze: true,
					freeze_message: "Creating Sales Invoice...",

					callback: function(r) {

						if (!r.message) {
							return;
						}

						frappe.show_alert({
							message:
								`Sales Invoice ${r.message.name} created successfully.`,
							indicator: "green"
						});

						frappe.set_route(
							"Form",
							"Sales Invoice",
							r.message.name
						);
					}
				});
			}
		);
	},
	create_liner_charges: function(frm) {

		if (!frm.doc.line) {
			frappe.msgprint("Line is missing.");
			return;
		}

		if (!frm.doc.company) {
			frappe.msgprint("Company is missing.");
			return;
		}

		if (!frm.doc.project) {
			frappe.msgprint("Project is missing.");
			return;
		}

		if (!frm.doc.total_liner || flt(frm.doc.total_liner) <= 0) {
			frappe.msgprint("Total Liner must be greater than 0.");
			return;
		}

		if (!frm.doc.contract_agreement || !frm.doc.contract_agreement.length) {
			frappe.msgprint(
				"No Contract Agreement found."
			);
			return;
		}

		let contract_row = frm.doc.contract_agreement.find(
			row => row.item_code
		);

		if (!contract_row) {
			frappe.msgprint(
				"Item Code is missing in Contract Agreement table."
			);
			return;
		}

		frappe.confirm(
			`Create Purchase Invoice for Liner charges`,
			function() {

				frappe.call({
					method: "liner.liner.doctype.import_export_order.import_export_order.create_liner_charges_purchase_invoice",

					args: {
						import_export_order: frm.doc.name
					},

					freeze: true,
					freeze_message: "Creating Purchase Invoice...",

					callback: function(r) {

						if (!r.message) {
							return;
						}

						frappe.show_alert({
							message:
								`Purchase Invoice ${r.message.name} created successfully.`,
							indicator: "green"
						});

						frappe.set_route(
							"Form",
							"Purchase Invoice",
							r.message.name
						);
					}
				});
			}
		);
	},
	create_other_charges: function(frm) {

		if (!frm.doc.customer) {
			frappe.msgprint("Customer is missing.");
			return;
		}

		if (!frm.doc.company) {
			frappe.msgprint("Company is missing.");
			return;
		}

		if (!frm.doc.project) {
			frappe.msgprint("Project is missing.");
			return;
		}

		if (!frm.doc.other_charges || !frm.doc.other_charges.length) {
			frappe.msgprint(
				"No Other Charges found."
			);
			return;
		}

		// Check that at least one valid row exists
		let other_charge_row = frm.doc.other_charges.find(
			row => row.qty && flt(row.rate) > 0
		);

		if (!other_charge_row) {
			frappe.msgprint(
				"No valid items found in Other Charges table."
			);
			return;
		}

		frappe.confirm(
			`Create Sales Invoice for Other Charges`,
			function() {

				frappe.call({
					method: "liner.liner.doctype.import_export_order.import_export_order.create_other_charges_sales_invoice",

					args: {
						import_export_order: frm.doc.name
					},

					freeze: true,
					freeze_message: "Creating Sales Invoice...",

					callback: function(r) {

						if (!r.message) {
							return;
						}

						frappe.show_alert({
							message:
								`Sales Invoice ${r.message.name} created successfully.`,
							indicator: "green"
						});

						frappe.set_route(
							"Form",
							"Sales Invoice",
							r.message.name
						);
					}
				});
			}
		);
	},
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

async function make_arrival_notice(frm) {
	const warehouse_name = "Port";

	// ---------------------------------------------------------
	// Check Warehouse
	// ---------------------------------------------------------
	let warehouses = await frappe.db.get_list("Warehouse", {
		filters: {
			warehouse_name: warehouse_name,
			company: frm.doc.company
		},
		fields: ["name"],
		limit: 1
	});

	let warehouse = warehouses.length
		? warehouses[0].name
		: null;


	// ---------------------------------------------------------
	// Create Warehouse if not available
	// ---------------------------------------------------------
	if (!warehouse) {

		let warehouse_doc = await frappe.call({
			method: "frappe.client.insert",
			args: {
				doc: {
					doctype: "Warehouse",
					warehouse_name: warehouse_name,
					company: frm.doc.company
				}
			}
		});

		warehouse = warehouse_doc.message.name;
	}


	// ---------------------------------------------------------
	// Create Stock Entry
	// ---------------------------------------------------------
	let stock_entry = frappe.model.get_new_doc("Stock Entry");

	stock_entry.stock_entry_type = "Material Receipt";
	stock_entry.company = frm.doc.company;


	// ---------------------------------------------------------
	// Add Items
	// ---------------------------------------------------------
	frm.doc.table_tvep.forEach(row => {

		if (!row.container_no) {
			return;
		}

		let item = frappe.model.add_child(
			stock_entry,
			"Stock Entry Detail",
			"items"
		);

		item.item_code = row.container_no;
		item.qty = 1;
		item.t_warehouse = warehouse;
		item.allow_zero_valuation_rate = 1;
	});


	// ---------------------------------------------------------
	// Save Stock Entry
	// ---------------------------------------------------------
	let saved_stock_entry = await frappe.call({
		method: "frappe.client.insert",
		args: {
			doc: stock_entry
		}
	});
}
 
function show_delivery_order_dialog(frm) {

	const containers = frm.doc.table_tvep || [];

	if (!containers.length) {
		frappe.msgprint({
			title: __("No Containers"),
			message: __("There are no containers available in this Import-Export Order."),
			indicator: "orange"
		});
		return;
	}

	const rows_html = containers.map((row, index) => {

		const is_printed = cint(row.delivery_order_printed) === 1;

		return `
			<tr>
				<td style="
					width:50px;
					text-align:center;
					vertical-align:middle;
				">
					<input
						type="checkbox"
						class="delivery-container-checkbox"
						data-index="${index}"
						${is_printed ? "disabled" : ""}
					>
				</td>

				<td style="vertical-align:middle;">
					<strong>
						${frappe.utils.escape_html(row.container_no || "")}
					</strong>
				</td>

				<td style="vertical-align:middle;">
					${frappe.utils.escape_html(row.c_type || "")}
				</td>

				<td style="
					width:120px;
					text-align:center;
					vertical-align:middle;
					font-weight:bold;
					color:${is_printed ? "#28a745":"#888"};
				">
					${is_printed ? __("Printed") : __("Not Printed")}
				</td>
			</tr>
		`;

	}).join("");


	const dialog = new frappe.ui.Dialog({

		title: __("Select Containers for Delivery Order"),

		size: "large",

		fields: [
			{
				fieldtype: "HTML",
				fieldname: "container_list"
			}
		],

		primary_action_label: __("Print Delivery Order"),

		primary_action: function () {

			const selected_containers = [];

			dialog.$wrapper
				.find(".delivery-container-checkbox:checked")
				.each(function () {

					const index = parseInt(
						$(this).attr("data-index"),
						10
					);

					const row = containers[index];

					if (!row) {
						return;
					}

					// Do not allow already printed containers
					if (cint(row.delivery_order_printed) === 1) {
						return;
					}

					selected_containers.push({
						name: row.name,
						container_no: row.container_no,
						c_type: row.c_type
					});
				});


			if (!selected_containers.length) {

				frappe.msgprint({
					title: __("No Container Selected"),
					message: __("Please select at least one container that has not been printed."),
					indicator: "orange"
				});

				return;
			}


			frappe.call({

				method:
					"liner.liner.doctype.import_export_order.import_export_order.print_delivery_order",

				args: {
					doctype: frm.doc.doctype,
					docname: frm.doc.name,
					containers: JSON.stringify(selected_containers)
				},

				freeze: true,

				freeze_message: __("Preparing Delivery Order..."),

				callback: function (r) {

					if (r.exc) {
						return;
					}

					if (!r.message) {

						frappe.msgprint({
							title: __("Error"),
							message: __("Unable to generate Delivery Order."),
							indicator: "red"
						});

						return;
					}


					dialog.hide();


					/*
					 * Reload the document so the
					 * delivery_order_printed values are updated
					 * in the child table.
					 */
					frm.reload_doc().then(() => {

						const print_window = window.open(
							"",
							"_blank"
						);

						if (!print_window) {

							frappe.msgprint({
								title: __("Popup Blocked"),
								message: __("Please allow popups in your browser to print the Delivery Order."),
								indicator: "orange"
							});

							return;
						}


						print_window.document.open();

						print_window.document.write(r.message);

						print_window.document.close();


						print_window.onload = function () {

							print_window.focus();

							setTimeout(() => {
								print_window.print();
							}, 300);

						};

					});

				}

			});

		}

	});


	/* =========================================================
	   SET DIALOG HTML
	   ========================================================= */

	dialog.fields_dict.container_list.$wrapper.html(`

		<div style="
			margin-bottom:10px;
			font-size:13px;
			color:#666;
		">
			${__("Select the containers to include in the Delivery Order.")}
		</div>

		<div style="
			max-height:500px;
			overflow-y:auto;
			border:1px solid #d1d8dd;
			border-radius:4px;
		">

			<table
				class="table table-bordered"
				style="
					width:100%;
					margin:0;
					border-collapse:collapse;
				"
			>

				<thead>
					<tr>

						<th style="
							width:50px;
							text-align:center;
							vertical-align:middle;
						">
							<input
								type="checkbox"
								class="select-all-containers"
							>
						</th>

						<th>
							${__("Container No")}
						</th>

						<th>
							${__("Container Type")}
						</th>

						<th style="
							width:120px;
							text-align:center;
						">
							${__("Status")}
						</th>

					</tr>
				</thead>

				<tbody>
					${rows_html}
				</tbody>

			</table>

		</div>

	`);


	/* =========================================================
	   SELECT ALL
	   Only selects NOT PRINTED containers
	   ========================================================= */

	dialog.$wrapper.on(
		"change",
		".select-all-containers",
		function () {

			const checked = $(this).is(":checked");

			dialog.$wrapper
				.find(".delivery-container-checkbox:not(:disabled)")
				.prop("checked", checked);
		}
	);


	/* =========================================================
	   UPDATE SELECT ALL STATE
	   ========================================================= */

	dialog.$wrapper.on(
		"change",
		".delivery-container-checkbox",
		function () {

			const total_selectable =
				dialog.$wrapper
					.find(".delivery-container-checkbox:not(:disabled)")
					.length;

			const total_selected =
				dialog.$wrapper
					.find(".delivery-container-checkbox:not(:disabled):checked")
					.length;

			dialog.$wrapper
				.find(".select-all-containers")
				.prop(
					"checked",
					total_selectable > 0 &&
					total_selectable === total_selected
				);

		}
	);


	dialog.show();
}

frappe.ui.form.on("Equipment Table2", {
	calculate_dt: function(frm, cdt, cdn) {
		let row = locals[cdt][cdn];

		if (!row.in_date || !row.return_date) {
			frappe.msgprint("Please enter both In Date and Return Date.");
			return;
		}

		if (!frm.doc.line) {
			frappe.msgprint("Line is missing in Import-Export Order.");
			return;
		}

		// Calculate total detention days
		let detention_days = frappe.datetime.get_diff(
			row.return_date,
			row.in_date
		);

		// Same day = 1 day
		if (detention_days <= 0) {
			detention_days = 1;
		}

		// Find Detention Slab based on Line
		frappe.db.get_list("Detention Slab", {
			filters: {
				line: frm.doc.line
			},
			fields: ["name"],
			limit: 1
		}).then(slabs => {

			if (!slabs || !slabs.length) {
				frappe.msgprint(
					`No Detention Slab found for Line: ${frm.doc.line}`
				);

				frappe.model.set_value(
					cdt,
					cdn,
					"dt_charges",
					0
				);

				return;
			}

			let slab_name = slabs[0].name;

			// Get complete Detention Slab document
			frappe.db.get_doc(
				"Detention Slab",
				slab_name
			).then(slab => {

				if (!slab.charges || !slab.charges.length) {
					frappe.msgprint(
						`No Charges Slab found in ${slab_name}.`
					);

					frappe.model.set_value(
						cdt,
						cdn,
						"dt_charges",
						0
					);

					return;
				}

				// Sort slabs by days ascending
				let charges = [...slab.charges].sort(
					(a, b) => cint(a.days) - cint(b.days)
				);

				// Find applicable slab
				let applicable_charge = charges.find(charge => {
					return detention_days <= cint(charge.days);
				});

				if (!applicable_charge) {
					frappe.msgprint(
						`No detention slab available for ${detention_days} days.`
					);

					frappe.model.set_value(
						cdt,
						cdn,
						"dt_charges",
						0
					);

					return;
				}

				let charge_per_day = flt(
					applicable_charge.charge_per_day
				);

				/*
				* Calculate previous/free days.
				*
				* Example:
				* 7 days  = 0/day
				* 14 days = 20/day
				*
				* 9 total days:
				* 9 - 7 = 2 chargeable days
				*
				* 14 total days:
				* 14 - 7 = 7 chargeable days
				*/
				let previous_slab = charges.find(charge => {
					return cint(charge.days) < cint(applicable_charge.days);
				});

				let free_days = 0;

				if (previous_slab) {
					free_days = cint(previous_slab.days);
				}

				// Charge only days exceeding the previous slab
				let chargeable_days = Math.max(
					0,
					detention_days - free_days
				);

				let detention_charges =
					chargeable_days * charge_per_day;

				// Set Detention Charges
				frappe.model.set_value(
					cdt,
					cdn,
					"dt_charges",
					detention_charges
				);

				frappe.show_alert({
					message:
						`Total Detention Days: ${detention_days}<br>` +
						`Free Days: ${free_days}<br>` +
						`Chargeable Days: ${chargeable_days}<br>` +
						`Slab: ${applicable_charge.days} days<br>` +
						`Rate: ${charge_per_day}/day<br>` +
						`Total: ${detention_charges}`,
					indicator: "green"
				});
			});
		});
	},
	create_dt: function(frm, cdt, cdn) {
		let row = locals[cdt][cdn];

		if (!frm.doc.customer) {
			frappe.msgprint("Customer is missing.");
			return;
		}

		if (!frm.doc.company) {
			frappe.msgprint("Company is missing.");
			return;
		}

		if (!frm.doc.project) {
			frappe.msgprint("Project is missing.");
			return;
		}

		if (!frm.doc.line) {
			frappe.msgprint("Line is missing.");
			return;
		}

		if (!row.dt_charges || flt(row.dt_charges) <= 0) {
			frappe.msgprint(
				"Please calculate Detention Charges first."
			);
			return;
		}

		frappe.confirm(
			`Create Sales Invoice for detention charges of <b>${row.container_no || ""}</b>?`,
			function() {

				frappe.call({
					method: "liner.liner.doctype.import_export_order.import_export_order.create_detention_sales_invoice",
					args: {
						import_export_order: frm.doc.name,
						equipment_row_name: row.name
					},
					freeze: true,
					freeze_message: "Creating Detention Sales Invoice...",
					callback: function(r) {

						if (!r.message) {
							return;
						}

						frappe.show_alert({
							message:
								`Sales Invoice ${r.message.name} created successfully.`,
							indicator: "green"
						});

						// Open Sales Invoice
						frappe.set_route(
							"Form",
							"Sales Invoice",
							r.message.name
						);
					}
				});
			}
		);
	}
});

function download_manifest_xml(frm) {
	if (frm.is_new()) {
		frappe.msgprint(__("Please save the document first."));
		return;
	}

	frappe.call({
		method: "liner.liner.doctype.import_export_order.import_export_order.get_manifest_xml",
		args: {
			import_export_order: frm.doc.name
		},
		freeze: true,
		freeze_message: __("Generating Manifest XML..."),
		callback: function (r) {
			if (r.exc || !r.message) {
				return;
			}

			// Build a filename like: <BL No> Manifest.xml
			let filename = (frm.doc.bl_no || frm.doc.name || "Manifest")
				.replace(/[\\/:*?"<>|]/g, "_") + " Manifest.xml";

			let blob = new Blob([r.message], {
				type: "application/xml;charset=utf-8"
			});

			let url = URL.createObjectURL(blob);
			let a = document.createElement("a");
			a.href = url;
			a.download = filename;
			document.body.appendChild(a);
			a.click();
			document.body.removeChild(a);
			URL.revokeObjectURL(url);

			frappe.show_alert({
				message: __("Manifest XML downloaded."),
				indicator: "green"
			});
		}
	});
}
