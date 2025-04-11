# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class EmployeePublic(models.Model):
    _inherit = "hr.employee.public"

    device_id = fields.Char(string="Biometric Device ID")
