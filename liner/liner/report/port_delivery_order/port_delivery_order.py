# Copyright (c) 2026, Milores and contributors
# For license information, please see license.txt

import frappe
from frappe import _

import frappe


def execute(filters=None):
    filters = filters or {}

    columns = get_columns()
    data = get_data(filters)

    return columns, data


def get_columns():
    return [
		{
            "label": "IDO Number",
            "fieldname": "ido_number",
            "fieldtype": "Data",
            "width": 120,
        },
		{
            "label": "Line Operator",
            "fieldname": "line",
            "fieldtype": "Data",
            "width": 120,
        },
		{
            "label": "Container",
            "fieldname": "container_no",
            "fieldtype": "Data",
            "width": 140,
        },
		{
            "label": "Return Location",
            "fieldname": "location",
            "fieldtype": "Data",
            "width": 140,
        },
		{
            "label": "Vessel",
            "fieldname": "vessal_name",
            "fieldtype": "Data",
            "width": 140,
        },
        {
            "label": "Voyage",
            "fieldname": "voyage_no",
            "fieldtype": "Data",
            "width": 100,
        },
		{
            "label": "Issued",
            "fieldname": "posting_date",
            "fieldtype": "Date",
            "width": 100,
        },
        {
            "label": "Expired",
            "fieldname": "expired_date",
            "fieldtype": "Date",
            "width": 100,
        }
    ]


def get_data(filters):
    conditions = []
    values = {}

    if filters.get("posting_date"):
        conditions.append("cdo.posting_date = %(posting_date)s")
        values["posting_date"] = filters["posting_date"]

    where_clause = ""

    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    return frappe.db.sql(
        f"""
        SELECT
            cdo.posting_date,
            cdo.ido_number,
            cdo.vessal_name,
            cdo.voyage_no,
            cdo.location,
            cdo.expired_date,
            child.container_no

        FROM `tabContainer Delivery Order` cdo

        LEFT JOIN `tabEquipment Table2` child
            ON child.parent = cdo.name
            AND child.parenttype = 'Container Delivery Order'
            AND child.parentfield = 'container_details'

        {where_clause}

        ORDER BY
            cdo.posting_date DESC,
            cdo.name DESC,
            child.idx ASC
        """,
        values,
        as_dict=True,
    )