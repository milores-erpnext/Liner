# Copyright (c) 2026, Milores and contributors
# For license information, please see license.txt

import frappe
import requests
import re
from frappe.model.document import Document
import xml.etree.ElementTree as ET
from datetime import datetime
from bs4 import BeautifulSoup
from datetime import datetime
from frappe.model.mapper import get_mapped_doc
from frappe.utils import today
import json
from frappe.utils import flt, cint, getdate, formatdate
from frappe import _
from xml.sax.saxutils import escape, quoteattr


class ImportExportOrder(Document):
	def validate(doc, method=None):
		calculate_other_charges(doc)

	def on_update(self):
		self.sync_container_items()
		self.fetch_container_tracking()
		self.fetch_bl_status()

	def sync_container_items(self):
		"""
		For every row in the Equipment Table2 child table (table_tvep),
		create or update an Item master where:
		  item_code = item_name = Container No
		  item_group = "Container"
		"""
		if not self.get("table_tvep"):
			return

		ensure_container_item_group_exists()

		for row in self.table_tvep:
			container_no = (row.container_no or "").strip()
			if not container_no:
				continue

			if frappe.db.exists("Item", container_no):
				item = frappe.get_doc("Item", container_no)
				item.item_name = container_no
				item.item_group = "Container"
				item.save(ignore_permissions=True)
			else:
				item = frappe.get_doc({
					"doctype": "Item",
					"item_code": container_no,
					"item_name": container_no,
					"item_group": "Container",
					"stock_uom": "Nos",
					"is_stock_item": 1,
				})
				item.insert(ignore_permissions=True)

	def fetch_container_tracking(self):
		if not self.bl_no or not self.table_tvep:
			return

		container_no = self.table_tvep[0].container_no
		if not container_no:
			return
		
		url = "http://182.72.192.230/TASFREIGHT/AppTasnet/ContainerTracking.aspx"

		try:
			response = requests.get(
				url,
				params={"containerno": container_no, "blNo": self.bl_no},
				timeout=20
			)
			response.raise_for_status()
			self.parse_tracking_response(response.text)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "TAS Freight Tracking API")

	def parse_tracking_response(self, html):
		soup = BeautifulSoup(html, "html.parser")
		tracking_rows = []

		table = soup.find("table", id="grdRouting")
		if not table:
			return

		for tr in table.find_all("tr")[1:]:
			cols = tr.find_all("td")
			if len(cols) < 6:
				continue

			from_ = cols[0].get_text(strip=True)
			to_ = cols[1].get_text(strip=True)
			if not from_ and not to_:
				continue

			tracking_rows.append({
				"from": from_,
				"to": to_,
				"vessel": cols[2].get_text(strip=True),
				"voyage": cols[3].get_text(strip=True),
				"etd": cols[4].get_text(strip=True),
				"eta": cols[5].get_text(strip=True),
			})

		self.set("expected_routing", [])
		for row in tracking_rows:
			self.append("expected_routing", row)
		self.update_child_table("expected_routing")

	def fetch_bl_status(self):
		url = "http://182.72.192.230/TASFREIGHT/AppTasnet/BLTracking.aspx"

		try:
			response = requests.get(
				url,
				params={"blNo": self.bl_no},
				timeout=20
			)
			response.raise_for_status()
			
			self.parse_bl_status_response(response.text)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "TAS Freight Tracking API")

	def parse_bl_status_response(self, html):
		soup = BeautifulSoup(html, "html.parser")

		table = soup.find("table", id="grdBLTracking")
		if not table:
			return

		tbody = table.find("tbody")
		if not tbody:
			return

		rows = tbody.find_all("tr")
		if not rows:
			return

		# Use the first data row; BL Status is the last column
		cols = rows[0].find_all("td")
		if len(cols) < 5:
			return

		bl_status = cols[-1].get_text(strip=True)
		if bl_status:
			self.db_set("bl_status", bl_status, update_modified=False)

def ensure_container_item_group_exists():
	if not frappe.db.exists("Item Group", "Container"):
		frappe.get_doc({
			"doctype": "Item Group",
			"item_group_name": "Container",
			"parent_item_group": "All Item Groups",
			"is_group": 0,
		}).insert(ignore_permissions=True)

@frappe.whitelist()
def import_xml(xml_text):
	"""
	Backward-compatible: parses only the first <Booking> and returns a
	flat dict, for applying onto a single already-open Import Order form.
	"""
	root = ET.fromstring(xml_text)
	root_values = _extract_vessel_values(root)

	booking = root.find("Booking")
	if booking is None:
		return root_values

	return _extract_booking_values(dict(root_values), booking)


@frappe.whitelist()
def import_xml_bulk(xml_text,order_type):
	"""
	Parses every <Booking> under the <Vessel> root and creates one
	Import-Export Order per booking. TASBookingno (-> line_booking_ref) is
	unique per booking; bookings that already exist are skipped.
	"""
	root = ET.fromstring(xml_text)
	root_values = _extract_vessel_values(root)

	created, skipped, errors = [], [], []

	for booking in root.findall("Booking"):
		values = _extract_booking_values(dict(root_values), booking)

		tas_booking_no = values.get("line_booking_ref")
		if not tas_booking_no:
			continue

		if frappe.db.exists("Import-Export Order", {"line_booking_ref": tas_booking_no}):
			skipped.append(tas_booking_no)
			continue

		try:
			values.setdefault("status", "Draft")
			doc = frappe.get_doc({"doctype": "Import-Export Order", **values})
			doc.order_type = order_type
			doc.company = None  # will be filled in manually after import
			# ignore_mandatory: company/customer etc. aren't in the XML and
			# will be filled in manually after import
			doc.insert(ignore_permissions=True, ignore_mandatory=True)
			frappe.db.set_value(
				"Import-Export Order",
				doc.name,
				"company",
				None,
				update_modified=False
			)

			doc.reload()
			created.append({"name": doc.name, "line_booking_ref": tas_booking_no})
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Import-Export Order Bulk XML Import")
			errors.append(tas_booking_no)

	return {"created": created, "skipped": skipped, "errors": errors}


# def _extract_vessel_values(root):
# 	"""Attributes on the root <Vessel> element — shared by every Booking."""
# 	values = {}
# 	root_field_map = {
# 		"VesselName": "vessel",
# 		"Voyage": "voyage_no",
# 		"Vesselcode":"vessel_code"
# 	}
# 	for xml_attr, fieldname in root_field_map.items():
# 		value = root.attrib.get(xml_attr)
# 		if value:
# 			values[fieldname] = value.strip()
# 	return values

def _extract_vessel_values(root):
	"""
	Attributes on the root <Vessel> element.
	Creates Vessel master automatically if it does not exist.
	"""

	values = {}

	vessel_name = (root.attrib.get("VesselName") or "").strip()
	vessel_code = (root.attrib.get("Vesselcode") or "").strip()
	voyage_no = (root.attrib.get("Voyage") or "").strip()

	# ---------------------------------------------------------
	# Vessel Link
	# ---------------------------------------------------------

	if vessel_name or vessel_code:
		vessel_link = get_or_create_vessel(
			vessel_name=vessel_name,
			vessel_code=vessel_code
		)

		if vessel_link:
			values["vessel"] = vessel_link

	# ---------------------------------------------------------
	# Voyage
	# ---------------------------------------------------------

	if voyage_no:
		values["voyage_no"] = voyage_no

	# Keep vessel code separately if your field exists
	if vessel_code:
		values["vessel_code"] = vessel_code

	return values

def _extract_booking_values(values, booking):
	"""Populate/return `values` with fields parsed from one <Booking> element."""
	booking_field_map = {"TASBookingno": "line_booking_ref"}
	for xml_attr, fieldname in booking_field_map.items():
		value = booking.attrib.get(xml_attr)
		if value:
			values[fieldname] = value.strip()

	bl = booking.find("BL")
	if bl is not None:
		bl_field_map = {"Blno": "bl_no", "BLdate": "bl_date"}
		for xml_attr, fieldname in bl_field_map.items():
			value = bl.attrib.get(xml_attr)
			if not value:
				continue
			value = value.strip()
			if xml_attr == "BLdate":
				try:
					value = datetime.strptime(value, "%d-%m-%Y").strftime("%Y-%m-%d")
				except ValueError:
					frappe.log_error(f"Could not parse BLdate value: {value}", "Import-Export Order XML Import")
					continue
			values[fieldname] = value

		party = bl.find("Party")
		if party is not None:
			parties = []
			shipper_name = party.attrib.get("Shipper")
			if shipper_name:
				parties.append({
					"party": "Shipper", "name1": shipper_name.strip(),
					"address": (party.attrib.get("ShipperAdd") or "").strip(),
					"fax": "", "email": "", "phone": "",
				})
			consignee_name = party.attrib.get("Consignee")
			if consignee_name:
				parties.append({
					"party": "Consignee", "name1": consignee_name.strip(),
					"address": (party.attrib.get("ConsigneeAdd") or "").strip(),
					"fax": "", "email": "", "phone": "",
				})
			notify1_name = party.attrib.get("Notify1")
			if notify1_name:
				parties.append({
					"party": "Notify1", "name1": notify1_name.strip(),
					"address": (party.attrib.get("Notify1Add") or "").strip(),
					"fax": "", "email": "", "phone": "",
				})
			if parties:
				values["table_zvxa"] = parties

		routing = bl.find("Routing")
		if routing is not None:
			# POLCode is inside <Routing>, not <Vessel>
			# pol_code = (routing.attrib.get("POLCode") or "").strip()

			# if pol_code:
			# 	values["port"] = pol_code
			pol_code = (routing.attrib.get("POLCode") or "").strip()

			if pol_code:
				port_link = get_or_create_sea_port(
					port_code=pol_code
				)

				if port_link:
					values["port"] = port_link
				
			hub_xml_map = {
				"Place of Receipt": "PlaceofReceipt",
				"Port of Loading": "PortOfLoading",
				"Port of Discharge": "PortOfDischarge",
				"Place of Delivery": "FinalPlaceOfDelivery",
			}
			# routing_rows = []
			# for hub_label, xml_attr in hub_xml_map.items():
			# 	hub_value = routing.attrib.get(xml_attr)
			# 	routing_rows.append({"hub": hub_label, "hub_name": (hub_value or "").strip()})
			# values["table_gfyk"] = routing_rows
			routing_rows = []

			for hub_label, xml_attr in hub_xml_map.items():

				hub_value = (routing.attrib.get(xml_attr) or "").strip()

				if hub_value:
					port_link = get_or_create_sea_port(
						port_code=hub_value
					)
				else:
					port_link = ""

				routing_rows.append({
					"hub": hub_label,
					"hub_name": port_link
				})

			values["table_gfyk"] = routing_rows

		cargo = bl.find("Cargo")
		if cargo is not None:
			cargo_field_map = {
				"MarksAndNos": "marks_and_nos",
				"NoOfPacks": "no_of_packs",
				"PackageDtl": "package_details",
				"DescriptionOfGoods": "desc_of_goods",
				"WeightInKg": "gross_weight_kg",
				"MeasurementCBM": "volume_cbm",
			}
			for xml_attr, fieldname in cargo_field_map.items():
				value = cargo.attrib.get(xml_attr)
				if value:
					values[fieldname] = value.strip()

			description = cargo.attrib.get("DescriptionOfGoods", "")
			if description:
				match = re.search(r"HS\s*CODE[:\s]*([0-9]{6,10})", description, re.IGNORECASE)
				if match:
					values["hs_code"] = match.group(1)

			containers_node = cargo.find("Containers")
			if containers_node is not None:
				equipment_summary = {}
				equipment_detail = []
				for container in containers_node.findall("Container"):
					c_type = (container.attrib.get("CType") or "").strip()
					if c_type:
						equipment_summary[c_type] = equipment_summary.get(c_type, 0) + 1
					equipment_detail.append({
						"c_type": c_type,
						"container_no": (container.attrib.get("ContainerNo") or "").strip(),
						"seal_no": (container.attrib.get("SealnO") or "").strip(),
						"tare_wt": (container.attrib.get("TareWt") or "").strip(),
					})
				if equipment_summary:
					values["table_cgpc"] = [
						{"type": c_type, "nos": str(count)} for c_type, count in equipment_summary.items()
					]
				if equipment_detail:
					values["table_tvep"] = equipment_detail

	freight_rows = []
	for freights in booking.findall("Freights"):
		for row in freights.findall("ContainerWise"):
			freight_rows.append({
				"ctype": (row.attrib.get("CType") or "").strip(),
				"element": (row.attrib.get("Element") or "").strip(),
				"rate": float(row.attrib.get("Rate") or 0),
				"currency": (row.attrib.get("Currency") or "").strip(),
				"collect_by": (row.attrib.get("CollectBy") or "").strip(),
				"no_of_containers": int(row.attrib.get("NoofContainers") or 0),
			})
	if freight_rows:
		values["freights"] = freight_rows

	return values

def format_date(value):
	if not value:
		return None

	for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y"):
		try:
			return datetime.strptime(value, fmt).date()
		except ValueError:
			continue

	return None
	
@frappe.whitelist()
def create_freight_charges_sales_invoice(import_export_order):

	# Get Import-Export Order
	order = frappe.get_doc("Import-Export Order", import_export_order)

	# Validate required fields
	if not order.customer:
		frappe.throw("Customer is missing in Import-Export Order.")

	if not order.company:
		frappe.throw("Company is missing in Import-Export Order.")

	if not order.project:
		frappe.throw("Project is missing in Import-Export Order.")

	if not order.grand_total:
		frappe.throw("Grand Total is missing or zero.")

	# Validate Contract Agreement table
	if not order.contract_agreement:
		frappe.throw(
			"No Contract Agreement found in Import-Export Order."
		)

	# Create Sales Invoice
	sales_invoice = frappe.new_doc("Sales Invoice")

	sales_invoice.customer = order.customer
	sales_invoice.company = order.company
	sales_invoice.project = order.project

	items_added = 0

	# Add all Contract Agreement items
	for row in order.contract_agreement:
		
		# Skip rows without item code
		if not row.item_code:
			continue

		# Get quantity from Equipment Table1 based on eq_type
		qty = frappe.db.get_value(
			"Equipment Table1",
			{"type": row.eq_type},
			"nos"
		) or 0

		qty = flt(qty)

		# Skip if quantity is zero
		if qty <= 0:
			continue

		# Rate from Contract Agreement
		rate = flt(row.rate)

		if rate <= 0:
			continue

		# Add item to Sales Invoice
		sales_invoice.append("items", {
			"item_code": row.item_code,
			"qty": qty,
			"rate": rate,
		})

		items_added += 1

	# Make sure at least one item was added
	if not items_added:
		frappe.throw(
			"No valid items found in Contract Agreement table."
		)

	# Set defaults and calculate totals
	sales_invoice.calculate_taxes_and_totals()

	# Insert as Draft
	sales_invoice.insert()

	return {
		"name": sales_invoice.name,
		"doctype": "Sales Invoice",
		"customer": sales_invoice.customer,
		"company": sales_invoice.company,
		"project": sales_invoice.project,
		"items": [
			{
				"item_code": item.item_code,
				"qty": item.qty,
				"rate": item.rate
			}
			for item in sales_invoice.items
		]
	}

@frappe.whitelist()
def create_liner_charges_purchase_invoice(import_export_order):

	# Get Import-Export Order
	order = frappe.get_doc("Import-Export Order", import_export_order)

	# Validate required fields
	if not order.line:
		frappe.throw("Line is missing in Import-Export Order.")

	if not order.company:
		frappe.throw("Company is missing in Import-Export Order.")

	if not order.project:
		frappe.throw("Project is missing in Import-Export Order.")

	if not order.total_liner:
		frappe.throw("Total Liner is missing or zero.")

	# Validate Contract Agreement table
	if not order.contract_agreement:
		frappe.throw(
			"No Contract Agreement found in Import-Export Order."
		)

	# Create Purchase Invoice
	purchase_invoice = frappe.new_doc("Purchase Invoice")

	purchase_invoice.supplier = order.line
	purchase_invoice.company = order.company
	purchase_invoice.project = order.project

	items_added = 0

	# Add all Contract Agreement items
	for row in order.contract_agreement:
		
		# Skip rows without item code
		if not row.item_code:
			continue

		# Skip LUM rate type
		if row.rate_type == "LUM":
			continue

		# Get quantity from Equipment Table1 based on eq_type
		qty = frappe.db.get_value(
			"Equipment Table1",
			{"type": row.eq_type},
			"nos"
		) or 0

		qty = flt(qty)

		# Skip if quantity is zero
		if qty <= 0:
			continue

		# Rate from Contract Agreement
		rate = flt(row.cost_price)

		if rate <= 0:
			continue

		# Add item to Purchase Invoice
		purchase_invoice.append("items", {
			"item_code": row.item_code,
			"qty": qty,
			"rate": rate,
		})

		items_added += 1

	# Make sure at least one item was added
	if not items_added:
		frappe.throw(
			"No valid items found in Contract Agreement table."
		)

	# Set defaults and calculate totals
	purchase_invoice.calculate_taxes_and_totals()

	# Insert as Draft
	purchase_invoice.insert()

	return {
		"name": purchase_invoice.name,
		"doctype": "Purchase Invoice",
		"supplier": purchase_invoice.supplier,
		"company": purchase_invoice.company,
		"project": purchase_invoice.project,
		"items": [
			{
				"item_code": item.item_code,
				"qty": item.qty,
				"rate": item.rate
			}
			for item in purchase_invoice.items
		]
	}

def sync_all_tracking_statuses():
	"""
	Daily scheduled job: refreshes container routing (expected_routing)
	and bl_status for every Import-Export Order that still has a BL No set.
	Each document runs as its own background job so one slow/erroring
	API call doesn't block the rest of the batch.
	"""
	names = frappe.get_all(
		"Import-Export Order",
		filters={"bl_no": ["is", "set"]},
		pluck="name",
	)

	for name in names:
		frappe.enqueue(
			method="liner.liner.doctype.import_export_order.import_export_order.sync_single_tracking_status",
			queue="long",
			job_name=f"import_export_order_tracking_sync_{name}",
			docname=name,
		)


def sync_single_tracking_status(docname):
	try:
		doc = frappe.get_doc("Import-Export Order", docname)
		doc.fetch_container_tracking()
		doc.fetch_bl_status()
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		frappe.log_error(
			frappe.get_traceback(),
			f"Import-Export Order Tracking Sync Failed: {docname}",
		)

@frappe.whitelist()
def make_sales_invoice(source_name, target_doc=None):
	def set_missing_values(source, target):
		target.customer = source.customer
		target.company = source.company
		target.project = source.project
		target.due_date = frappe.utils.today()

		# Link back to Import-Export Order (optional custom field)
		target.import_export_order = source.name

	doc = get_mapped_doc(
		"Import-Export Order",
		source_name,
		{
			"Import-Export Order": {
				"doctype": "Sales Invoice",
				"validation": {
					"docstatus": ["=", 1]
				}
			},
			"Contract Agreement Table": {
				"doctype": "Sales Invoice Item",
				"field_map": {
					"item_code": "item_code",
					"item_name": "item_name",
					"qty": "qty",
					"rate": "rate",
					"amount": "amount"
				}
			}
		},
		target_doc,
		set_missing_values
	)

	return doc

@frappe.whitelist()
def make_payment_entry(source_name, target_doc=None):
	def set_missing_values(source, target):
		target.payment_type = "Receive"
		target.party_type = "Customer"
		target.party = source.customer
		target.party_name = frappe.get_value("Customer", source.customer, "customer_name") or None
		target.custom_reference_importexport = source.name

		target.company = source.company
		target.posting_date = today()

		# Amount from Import-Export Order
		target.paid_amount = source.grand_total or 0
		target.received_amount = source.grand_total or 0

		# Optional reference fields
		target.reference_no = source.name
		target.reference_date = source.arrival_date or today()

		# Optional custom link field
		if hasattr(target, "import_export_order"):
			target.import_export_order = source.name

	doc = get_mapped_doc(
		"Import-Export Order",
		source_name,
		{
			"Import-Export Order": {
				"doctype": "Payment Entry",
				# "validation": {
				# 	"docstatus": ["=", 0]
				# }
			}
		},
		target_doc,
		set_missing_values
	)

	return doc

# @frappe.whitelist()
# def print_delivery_order(doctype, docname, containers,doc):
# 	if isinstance(containers, str):
# 		containers = json.loads(containers)
# 		doc = json.loads(doc)

# 	if not containers:
# 		frappe.throw("Please select at least one container.")

# 	# ---------------------------------------------------------
# 	# Create Container Delivery Order
# 	# ---------------------------------------------------------
# 	delivery_order = frappe.new_doc("Container Delivery Order")
# 	company_name = (doc.get("company") or "").strip()
# 	company_words = company_name.split()

# 	# ---------------------------------------------------------
# 	# Map Header Fields
# 	# ---------------------------------------------------------
# 	delivery_order.company = doc.get("company")
# 	delivery_order.customer = doc.get("customer")
# 	delivery_order.line = doc.get("line")
# 	delivery_order.posting_date = frappe.utils.today()
# 	delivery_order.location = doc.get("location")

# 	# ---------------------------------------------------------
# 	# Map Selected Container Details
# 	# ---------------------------------------------------------
# 	selected_names = {
# 		row.get("name")
# 		for row in containers
# 		if row.get("name")
# 	}

# 	for source_row in doc.get("table_tvep"):

# 		if source_row.get("name") not in selected_names:
# 			continue

# 		target_row = delivery_order.append(
# 			"container_details",
# 			{}
# 		)

# 		# Equipment Table2 fields
# 		target_row.container_no = source_row.get("container_no")
# 		target_row.c_type = source_row.get("c_type")
# 		target_row.seal_no = source_row.get("seal_no")
# 		target_row.tare_wt = source_row.get("tare_wt")

# 		# Optional fields, if required
# 		# target_row.owner1 = source_row.owner1
# 		# target_row.dt_charges = source_row.dt_charges
# 		# target_row.dth = source_row.dth
# 		# target_row.og = source_row.og
# 		# target_row.dg = source_row.dg

# 	# ---------------------------------------------------------
# 	# Validate
# 	# ---------------------------------------------------------
# 	if not delivery_order.container_details:
# 		frappe.throw("No valid containers were selected.")

# 	# ---------------------------------------------------------
# 	# Save
# 	# ---------------------------------------------------------
# 	delivery_order.insert()
# 	frappe.db.set_value(doctype,docname,'do_no',delivery_order.name)

# 	# ---------------------------------------------------------
# 	# Load original Import-Export Order
# 	# ---------------------------------------------------------

# 	original_doc = frappe.get_doc(doctype, docname)

# 	# ---------------------------------------------------------
# 	# Get selected child row names
# 	# ---------------------------------------------------------

# 	selected_names = {
# 		row.get("name")
# 		for row in containers
# 		if row.get("name")
# 	}

# 	if not selected_names:
# 		frappe.throw("No valid containers were selected.")

# 	# ---------------------------------------------------------
# 	# Create an in-memory copy
# 	# ---------------------------------------------------------

# 	doc = frappe.copy_doc(original_doc)

# 	doc.name = original_doc.name
# 	doc.posting_date = original_doc.get("posting_date")

# 	# ---------------------------------------------------------
# 	# Keep only selected containers
# 	# ---------------------------------------------------------

# 	selected_table = []

# 	for row in original_doc.table_tvep:

# 		if row.name in selected_names:

# 			selected_table.append({
# 				"doctype": row.doctype,
# 				"name": row.name,
# 				"container_no": row.container_no,
# 				"c_type": row.c_type,
# 				"seal_no": row.seal_no,
# 				"tare_wt": row.tare_wt,
# 				"owner1": row.owner1,
# 				"dth": row.dth,
# 				"og": row.og,
# 				"dg": row.dg,
# 				"delivery_order_printed": row.delivery_order_printed,
# 			})

# 	if not selected_table:
# 		frappe.throw("No valid containers were selected.")

# 	# ---------------------------------------------------------
# 	# Replace child table only in memory
# 	# Original document is NOT modified here
# 	# ---------------------------------------------------------

# 	doc.set("table_tvep", selected_table)

# 	# ---------------------------------------------------------
# 	# Generate print HTML
# 	# ---------------------------------------------------------

# 	# html = frappe.get_print(
# 	# 	doctype=doctype,
# 	# 	name=docname,
# 	# 	print_format="Delivery Order",
# 	# 	doc=doc,
# 	# 	letterhead="Company Letterhead - ICSS"
# 	# )

# 	# ---------------------------------------------------------
# 	# Mark selected containers as Delivery Order Printed
# 	# Only execute after successful print HTML generation
# 	# ---------------------------------------------------------

# 	for row_name in selected_names:

# 		child_exists = frappe.db.exists(
# 			"Equipment Table2",
# 			row_name
# 		)

# 		if child_exists:
# 			frappe.db.set_value(
# 				"Equipment Table2",
# 				row_name,
# 				"delivery_order_printed",
# 				1,
# 				update_modified=False
# 			)

	# ---------------------------------------------------------
	# Return print HTML
	# ---------------------------------------------------------

	# return html

@frappe.whitelist()
def create_detention_sales_invoice(import_export_order, equipment_row_name):
	"""
	Create Sales Invoice for detention charges from Equipment Table2.
	"""

	# Get Import-Export Order
	order = frappe.get_doc("Import-Export Order", import_export_order)

	if not order.customer:
		frappe.throw("Customer is missing in Import-Export Order.")

	if not order.company:
		frappe.throw("Company is missing in Import-Export Order.")

	if not order.project:
		frappe.throw("Project is missing in Import-Export Order.")

	if not order.line:
		frappe.throw("Line is missing in Import-Export Order.")

	# Find Equipment Table2 row
	equipment_row = None

	for row in order.table_tvep:
		if row.name == equipment_row_name:
			equipment_row = row
			break

	if not equipment_row:
		frappe.throw(
			f"Equipment row {equipment_row_name} not found."
		)

	# Validate detention charges
	dt_charges = frappe.utils.flt(equipment_row.dt_charges)

	if dt_charges <= 0:
		frappe.throw(
			"Detention Charges must be greater than 0 before creating the Sales Invoice."
		)

	# Find Detention Slab using Line
	detention_slabs = frappe.get_all(
		"Detention Slab",
		filters={
			"line": order.line
		},
		fields=[
			"name",
			"item",
			"item_name",
			"currency"
		],
		limit=1
	)

	if not detention_slabs:
		frappe.throw(
			f"No Detention Slab found for Line: {order.line}"
		)

	detention_slab = detention_slabs[0]

	if not detention_slab.item:
		frappe.throw(
			f"Item is not configured in Detention Slab {detention_slab.name}."
		)

	# Create Sales Invoice
	sales_invoice = frappe.new_doc("Sales Invoice")

	sales_invoice.customer = order.customer
	sales_invoice.company = order.company
	sales_invoice.project = order.project

	# Add item
	sales_invoice.append("items", {
		"item_code": detention_slab.item,
		"qty": 1,
		"rate": dt_charges,
		"project": order.project
	})

	# Optional reference
	# Only set these if the fields exist in your Sales Invoice.
	if frappe.get_meta("Sales Invoice").has_field("custom_import_export_order"):
		sales_invoice.custom_import_export_order = order.name

	if frappe.get_meta("Sales Invoice Item").has_field("custom_container_no"):
		sales_invoice.items[0].custom_container_no = (
			equipment_row.container_no
		)

	sales_invoice.insert()

	return {
		"name": sales_invoice.name,
		"doctype": "Sales Invoice",
		"customer": sales_invoice.customer,
		"company": sales_invoice.company,
		"project": sales_invoice.project,
		"item": detention_slab.item,
		"qty": 1,
		"rate": dt_charges
	}

def _esc(value):
	"""Escape a value for use inside an XML attribute."""
	return escape(str(value or ""))


def _attr(name, value):
	"""Build a single XML attribute safely."""
	return f' {name}={quoteattr(str(value or ""))}'


@frappe.whitelist()
def get_manifest_xml(import_export_order):
	"""
	Generate Cargo Manifest XML for Qatar Customs format.
	"""

	doc = frappe.get_doc("Import-Export Order", import_export_order)

	# =========================================================
	# HELPERS
	# =========================================================

	def clean(value):
		return str(value or "").strip()

	def xml_escape(value):
		return escape(clean(value))

	def add_element(tag, value="", indent=0):
		spaces = " " * indent
		return f"{spaces}<{tag}>{xml_escape(value)}</{tag}>\n"

	def add_element_raw(tag, content, indent=0):
		spaces = " " * indent
		return f"{spaces}<{tag}>\n{content}{spaces}</{tag}>\n"

	def parse_port(value):
		"""
		Convert values such as:
			HAMAD,QATAR
			CHENNAI,INDIA
			JEBEL ALI,DUBAI,UAE

		Returns:
			country_code
			port_code
			port_name
		"""

		value = clean(value)

		if not value:
			return {
				"country_code": "",
				"port_code": "",
				"port_name": ""
			}

		upper = value.upper()

		# -----------------------------------------------------
		# Known ports
		# -----------------------------------------------------
		port_map = {
			"HAMAD": {
				"country_code": "QA",
				"port_code": "QAHMD",
				"port_name": "Hamad",
			},
			"HAMAD,QATAR": {
				"country_code": "QA",
				"port_code": "QAHMD",
				"port_name": "Hamad",
			},
			"CHENNAI": {
				"country_code": "IN",
				"port_code": "INMAA",
				"port_name": "Chennai",
			},
			"CHENNAI,INDIA": {
				"country_code": "IN",
				"port_code": "INMAA",
				"port_name": "Chennai",
			},
			"COCHIN": {
				"country_code": "IN",
				"port_code": "INCOK",
				"port_name": "Cochin Seaport",
			},
			"COCHIN,INDIA": {
				"country_code": "IN",
				"port_code": "INCOK",
				"port_name": "Cochin Seaport",
			},
			"JEBEL ALI": {
				"country_code": "AE",
				"port_code": "AEJEA",
				"port_name": "Jebel Ali, Dubai, Uae",
			},
			"JEBEL ALI,DUBAI,UAE": {
				"country_code": "AE",
				"port_code": "AEJEA",
				"port_name": "Jebel Ali, Dubai, Uae",
			},
			"SOHAR": {
				"country_code": "OM",
				"port_code": "OMSOH",
				"port_name": "Sohar",
			},
			"SOHAR,OMAN": {
				"country_code": "OM",
				"port_code": "OMSOH",
				"port_name": "Sohar",
			},
		}

		if upper in port_map:
			return port_map[upper]

		# -----------------------------------------------------
		# Generic fallback
		# -----------------------------------------------------
		parts = [x.strip() for x in value.split(",") if x.strip()]

		port_name = parts[0] if parts else value

		country_code = ""

		country_map = {
			"QATAR": "QA",
			"INDIA": "IN",
			"UAE": "AE",
			"DUBAI": "AE",
			"OMAN": "OM",
			"KUWAIT": "KW",
			"SAUDI ARABIA": "SA",
			"BAHRAIN": "BH",
		}

		for part in parts[1:]:
			if part.upper() in country_map:
				country_code = country_map[part.upper()]
				break

		return {
			"country_code": country_code,
			"port_code": "",
			"port_name": port_name,
		}

	def get_container_size(c_type):
		"""
		20DV -> 20
		40HC -> 40
		40HQ -> 40
		"""
		c_type = clean(c_type)

		match = re.search(r"(\d{2})", c_type)

		if match:
			return match.group(1)

		return ""

	def get_container_type(c_type):
		"""
		20DV -> DV
		40HC -> HC
		40HQ -> HQ
		"""
		c_type = clean(c_type)

		match = re.search(r"\d{2}(.*)", c_type)

		if match:
			return clean(match.group(1))

		return c_type

	def get_country_from_port(value):
		return parse_port(value).get("country_code", "")

	def get_port_from_routing(hub_name):
		return parse_port(hub_name)

	# =========================================================
	# MESSAGE HEADER
	# =========================================================

	now = datetime.now()

	message_date = now.strftime("%d%m%Y")
	message_time = now.strftime("%H%M")

	# These are not present in Import-Export Order.
	# Keep configurable/static for now.
	customs_code = "1021"
	user_id = "1082760027535602832"

	# =========================================================
	# MANIFEST HEADER
	# =========================================================

	manifest_no = clean(
		doc.name
	)

	transport_mode = "S"

	if doc.order_type:
		if clean(doc.order_type).lower() == "export":
			transport_doc_type = "EX"
		else:
			transport_doc_type = "IN"
	else:
		transport_doc_type = "IN"

	# =========================================================
	# ROUTING
	# =========================================================

	place_of_receipt = ""
	port_of_loading = ""
	port_of_discharge = ""
	final_destination = ""

	for row in doc.table_gfyk or []:

		hub = clean(row.hub).lower()
		hub_name = clean(row.hub_name)

		if hub == "place of receipt":
			place_of_receipt = hub_name

		elif hub == "port of loading":
			port_of_loading = hub_name

		elif hub == "port of discharge":
			port_of_discharge = hub_name

		elif hub == "place of delivery":
			final_destination = hub_name

	# ---------------------------------------------------------
	# Port of Discharge
	# ---------------------------------------------------------

	pod = get_port_from_routing(port_of_discharge)

	# If no POD exists, try final destination
	if not pod["port_name"]:
		pod = get_port_from_routing(final_destination)

	# ---------------------------------------------------------
	# Port of Loading
	# ---------------------------------------------------------

	pol = get_port_from_routing(port_of_loading)

	# If no POL exists, use Place of Receipt
	if not pol["port_name"]:
		pol = get_port_from_routing(place_of_receipt)

	# =========================================================
	# CONTAINERS
	# =========================================================

	container_rows = doc.table_tvep or []

	number_of_containers = len(
		[
			row
			for row in container_rows
			if clean(row.container_no)
		]
	)

	# =========================================================
	# PARTIES
	# =========================================================

	party_type_map = {
		"shipper": "1",
		"consignee": "3",
		"notify1": "4",
		"notify": "4",
	}

	party_xml = ""

	for row in doc.table_zvxa or []:

		party = clean(row.party).lower()

		party_type = party_type_map.get(party)

		if not party_type:
			continue

		party_xml += " " * 14 + "<Party>\n"
		party_xml += add_element(
			"PartyType",
			party_type,
			16
		)
		party_xml += add_element(
			"PartyName",
			row.name1,
			16
		)
		party_xml += add_element(
			"PartyAddress",
			row.address,
			16
		)
		party_xml += " " * 14 + "</Party>\n"

	# =========================================================
	# TOTAL QUANTITY
	# =========================================================

	total_quantity = flt(doc.no_of_packs)

	if not total_quantity:
		total_quantity = 0

	# ---------------------------------------------------------
	# UOM
	# ---------------------------------------------------------

	package_detail = clean(doc.package_details)

	if package_detail:
		package_upper = package_detail.upper()

		if package_upper in ("BOX", "BOXES", "CARTON", "CARTONS", "CTN", "CTNS"):
			quantity_uom = "CTN"
		elif package_upper in ("PACKAGE", "PACKAGES", "PKG", "PKGS"):
			quantity_uom = "PKG"
		else:
			quantity_uom = package_upper
	else:
		quantity_uom = "PKG"

	# =========================================================
	# MASTER / TRANSPORT DOCUMENT
	# =========================================================

	bl_no = clean(doc.bl_no)

	transport_doc_number = bl_no

	# =========================================================
	# GROSS WEIGHT
	# =========================================================

	gross_weight = flt(doc.gross_weight_kg)

	# =========================================================
	# COUNTRY OF ORIGIN
	# =========================================================

	coo = pol["country_code"] or "IN"

	# =========================================================
	# ITEM DESCRIPTION
	# =========================================================

	item_description = clean(doc.desc_of_goods)

	# =========================================================
	# MARKS & NUMBERS
	# =========================================================

	marks_numbers = clean(doc.marks_and_nos)

	# =========================================================
	# ROUTE LIST
	# =========================================================

	route_values = []

	# First use Import-Export Order routing table
	for row in doc.table_gfyk or []:

		hub = clean(row.hub).lower()

		if hub in (
			"place of receipt",
			"port of loading",
			"port of discharge",
			"place of delivery"
		):

			value = clean(row.hub_name)

			if value:
				route_values.append(value)

	# Remove duplicate routes while maintaining order
	unique_routes = []

	for value in route_values:
		if value not in unique_routes:
			unique_routes.append(value)

	# =========================================================
	# XML START
	# =========================================================

	xml = '<?xml version="1.0" encoding="UTF-8"?>\n'

	xml += (
		'<CargoManifest '
		'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
		'xsi:noNamespaceSchemaLocation="xml%20scheam\\Cargo_Manifest_Schema.xsd">'
		"\n"
	)

	# =========================================================
	# MESSAGE HEADER
	# =========================================================

	xml += "  <MessageHeader>\n"

	xml += "    <MessageSender>\n"

	xml += add_element(
		"customs_code",
		customs_code,
		6
	)

	xml += add_element(
		"user_id",
		user_id,
		6
	)

	xml += "    </MessageSender>\n"

	xml += add_element(
		"MessageDate",
		message_date,
		4
	)

	xml += add_element(
		"MessageTime",
		message_time,
		4
	)

	xml += add_element(
		"CourierManifest",
		"N",
		4
	)

	xml += "  </MessageHeader>\n"

	# =========================================================
	# MANIFEST RECORD
	# =========================================================

	xml += "  <ManifestRecord>\n"

	# ---------------------------------------------------------
	# Manifest Header
	# ---------------------------------------------------------

	xml += "    <ManifestHeader>\n"

	xml += add_element(
		"ManifestNo",
		manifest_no,
		6
	)

	xml += add_element(
		"TransportMode",
		transport_mode,
		6
	)

	xml += add_element(
		"TransportDocType",
		transport_doc_type,
		6
	)

	# ---------------------------------------------------------
	# Port Of Discharge
	# ---------------------------------------------------------

	xml += "      <PortOfDischarge>\n"

	xml += add_element(
		"CountryCode",
		pod["country_code"],
		8
	)

	xml += add_element(
		"PortCode",
		pod["port_code"],
		8
	)

	xml += add_element(
		"PortName",
		pod["port_name"],
		8
	)

	xml += add_element(
		"PortType",
		"S",
		8
	)

	xml += "      </PortOfDischarge>\n"

	xml += add_element(
		"NumberofTrnDocs",
		1,
		6
	)

	xml += add_element(
		"NumberofContainers",
		number_of_containers,
		6
	)

	xml += "    </ManifestHeader>\n"

	# =========================================================
	# ETA / ETD FROM EXPECTED ROUTING
	# =========================================================

	eta_value = ""
	etd_value = ""

	if doc.expected_routing:
		# Prefer the routing row matching the order vessel/voyage
		tracking_row = None

		for row in doc.expected_routing:
			if (
				row.vessel == doc.vessel
				and row.voyage == doc.voyage_no
			):
				tracking_row = row
				break

		# Fallback to the last routing row
		if not tracking_row:
			tracking_row = doc.expected_routing[-1]

		etd_value = format_tracking_datetime(tracking_row.etd)
		eta_value = format_tracking_datetime(tracking_row.eta)

	# =========================================================
	# MEANS OF TRANSPORT
	# =========================================================

	xml += "    <MeansOfTransport>\n"

	# No vessel/IMO/transport number field currently exists.
	# Keep blank instead of incorrectly using voyage or booking number.
	xml += add_element(
		"MeansofTransportNumber",
		doc.vessel_code,
		6
	)

	xml += add_element(
		"MeansofTransportName",
		doc.vessel,
		6
	)

	xml += add_element(
		"MeansofTransportNat",
		pod["country_code"] or "QA",
		6
	)

	xml += add_element(
		"DestinationCountry",
		pod["country_code"] or "QA",
		6
	)

	xml += add_element(
		"FlightVoyageNumber",
		doc.voyage_no,
		6
	)

	# ETA / ETD are not available in Import-Export Order.
	xml += add_element(
		"ETA",
		eta_value,
		6
	)

	xml += add_element(
		"ETD",
		etd_value,
		6
	)

	xml += "    </MeansOfTransport>\n"

	# =========================================================
	# TRANSPORT DOCUMENT LIST
	# =========================================================

	xml += "    <TransportDocumentList>\n"

	xml += "      <TransportDocument>\n"

	xml += add_element(
		"MasterTransportDocNumber",
		bl_no,
		8
	)

	xml += add_element(
		"TransportDocNumber",
		transport_doc_number,
		8
	)

	# ---------------------------------------------------------
	# Total Quantity
	# ---------------------------------------------------------

	xml += "        <TotalQuantity>\n"

	xml += add_element(
		"Quantity",
		f"{total_quantity:.2f}",
		10
	)

	xml += add_element(
		"UOM",
		quantity_uom,
		10
	)

	xml += "        </TotalQuantity>\n"

	# ---------------------------------------------------------
	# Total Gross Weight
	# ---------------------------------------------------------

	xml += "        <TotalGrossWeight>\n"

	xml += add_element(
		"Weight",
		f"{gross_weight:.2f}",
		10
	)

	xml += add_element(
		"UOM",
		"KG",
		10
	)

	xml += "        </TotalGrossWeight>\n"

	# ---------------------------------------------------------
	# First Port Of Loading
	# ---------------------------------------------------------

	xml += "        <FirstPortofLoading>\n"

	xml += add_element(
		"CountryCode",
		pol["country_code"],
		10
	)

	xml += add_element(
		"PortCode",
		pol["port_code"],
		10
	)

	xml += add_element(
		"PortName",
		pol["port_name"],
		10
	)

	xml += add_element(
		"PortType",
		"S",
		10
	)

	xml += "        </FirstPortofLoading>\n"

	# ---------------------------------------------------------
	# Port Of Loading
	# ---------------------------------------------------------

	xml += "        <PortofLoading>\n"

	xml += add_element(
		"CountryCode",
		pol["country_code"],
		10
	)

	xml += add_element(
		"PortCode",
		pol["port_code"],
		10
	)

	xml += add_element(
		"PortName",
		pol["port_name"],
		10
	)

	xml += add_element(
		"PortType",
		"S",
		10
	)

	xml += "        </PortofLoading>\n"

	# ---------------------------------------------------------
	# Port Of Discharge
	# ---------------------------------------------------------

	xml += "        <PortofDischarge>\n"

	xml += add_element(
		"CountryCode",
		pod["country_code"],
		10
	)

	xml += add_element(
		"PortCode",
		pod["port_code"],
		10
	)

	xml += add_element(
		"PortName",
		pod["port_name"],
		10
	)

	xml += add_element(
		"PortType",
		"S",
		10
	)

	xml += "        </PortofDischarge>\n"

	# ---------------------------------------------------------
	# Final Destination
	# ---------------------------------------------------------

	final_destination_country = (
		get_country_from_port(final_destination)
		or pod["country_code"]
		or "QA"
	)

	xml += add_element(
		"FinalDestination",
		final_destination_country,
		8
	)

	# =========================================================
	# PARTY LIST
	# =========================================================

	xml += "        <PartyList>\n"
	xml += party_xml
	xml += "        </PartyList>\n"

	# =========================================================
	# CONTAINER LIST
	# =========================================================

	xml += "        <ContainerList>\n"

	for row in container_rows:

		container_no = clean(row.container_no)

		if not container_no:
			continue

		c_type = clean(row.c_type)

		container_type = get_container_type(c_type)
		container_size = get_container_size(c_type)

		seal_no = clean(row.seal_no)
		tare_weight = flt(row.tare_wt)

		# At present gross weight is stored at document level.
		# Therefore use document gross weight for cargo weight.
		cargo_weight = gross_weight

		xml += "          <Container>\n"

		xml += add_element(
			"ContainerNumber",
			container_no,
			12
		)

		xml += add_element(
			"ContainerType",
			container_type,
			12
		)

		xml += add_element(
			"ContainerSize",
			container_size,
			12
		)

		# -----------------------------------------------------
		# Seal
		# -----------------------------------------------------

		xml += "            <SealNo>\n"

		xml += add_element(
			"SealNo",
			seal_no,
			14
		)

		xml += add_element(
			"CountryCode",
			coo,
			14
		)

		xml += "            </SealNo>\n"

		# -----------------------------------------------------
		# Tare Weight
		# -----------------------------------------------------

		xml += "            <TareWeight>\n"

		xml += add_element(
			"Weight",
			f"{tare_weight:.2f}",
			14
		)

		xml += add_element(
			"UOM",
			"KG",
			14
		)

		xml += "            </TareWeight>\n"

		# -----------------------------------------------------
		# Cargo Weight
		# -----------------------------------------------------

		xml += "            <CargoWeight>\n"

		xml += add_element(
			"Weight",
			f"{cargo_weight:.2f}",
			14
		)

		xml += add_element(
			"UOM",
			"KG",
			14
		)

		xml += "            </CargoWeight>\n"

		xml += "          </Container>\n"

	xml += "        </ContainerList>\n"

	# =========================================================
	# ITEM LIST
	# =========================================================

	xml += "        <ItemList>\n"

	xml += "          <Item>\n"

	xml += add_element(
		"ItemDescription",
		item_description,
		12
	)

	xml += add_element(
		"ItemCOO",
		coo,
		12
	)

	# ---------------------------------------------------------
	# Item Quantity
	# ---------------------------------------------------------

	xml += "            <ItemQuantity>\n"

	xml += add_element(
		"Quantity",
		f"{total_quantity:.2f}",
		14
	)

	xml += add_element(
		"UOM",
		quantity_uom,
		14
	)

	xml += "            </ItemQuantity>\n"

	# ---------------------------------------------------------
	# Item Gross Weight
	# ---------------------------------------------------------

	xml += "            <ItemGrossWeight>\n"

	xml += add_element(
		"Weight",
		f"{gross_weight:.2f}",
		14
	)

	xml += add_element(
		"UOM",
		"KG",
		14
	)

	xml += "            </ItemGrossWeight>\n"

	xml += add_element(
		"ItemPackagingType",
		"CON",
		12
	)

	# ---------------------------------------------------------
	# Item Container Number
	# ---------------------------------------------------------

	first_container = ""

	for row in container_rows:
		if clean(row.container_no):
			first_container = clean(row.container_no)
			break

	xml += add_element(
		"ItemContainerNumber",
		first_container,
		12
	)

	xml += add_element(
		"ItemMarksNumbers",
		marks_numbers,
		12
	)

	# ---------------------------------------------------------
	# Dangerous Goods
	# ---------------------------------------------------------

	dg_flag = "N"

	for row in container_rows:
		if cint(row.dg):
			dg_flag = "Y"
			break

	xml += add_element(
		"ItemDGFlag",
		dg_flag,
		12
	)

	xml += add_element(
		"ItemUNDGClass",
		"NIL" if dg_flag == "N" else "",
		12
	)

	xml += "          </Item>\n"

	xml += "        </ItemList>\n"

	# =========================================================
	# ROUTE LIST
	# =========================================================

	xml += "        <RouteList>\n"

	for route in unique_routes:

		port = get_port_from_routing(route)

		xml += "          <Route>\n"
		xml += "            <Port>\n"

		xml += add_element(
			"CountryCode",
			port["country_code"],
			14
		)

		xml += add_element(
			"PortCode",
			port["port_code"],
			14
		)

		xml += add_element(
			"PortName",
			port["port_name"],
			14
		)

		xml += add_element(
			"PortType",
			"S",
			14
		)

		xml += "            </Port>\n"
		xml += "          </Route>\n"

	xml += "        </RouteList>\n"

	# =========================================================
	# CLOSE DOCUMENT
	# =========================================================

	xml += "      </TransportDocument>\n"
	xml += "    </TransportDocumentList>\n"
	xml += "  </ManifestRecord>\n"
	xml += "</CargoManifest>"

	return xml

def calculate_other_charges(doc):
	total = 0

	# Build equipment quantity map from table_cgpc
	equipment_qty = {}

	for row in doc.table_cgpc or []:
		if row.type:
			equipment_qty[row.type] = float(row.nos or 0)

	# Calculate each other charge
	for row in doc.other_charges or []:
		amount = row.qty * row.rate

		row.amount = amount
		total += amount

	doc.total_other_charges = total

@frappe.whitelist()
def create_other_charges_sales_invoice(import_export_order):
	# Get Import-Export Order
	order = frappe.get_doc("Import-Export Order", import_export_order)

	# Validate required fields
	if not order.customer:
		frappe.throw("Customer is missing in Import-Export Order.")

	if not order.company:
		frappe.throw("Company is missing in Import-Export Order.")

	if not order.project:
		frappe.throw("Project is missing in Import-Export Order.")

	# Validate Other Charges table
	if not order.other_charges:
		frappe.throw("No Other Charges found in Import-Export Order.")

	# Create Sales Invoice
	sales_invoice = frappe.new_doc("Sales Invoice")

	sales_invoice.customer = order.customer
	sales_invoice.company = order.company
	sales_invoice.project = order.project

	items_added = 0

	# Add all Other Charges items
	for row in order.other_charges:
		
		# # Skip rows without item/type
		# if not row.eq_type:
		# 	continue
		
		# Get quantity from Equipment Table1 based on eq_type
		# qty = frappe.db.get_value(
		# 	"Equipment Table1",
		# 	{"type": row.eq_type},
		# 	"nos"
		# ) or 0

		# qty = flt(qty)

		# # Skip if quantity is zero
		# if qty <= 0:
		# 	continue

		# Add item to Sales Invoice
		sales_invoice.append("items", {
			"item_code": row.item_code,
			"rate": row.rate,
			"qty": row.qty,
		})
		items_added += 1

	# Make sure at least one item was added
	if not items_added:
		frappe.throw(
			"No valid items found in Other Charges table."
		)

	# Calculate totals
	sales_invoice.calculate_taxes_and_totals()

	# Insert as Draft
	sales_invoice.insert()

	return {
		"name": sales_invoice.name,
		"doctype": "Sales Invoice",
		"customer": sales_invoice.customer,
		"company": sales_invoice.company,
		"project": sales_invoice.project,
		"items": [
			{
				"item_code": item.item_code,
				"qty": item.qty,
				"rate": item.rate
			}
			for item in sales_invoice.items
		]
	}

@frappe.whitelist()
def get_local_freight_charges(import_export_order):
	io = frappe.get_doc("Import-Export Order", import_export_order)

	# Find matching active Contract Agreement
	contract_name = frappe.db.get_value(
		"Tariff Charges",
		{
			"supplier": io.line,
			"is_active": 1
		},
		"name"
	)

	if not contract_name:
		frappe.throw(
			"Contract Agreement not found for selected Liner, or it is not Active."
		)

	contract = frappe.get_doc("Tariff Charges", contract_name)

	# Clear existing rows
	io.set("contract_agreement", [])

	# Copy child table rows
	for row in contract.table_vunr:
		qty = frappe.db.get_value(
			"Equipment Table1",
			{"type": row.eq_type,"parent":import_export_order},
			"nos"
		) or 0

		equipment_row = frappe.db.get_value( "Equipment Table1", { "type": row.eq_type, "parent": import_export_order }, ["name", "nos"], as_dict=True ) 
		
		if not equipment_row: 
			frappe.msgprint( 
				f"Container Type '{row.eq_type}' from the Tariff Charges " 
				f"does not match any Container Type in Import-Export Order '{import_export_order}'." 
			)
		
		if row.rate_type == "LUM":
			qty = 1
		
		io.append("contract_agreement", {
			"item_code": row.item_code,
			"item_name": row.item_name,
			"vc": row.vc,
			"c": row.c,
			"party": row.party,
			"rate_type": row.rate_type,
			"eq_type": row.eq_type,
			"rate": row.rate,
			"cost_price": row.cost_price,
			"line_cost_price": row.cost_price* flt(qty),
			"line_total_amount": row.rate * flt(qty)
		})
	io.db_set("grand_total", sum(row.line_total_amount for row in io.contract_agreement), update_modified=False)	
	io.db_set("total_liner", sum(row.line_cost_price for row in io.contract_agreement), update_modified=False)	
	
	io.save(ignore_permissions=True)

	return {
		"message": f"Local Freight Charges imported from {contract_name}."
	}

@frappe.whitelist()
def print_gate_pass(docname):

	doc = frappe.get_doc("Import-Export Order", docname)

	html = frappe.get_print(
		doctype="Import-Export Order",
		name=doc.name,
		print_format="Gate Pass"
	)

	return html

@frappe.whitelist()
def send_arrival_notification(import_export_order):
	order = frappe.get_doc("Import-Export Order", import_export_order)

	# Consignee email (as in your original logic)
	executive_email = frappe.db.get_value(
		"Party Table",
		{
			"party": "Consignee",
			"parent": import_export_order,
			"parenttype": "Import-Export Order",
		},
		"email",
	)

	if not executive_email:
		frappe.throw("No email address found for Consignee.")

	# Team emails: plain list of strings, no blanks, no duplicates
	team_emails = frappe.get_all(
		"Employee",
		filters={"company": order.company, "status": "Active"},
		pluck="company_email",
	)
	team_emails = list({e for e in team_emails})

	attachments = [
		frappe.attach_print(
			doctype="Import-Export Order",
			name=order.name,
			print_format="Cargo Arrival Notice",
			file_name=f"Arrival Notice - {order.name}",
			print_letterhead=True,
		)
	]

	frappe.sendmail(
		recipients=[executive_email],
		cc=team_emails,
		subject=f"Arrival Notice - {order.name}",
		message=f"""
			<p>Dear Sir/Madam,</p>

			<p>
				Please find attached the Cargo Arrival Notice for
				Vessel <b>{order.vessel or ""}</b>,
				Voyage <b>{order.voyage_no or ""}</b>,
				B/L No. <b>{order.bl_no or ""}</b>.
			</p>

			<p>
				Regards,<br>
				{frappe.db.get_value("Company", order.company, "company_name") or order.company}
			</p>
		""",
		attachments=attachments,
		reference_doctype="Import-Export Order",
		reference_name=order.name,
	)

	return True

def format_tracking_datetime(value):
	"""
	Convert tracking date text such as:
		Sailed On 20-09-2021
		Arrived On Sep 21 2021

	into:
		2021-09-20T12:00:00Z
		2021-09-21T12:00:00Z
	"""

	if value is None:
		return ""

	value = str(value).strip()

	if not value:
		return ""

	# Remove tracking prefixes
	value = re.sub(
		r"^(Sailed On|Arrived On)\s*",
		"",
		value,
		flags=re.IGNORECASE
	).strip()

	formats = (
		"%d-%m-%Y",
		"%d/%m/%Y",
		"%d-%b-%Y",
		"%b %d %Y",
		"%B %d %Y",
	)

	for fmt in formats:
		try:
			dt = datetime.strptime(value, fmt)

			# XML requires time, but tracking data has date only.
			# Use 12:00:00 as the default time.
			dt = dt.replace(
				hour=12,
				minute=0,
				second=0
			)

			return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

		except ValueError:
			continue

	return ""

def get_or_create_vessel(vessel_name, vessel_code=None):
	"""
	Find Vessel by code/name1 and create it if it does not exist.

	Returns the Vessel document name for Link field usage.
	"""

	vessel_name = (vessel_name or "").strip()
	vessel_code = (vessel_code or "").strip()

	if not vessel_name and not vessel_code:
		return None

	# ---------------------------------------------------------
	# 1. Check by code
	# ---------------------------------------------------------

	if vessel_code:
		existing = frappe.db.get_value(
			"Vessel",
			{"code": vessel_code},
			"name"
		)

		if existing:
			return existing

	# ---------------------------------------------------------
	# 2. Check by vessel name
	# ---------------------------------------------------------

	if vessel_name:
		existing = frappe.db.get_value(
			"Vessel",
			{"name1": vessel_name},
			"name"
		)

		if existing:
			return existing

	# ---------------------------------------------------------
	# 3. Create Vessel
	# ---------------------------------------------------------

	if not vessel_code:
		# Use vessel name as code if XML does not provide one
		vessel_code = re.sub(
			r"[^A-Za-z0-9]+",
			"",
			vessel_name
		).lower()

		if not vessel_code:
			frappe.throw(
				f"Unable to generate Vessel code for '{vessel_name}'."
			)

	# Make sure generated code is unique
	base_code = vessel_code
	counter = 1

	while frappe.db.exists("Vessel", vessel_code):
		vessel_code = f"{base_code}{counter}"
		counter += 1

	vessel = frappe.get_doc({
		"doctype": "Vessel",
		"name1": vessel_name,
		"code": vessel_code,
		"is_active": 1
	})

	vessel.insert(ignore_permissions=True)

	return vessel.name


def get_or_create_sea_port(port_code, port_name=None):
	"""
	Find Sea Ports by code/name and create it if required.

	Returns the Sea Ports document name for Link field usage.
	"""

	port_code = (port_code or "").strip()
	port_name = (port_name or "").strip()

	if not port_code and not port_name:
		return None

	# ---------------------------------------------------------
	# 1. Check by document name / code
	# ---------------------------------------------------------

	if port_code and frappe.db.exists("Sea Ports", port_code):
		return port_code

	# ---------------------------------------------------------
	# 2. Check by code field
	# ---------------------------------------------------------

	if port_code:
		existing = frappe.db.get_value(
			"Sea Ports",
			{"code": port_code},
			"name"
		)

		if existing:
			return existing

	# ---------------------------------------------------------
	# 3. Check by name1
	# ---------------------------------------------------------

	if port_name:
		existing = frappe.db.get_value(
			"Sea Ports",
			{"name1": port_name},
			"name"
		)

		if existing:
			return existing

	# ---------------------------------------------------------
	# 4. Create Sea Port
	# ---------------------------------------------------------

	if not port_code:
		port_code = re.sub(
			r"[^A-Za-z0-9]+",
			"",
			port_name
		).upper()

		if not port_code:
			frappe.throw(
				f"Unable to generate Port code for '{port_name}'."
			)

		base_code = port_code
		counter = 1

		while frappe.db.exists("Sea Ports", port_code):
			port_code = f"{base_code}{counter}"
			counter += 1

	port = frappe.get_doc({
		"doctype": "Sea Ports",
		"code": port_code,
		"name1": port_name,
		"is_active": 1
	})

	port.insert(ignore_permissions=True)

	return port.name

@frappe.whitelist()
def get_arrival_notice_print_formats():
	"""
	Return enabled Print Formats available for Import-Export Order.
	"""

	return frappe.get_all(
		"Print Format",
		filters={
			"doc_type": "Import-Export Order",
			"disabled": 0
		},
		fields=["name"],
		order_by="name asc"
	)

@frappe.whitelist()
def create_arrival_notice_attachment(import_export_order, print_format):
	"""
	Generate the selected Arrival Notice print format as PDF
	and attach it to the Import-Export Order.
	"""

	if not import_export_order:
		frappe.throw(_("Import-Export Order is required."))

	if not print_format:
		frappe.throw(_("Please select a Print Format."))

	# ---------------------------------------------------------
	# Validate document
	# ---------------------------------------------------------

	doc = frappe.get_doc(
		"Import-Export Order",
		import_export_order
	)

	# ---------------------------------------------------------
	# Validate Print Format
	# ---------------------------------------------------------

	print_format_exists = frappe.db.exists(
		"Print Format",
		{
			"name": print_format,
			"doc_type": "Import-Export Order",
			"disabled": 0
		}
	)

	if not print_format_exists:
		frappe.throw(
			_("Invalid Print Format: {0}").format(print_format)
		)

	# ---------------------------------------------------------
	# Generate Print HTML
	# ---------------------------------------------------------

	html = frappe.get_print(
		doctype="Import-Export Order",
		name=doc.name,
		print_format=print_format,
		letterhead=None
	)

	# ---------------------------------------------------------
	# Convert HTML to PDF
	# ---------------------------------------------------------

	from frappe.utils.pdf import get_pdf

	pdf_data = get_pdf(html)

	# ---------------------------------------------------------
	# File name
	# ---------------------------------------------------------

	file_name = f"Arrival Notice - {doc.name}.pdf"

	# ---------------------------------------------------------
	# Check existing attachment
	# ---------------------------------------------------------

	existing_file = frappe.db.get_value(
		"File",
		{
			"attached_to_doctype": "Import-Export Order",
			"attached_to_name": doc.name,
			"file_name": file_name
		},
		"name"
	)

	if existing_file:
		frappe.delete_doc(
			"File",
			existing_file,
			ignore_permissions=True
		)

	# ---------------------------------------------------------
	# Create File attachment
	# ---------------------------------------------------------

	file_doc = frappe.get_doc({
		"doctype": "File",
		"file_name": file_name,
		"is_private": 1,
		"content": pdf_data,
		"attached_to_doctype": "Import-Export Order",
		"attached_to_name": doc.name,
	})

	file_doc.insert(ignore_permissions=True)

	return {
		"file_name": file_name,
		"file_url": file_doc.file_url,
		"file_name": file_doc.name,
		"print_format": print_format
	}

@frappe.whitelist()
def create_arrival_stock_entries(
	import_export_order,
	warehouse,
	containers
):
	import json

	if not warehouse:
		frappe.throw(_("Warehouse is required."))

	if not containers:
		frappe.throw(_("Please select at least one container."))

	containers = json.loads(containers)

	order = frappe.get_doc(
		"Import-Export Order",
		import_export_order
	)

	created_entries = []

	for container in containers:

		container_no = container.get("container_no")

		if not container_no:
			continue

		# Container number must exist as an Item
		if not frappe.db.exists("Item", container_no):
			frappe.throw(
				_("Item {0} does not exist.").format(
					frappe.bold(container_no)
				)
			)

		stock_uom = frappe.db.get_value(
			"Item",
			container_no,
			"stock_uom"
		)

		stock_entry = frappe.new_doc("Stock Entry")

		stock_entry.stock_entry_type = "Material Receipt"

		stock_entry.company = order.company
		stock_entry.custom_importexport_order = order.name

		stock_entry.append("items", {
			"item_code": container_no,
			"qty": 1,
			"uom": stock_uom,
			"stock_uom": stock_uom,
			"rate": 0,
			"basic_rate": 0,
			"t_warehouse": warehouse,
			"allow_zero_valuation_rate": 1,
			"importexport_order": order.name
		})

		stock_entry.insert(
			ignore_permissions=True
		)

		stock_entry.submit()

		created_entries.append(
			stock_entry.name
		)

	frappe.db.commit()

	return created_entries

@frappe.whitelist()
def get_pending_arrival_containers(import_export_order):

	order = frappe.get_doc(
		"Import-Export Order",
		import_export_order
	)

	pending_containers = []

	for row in order.table_tvep:

		if not row.container_no:
			continue

		# Check whether an Arrival Stock Entry
		# already exists for this container
		existing = frappe.db.sql("""
			SELECT DISTINCT
				se.name
			FROM `tabStock Entry` se
			INNER JOIN `tabStock Entry Detail` sed
				ON sed.parent = se.name
			WHERE se.docstatus = 1
			  AND se.stock_entry_type = 'Material Receipt'
			  AND sed.item_code = %s
			LIMIT 1
		""", row.container_no, as_dict=True)

		if existing:
			continue

		pending_containers.append({
			"name": row.name,
			"container_no": row.container_no,
			"c_type": row.c_type,
			"seal_no": row.seal_no
		})

	return pending_containers

@frappe.whitelist()
def get_arrived_containers_for_delivery(import_export_order):
    """
    Return containers from table_tvep that:
      - have a submitted Material Receipt Stock Entry linked to this Import-Export Order
      - have NOT yet been delivered (delivery_order_printed != 1)

    Also include the source warehouse (t_warehouse of the receipt)
    so the Delivery Order Material Issue can pull from it.
    """
    order = frappe.get_doc("Import-Export Order", import_export_order)
    result = []

    for row in order.table_tvep:
        if not row.container_no:
            continue

        # Skip already delivered
        # if cint(row.delivery_order_printed) == 1:
        #     continue

        # Find the submitted Material Receipt linked to this order
        receipt = frappe.db.sql(
            """
            SELECT
                se.name AS stock_entry,
                sed.t_warehouse AS warehouse,
                sed.qty AS qty,
                sed.uom AS uom
            FROM `tabStock Entry` se
            INNER JOIN `tabStock Entry Detail` sed
                ON sed.parent = se.name
            WHERE se.docstatus = 1
              AND se.stock_entry_type = 'Material Receipt'
              AND se.custom_importexport_order = %(order)s
              AND sed.custom_importexport_order = %(order)s
              AND sed.item_code = %(item)s
            ORDER BY se.creation DESC
            LIMIT 1
            """,
            {
                "order": import_export_order,
                "item": row.container_no,
            },
            as_dict=True,
        )

        if not receipt:
            # No arrival yet — skip
            continue

        result.append(
            {
                "name": row.name,
                "container_no": row.container_no,
                "c_type": row.c_type,
                "seal_no": row.seal_no,
                "source_warehouse": receipt[0].warehouse or "",
                "source_stock_entry": receipt[0].stock_entry,
                "qty": receipt[0].qty or 1,
                "uom": receipt[0].uom or "Nos",
            }
        )

    return result


@frappe.whitelist()
def create_delivery_order_with_stock_entry(doctype, docname, containers):
    """
    Create:
      1. One Container Delivery Order with the selected containers.
      2. One combined Material Issue Stock Entry for all selected containers,
         sourced from each container's arrival warehouse.
    Marks delivery_order_printed = 1 on each selected child row.
    """
    if isinstance(containers, str):
        containers = json.loads(containers)

    if not containers:
        frappe.throw(_("Please select at least one container."))

    order = frappe.get_doc(doctype, docname)

    # ---------------------------------------------------------
    # Validate required header fields for Delivery Order
    # ---------------------------------------------------------
    if not order.company:
        frappe.throw(_("Company is missing in Import-Export Order."))
    if not order.customer:
        frappe.throw(_("Customer is missing in Import-Export Order."))

    # ---------------------------------------------------------
    # Build set of selected child row names
    # ---------------------------------------------------------
    selected_names = {c.get("name") for c in containers if c.get("name")}
    if not selected_names:
        frappe.throw(_("No valid containers were selected."))

    # ---------------------------------------------------------
    # Validate each selected container has a source warehouse
    # ---------------------------------------------------------
    for c in containers:
        if not c.get("source_warehouse"):
            frappe.throw(
                _(
                    "Source warehouse is missing for container {0}. "
                    "Please check the Arrival Stock Entry."
                ).format(c.get("container_no") or c.get("name"))
            )

    # ---------------------------------------------------------
    # 1. Create Container Delivery Order
    # ---------------------------------------------------------
    delivery_order = frappe.new_doc("Container Delivery Order")
    delivery_order.company = order.company
    delivery_order.customer = order.customer
    delivery_order.line = order.line
    delivery_order.posting_date = frappe.utils.today()
    delivery_order.location = order.location

    for src in order.table_tvep:
        if src.name not in selected_names:
            continue

        target_row = delivery_order.append("container_details", {})
        target_row.container_no = src.container_no
        target_row.c_type = src.c_type
        target_row.seal_no = src.seal_no
        target_row.tare_wt = src.tare_wt

    if not delivery_order.container_details:
        frappe.throw(_("No valid containers were selected."))

    delivery_order.insert(ignore_permissions=True)

    # Link DO back on Import-Export Order
    frappe.db.set_value(doctype, docname, "do_no", delivery_order.name)

    # ---------------------------------------------------------
    # 2. Create ONE combined Material Issue Stock Entry
    # ---------------------------------------------------------
    stock_entry = frappe.new_doc("Stock Entry")
    stock_entry.stock_entry_type = "Material Issue"
    stock_entry.purpose = "Material Issue"
    stock_entry.company = order.company
    stock_entry.custom_importexport_order = order.name

    for c in containers:
        container_no = c.get("container_no")
        source_warehouse = c.get("source_warehouse")

        if not container_no or not source_warehouse:
            continue

        if not frappe.db.exists("Item", container_no):
            frappe.throw(
                _("Item {0} does not exist.").format(container_no)
            )

        stock_uom = frappe.db.get_value(
            "Item", container_no, "stock_uom"
        ) or "Nos"

        stock_entry.append(
            "items",
            {
                "item_code": container_no,
                "qty": c.get("qty") or 1,
                "uom": c.get("uom") or stock_uom,
                "stock_uom": stock_uom,
                "s_warehouse": source_warehouse,
                "allow_zero_valuation_rate": 1,
                "importexport_order": order.name,
            },
        )

    if not stock_entry.items:
        frappe.throw(
            _("No valid containers were selected for Stock Entry.")
        )

    stock_entry.insert(ignore_permissions=True)
    stock_entry.submit()

    # ---------------------------------------------------------
    # 3. Mark selected containers as delivered
    # ---------------------------------------------------------
    for row_name in selected_names:
        if frappe.db.exists("Equipment Table2", row_name):
            frappe.db.set_value(
                "Equipment Table2",
                row_name,
                "delivery_order_printed",
                1,
                update_modified=False,
            )

    frappe.db.commit()

    return {
        "delivery_order": delivery_order.name,
        "stock_entries": [stock_entry.name],
    }

@frappe.whitelist()
def get_delivered_containers_for_return(import_export_order):
    """
    Return containers from table_tvep that:
      - have been delivered (delivery_order_printed = 1)
      - have NOT yet been returned (no submitted Material Receipt
        linked to this order AFTER the delivery)

    Also include the warehouse used in the delivery Material Issue
    so the dialog can show where the container came from.
    """
    order = frappe.get_doc("Import-Export Order", import_export_order)
    result = []

    for row in order.table_tvep:
        if not row.container_no:
            continue

        # Must have been delivered first
        if cint(row.delivery_order_printed) != 1:
            continue

        # Get the Delivery (Material Issue) Stock Entry for this container
        delivery = frappe.db.sql(
            """
            SELECT
                se.name AS stock_entry,
                sed.s_warehouse AS warehouse,
                sed.qty AS qty,
                sed.uom AS uom
            FROM `tabStock Entry` se
            INNER JOIN `tabStock Entry Detail` sed
                ON sed.parent = se.name
            WHERE se.docstatus = 1
              AND se.stock_entry_type = 'Material Issue'
              AND se.custom_importexport_order = %(order)s
              AND sed.importexport_order = %(order)s
              AND sed.item_code = %(item)s
            ORDER BY se.creation DESC
            LIMIT 1
            """,
            {
                "order": import_export_order,
                "item": row.container_no,
            },
            as_dict=True,
        )

        if not delivery:
            # delivered flag is on, but no Material Issue found — skip
            continue

        # Skip if already returned (a Material Receipt exists AFTER the issue)
        returned = frappe.db.sql(
            """
            SELECT se.name
            FROM `tabStock Entry` se
            INNER JOIN `tabStock Entry Detail` sed
                ON sed.parent = se.name
            WHERE se.docstatus = 1
              AND se.stock_entry_type = 'Material Receipt'
              AND se.custom_importexport_order = %(order)s
              AND sed.importexport_order = %(order)s
              AND sed.item_code = %(item)s
              AND se.creation > (
                  SELECT creation FROM `tabStock Entry` WHERE name = %(issue)s
              )
            LIMIT 1
            """,
            {
                "order": import_export_order,
                "item": row.container_no,
                "issue": delivery[0].stock_entry,
            },
            as_dict=True,
        )

        if returned:
            continue

        result.append(
            {
                "name": row.name,
                "container_no": row.container_no,
                "c_type": row.c_type,
                "seal_no": row.seal_no,
                "source_warehouse": delivery[0].warehouse or "",
                "source_stock_entry": delivery[0].stock_entry,
                "qty": delivery[0].qty or 1,
                "uom": delivery[0].uom or "Nos",
            }
        )

    return result

@frappe.whitelist()
def create_return_stock_entries(import_export_order, warehouse, containers):
    """
    Create one Material Receipt Stock Entry per returned container,
    pulling them back into the chosen warehouse.
    """
    if not warehouse:
        frappe.throw(_("Warehouse is required."))

    if not containers:
        frappe.throw(_("Please select at least one container."))

    if isinstance(containers, str):
        containers = json.loads(containers)

    order = frappe.get_doc("Import-Export Order", import_export_order)

    created_entries = []

    for container in containers:
        container_no = container.get("container_no")

        if not container_no:
            continue

        if not frappe.db.exists("Item", container_no):
            frappe.throw(
                _("Item {0} does not exist.").format(
                    frappe.bold(container_no)
                )
            )

        stock_uom = frappe.db.get_value(
            "Item", container_no, "stock_uom"
        ) or "Nos"

        stock_entry = frappe.new_doc("Stock Entry")
        stock_entry.stock_entry_type = "Material Receipt"
        stock_entry.company = order.company
        stock_entry.custom_importexport_order = order.name

        stock_entry.append(
            "items",
            {
                "item_code": container_no,
                "qty": container.get("qty") or 1,
                "uom": container.get("uom") or stock_uom,
                "stock_uom": stock_uom,
                "rate": 0,
                "basic_rate": 0,
                "t_warehouse": warehouse,
                "allow_zero_valuation_rate": 1,
                "importexport_order": order.name,
            },
        )

        stock_entry.insert(ignore_permissions=True)
        stock_entry.submit()

        created_entries.append(stock_entry.name)

    frappe.db.commit()

    return created_entries

@frappe.whitelist()
def get_returned_containers_for_export(import_export_order):
    """
    Return containers from table_tvep that:
      - have been delivered (delivery_order_printed = 1)
      - have been returned (a Material Receipt exists AFTER the delivery)
      - have NOT yet been exported (no Material Issue after the return)

    Also include the warehouse from the Return Material Receipt
    so the Export Material Issue can pull from it.
    """
    order = frappe.get_doc("Import-Export Order", import_export_order)
    result = []

    for row in order.table_tvep:
        if not row.container_no:
            continue

        # Must have been delivered
        if cint(row.delivery_order_printed) != 1:
            continue

        # Find the latest delivery (Material Issue)
        delivery = frappe.db.sql(
            """
            SELECT se.name AS stock_entry, se.creation AS creation
            FROM `tabStock Entry` se
            INNER JOIN `tabStock Entry Detail` sed
                ON sed.parent = se.name
            WHERE se.docstatus = 1
              AND se.stock_entry_type = 'Material Issue'
              AND se.custom_importexport_order = %(order)s
              AND sed.importexport_order = %(order)s
              AND sed.item_code = %(item)s
            ORDER BY se.creation DESC
            LIMIT 1
            """,
            {
                "order": import_export_order,
                "item": row.container_no,
            },
            as_dict=True,
        )

        if not delivery:
            continue

        # Find the return (Material Receipt AFTER the delivery)
        returned = frappe.db.sql(
            """
            SELECT
                se.name AS stock_entry,
                se.creation AS creation,
                sed.t_warehouse AS warehouse,
                sed.qty AS qty,
                sed.uom AS uom
            FROM `tabStock Entry` se
            INNER JOIN `tabStock Entry Detail` sed
                ON sed.parent = se.name
            WHERE se.docstatus = 1
              AND se.stock_entry_type = 'Material Receipt'
              AND se.custom_importexport_order = %(order)s
              AND sed.importexport_order = %(order)s
              AND sed.item_code = %(item)s
              AND se.creation > %(delivery_creation)s
            ORDER BY se.creation DESC
            LIMIT 1
            """,
            {
                "order": import_export_order,
                "item": row.container_no,
                "delivery_creation": delivery[0].creation,
            },
            as_dict=True,
        )

        if not returned:
            # Not yet returned — skip
            continue

        # Skip if already exported (Material Issue AFTER the return)
        exported = frappe.db.sql(
            """
            SELECT se.name
            FROM `tabStock Entry` se
            INNER JOIN `tabStock Entry Detail` sed
                ON sed.parent = se.name
            WHERE se.docstatus = 1
              AND se.stock_entry_type = 'Material Issue'
              AND se.custom_importexport_order = %(order)s
              AND sed.importexport_order = %(order)s
              AND sed.item_code = %(item)s
              AND se.creation > %(return_creation)s
            LIMIT 1
            """,
            {
                "order": import_export_order,
                "item": row.container_no,
                "return_creation": returned[0].creation,
            },
            as_dict=True,
        )

        if exported:
            continue

        result.append(
            {
                "name": row.name,
                "container_no": row.container_no,
                "c_type": row.c_type,
                "seal_no": row.seal_no,
                "source_warehouse": returned[0].warehouse or "",
                "source_stock_entry": returned[0].stock_entry,
                "qty": returned[0].qty or 1,
                "uom": returned[0].uom or "Nos",
            }
        )

    return result

@frappe.whitelist()
def create_export_stock_entries(import_export_order, warehouse, containers):
    """
    Create one Material Issue Stock Entry per exported container,
    pulling them out of the selected source warehouse.
    """
    if not warehouse:
        frappe.throw(_("Source Warehouse is required."))

    if not containers:
        frappe.throw(_("Please select at least one container."))

    if isinstance(containers, str):
        containers = json.loads(containers)

    order = frappe.get_doc("Import-Export Order", import_export_order)

    created_entries = []

    for container in containers:
        container_no = container.get("container_no")

        if not container_no:
            continue

        if not frappe.db.exists("Item", container_no):
            frappe.throw(
                _("Item {0} does not exist.").format(
                    frappe.bold(container_no)
                )
            )

        stock_uom = frappe.db.get_value(
            "Item", container_no, "stock_uom"
        ) or "Nos"

        stock_entry = frappe.new_doc("Stock Entry")
        stock_entry.stock_entry_type = "Material Issue"
        stock_entry.company = order.company
        stock_entry.custom_importexport_order = order.name

        stock_entry.append(
            "items",
            {
                "item_code": container_no,
                "qty": container.get("qty") or 1,
                "uom": container.get("uom") or stock_uom,
                "stock_uom": stock_uom,
                "s_warehouse": warehouse,
                "allow_zero_valuation_rate": 1,
                "importexport_order": order.name,
            },
        )

        stock_entry.insert(ignore_permissions=True)
        stock_entry.submit()

        created_entries.append(stock_entry.name)

    frappe.db.commit()

    return created_entries
