import frappe

def on_submit(doc, method=None):
    if doc.paid_amount > 0:
        frappe.db.set_value("Import-Export Order", doc.custom_reference_importexport, "paid_amount", doc.paid_amount)
    