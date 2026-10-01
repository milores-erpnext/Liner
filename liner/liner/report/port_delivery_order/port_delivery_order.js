// Copyright (c) 2026, Milores and contributors
// For license information, please see license.txt

frappe.query_reports["Port Delivery Order"] = {
	filters: [
		 {
            fieldname: "posting_date",
            label: __("Posting Date"),
            fieldtype: "Date",
            default: frappe.datetime.get_today(),
            reqd: 1
        }
	],
};
