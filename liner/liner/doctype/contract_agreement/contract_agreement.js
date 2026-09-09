// Copyright (c) 2026, Milores and contributors
// For license information, please see license.txt

// frappe.ui.form.on("Contract Agreement", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on("Contract Agreement Table", {
    qty(frm, cdt, cdn) {
        calculate_amount(cdt, cdn);
    },

    rate(frm, cdt, cdn) {
        calculate_amount(cdt, cdn);
    },

    cost_price(frm, cdt, cdn) {
        calculate_amount(cdt, cdn);
    }
});

function calculate_amount(cdt, cdn) {
    const row = locals[cdt][cdn];

    const amount = flt(row.qty) * flt(row.rate);
    const cost_price = flt(row.qty) * flt(row.cost_price);

    frappe.model.set_value(cdt, cdn, "amount", amount);
    frappe.model.set_value(cdt, cdn, "line_cost_price", cost_price);
}
