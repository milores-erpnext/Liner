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

class ImportExportOrder(Document):
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
					"is_stock_item": 0,
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
			print(f"\n\n\n{response.text}\n\n\n")
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
			# ignore_mandatory: company/customer etc. aren't in the XML and
			# will be filled in manually after import
			doc.insert(ignore_permissions=True, ignore_mandatory=True)
			created.append({"name": doc.name, "line_booking_ref": tas_booking_no})
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Import-Export Order Bulk XML Import")
			errors.append(tas_booking_no)

	return {"created": created, "skipped": skipped, "errors": errors}


def _extract_vessel_values(root):
	"""Attributes on the root <Vessel> element — shared by every Booking."""
	values = {}
	root_field_map = {
		"VesselName": "vessel",
		"Voyage": "voyage_no",
	}
	for xml_attr, fieldname in root_field_map.items():
		value = root.attrib.get(xml_attr)
		if value:
			values[fieldname] = value.strip()
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
			hub_xml_map = {
				"Place of Receipt": "PlaceofReceipt",
				"Port of Loading": "PortOfLoading",
				"Port of Discharge": "PortOfDischarge",
				"Place of Delivery": "FinalPlaceOfDelivery",
			}
			routing_rows = []
			for hub_label, xml_attr in hub_xml_map.items():
				hub_value = routing.attrib.get(xml_attr)
				routing_rows.append({"hub": hub_label, "hub_name": (hub_value or "").strip()})
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
def get_local_freight_charges(import_export_order):
	io = frappe.get_doc("Import-Export Order", import_export_order)

	# Find matching active Contract Agreement
	contract_name = frappe.db.get_value(
		"Contract Agreement",
		{
			"customer": io.customer,
			"supplier": io.line,
			"is_active": 1
		},
		"name"
	)

	if not contract_name:
		frappe.throw(
			"Contract Agreement not found for selected Customer and Supplier, or it is not Active."
		)

	contract = frappe.get_doc("Contract Agreement", contract_name)

	# Clear existing rows
	io.set("contract_agreement", [])

	# Copy child table rows
	for row in contract.table_vunr:
		io.append("contract_agreement", {
			"item_code": row.item_code,
			"item_name": row.item_name,
			"vc": row.vc,
			"c": row.c,
			"party": row.party,
			"rate_type": row.rate_type,
			"eq_type": row.eq_type,
			"qty": row.qty,
			"rate": row.rate,
			"amount": row.amount,
			"line_cost_price": row.line_cost_price,
		})
	io.db_set("grand_total", sum(row.amount+row.line_cost_price for row in io.contract_agreement), update_modified=False)	
	io.save(ignore_permissions=True)

	return {
		"message": f"Local Freight Charges imported from {contract_name}."
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
                "validation": {
                    "docstatus": ["=", 1]
                }
            }
        },
        target_doc,
        set_missing_values
    )

    return doc
