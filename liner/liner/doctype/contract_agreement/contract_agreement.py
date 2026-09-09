# Copyright (c) 2026, Milores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class ContractAgreement(Document):
	def validate(self):
		self.grand_total = 0

		if self.table_vunr:
			for row in self.table_vunr:
				self.grand_total += row.amount or 0