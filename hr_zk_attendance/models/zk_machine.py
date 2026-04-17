# -*- coding: utf-8 -*-
###################################################################################
#
#    Cybrosys Technologies Pvt. Ltd.
#    Copyright (C) 2022-TODAY Cybrosys Technologies(<http://www.cybrosys.com>).
#    Author: cybrosys(<https://www.cybrosys.com>)
#
#    This program is free software: you can modify
#    it under the terms of the GNU Affero General Public License (AGPL) as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
###################################################################################
import pytz
from datetime import datetime, timedelta
import logging
from struct import unpack
from itertools import groupby

from . import zklib
from .zkconst import *
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

# FIX: removed duplicate _logger definition
_logger = logging.getLogger(__name__)

try:
    from zk import ZK, const
except ImportError:
    _logger.error("Please Install pyzk library.")


class HrAttendance(models.Model):
    _inherit = "hr.attendance"

    device_id = fields.Char(string="Biometric Device ID")

    @api.constrains("check_in", "check_out", "employee_id")
    def _check_validity(self):
        """Customization: constraint checking removed."""

    def _check_validity_check_in_check_out(self):
        """"""


class ZkMachine(models.Model):
    _name = "zk.machine"

    name = fields.Char(string="Machine IP", required=True)
    port_no = fields.Integer(string="Port No", required=True)
    address_id = fields.Many2one("res.partner", string="Working Address")
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        default=lambda self: self.env.user.company_id.id,
    )

    def device_connect(self, zk):
        try:
            conn = zk.connect()
            return conn
        except Exception:
            return False

    def clear_attendance(self):
        for info in self:
            machine_ip = info.name
            zk_port = info.port_no
            timeout = 30
            try:
                zk = ZK(
                    machine_ip,
                    port=zk_port,
                    timeout=timeout,
                    password=0,
                    force_udp=False,
                    ommit_ping=False,
                )
            except NameError:
                raise UserError(_("Please install pyzk with 'pip3 install pyzk'."))

            conn = self.device_connect(zk)
            if not conn:
                raise UserError(
                    _(
                        "Unable to connect to Attendance Device. "
                        "Please use the Test Connection button to verify."
                    )
                )

            try:
                conn.enable_device()
                clear_data = zk.get_attendance()
                if clear_data:
                    self._cr.execute("DELETE FROM zk_machine_attendance")
                    # conn.clear_attendance()
                else:
                    raise UserError(
                        _(
                            "Unable to clear Attendance log. "
                            "Are you sure the attendance log is not empty?"
                        )
                    )
            except UserError:
                raise
            except Exception as e:
                _logger.exception("Error clearing attendance: %s", e)
                raise ValidationError(
                    _(
                        "Unable to clear Attendance log. "
                        "Are you sure the attendance device is connected and the record is not empty?"
                    )
                )
            finally:
                conn.disconnect()

    def getSizeUser(self, zk):
        """Checks a returned packet for CMD_PREPARE_DATA and returns byte count."""
        command = unpack("HHHH", zk.data_recv[:8])[0]
        if command == CMD_PREPARE_DATA:
            size = unpack("I", zk.data_recv[8:12])[0]
            _logger.debug("getSizeUser size: %s", size)
            return size
        return False

    def zkgetuser(self, zk):
        """Fetch users from the time clock device."""
        try:
            users = zk.get_users()
            return users
        except Exception:
            return False

    @api.model
    def cron_download(self):
        machines = self.env["zk.machine"].search([])
        for machine in machines:
            machine.download_attendance()

    def download_attendance(self):
        _logger.info("++++++++++++Cron Executed++++++++++++++++++++++")
        zk_attendance = self.env["zk.machine.attendance"]
        att_obj = self.env["hr.attendance"]

        for info in self:
            machine_ip = info.name
            zk_port = info.port_no
            timeout = 15

            try:
                zk = ZK(
                    machine_ip,
                    port=zk_port,
                    timeout=timeout,
                    password=0,
                    force_udp=False,
                    ommit_ping=False,
                )
            except NameError:
                raise UserError(
                    _(
                        "Pyzk module not found. "
                        "Please install it with 'pip3 install pyzk'."
                    )
                )

            conn = self.device_connect(zk)
            if not conn:
                raise UserError(
                    _(
                        "Unable to connect. "
                        "Please check the parameters and network connections."
                    )
                )

            try:
                try:
                    device_users = conn.get_users()
                except Exception:
                    device_users = False

                if not device_users:
                    raise UserError(
                        _(
                            "There are no users on the device yet. "
                            "Please create at least one user."
                        )
                    )

                try:
                    attendance = conn.get_attendance()
                except Exception as e:
                    _logger.exception("Failed to fetch attendance: %s", e)
                    attendance = []

                if not attendance:
                    raise UserError(
                        _("Unable to get the attendance log, please try again later.")
                    )

                # Pre-fetch lookups to avoid per-record DB queries
                all_employees = self.env["hr.employee"].search(
                    [("device_id", "!=", False)]
                )
                employee_map = {emp.device_id: emp for emp in all_employees}

                # Also pre-fetch inactive employees to quickly skip them
                inactive_employees = self.env["hr.employee"].search(
                    [("device_id", "!=", False), ("active", "=", False)]
                )
                inactive_device_ids = {emp.device_id for emp in inactive_employees}

                # Build a map of device_user.user_id -> device_user for fast lookup
                device_user_map = {str(u.user_id): u for u in device_users}

                # Pre-fetch all existing zk_attendance punch times
                # keyed by (device_id, punching_time) to avoid per-record searches.
                existing_zk = zk_attendance.search([])
                existing_zk_set = {
                    (r.device_id, fields.Datetime.to_string(r.punching_time))
                    for r in existing_zk
                }

                local_tz = pytz.timezone(self.env.user.partner_id.tz or "GMT")

                attendance.sort(key=lambda x: int(x.user_id))
                grouped_attendances = [
                    list(group)
                    for _, group in groupby(attendance, key=lambda x: x.user_id)
                ]

                non_existence_employees = []

                for each_user_attendances in grouped_attendances:
                    each_user_attendances.sort(key=lambda x: x.timestamp)

                    # All records in this group share the same user_id
                    user_id = each_user_attendances[0].user_id
                    employee = employee_map.get(str(user_id))

                    if not employee:
                        # Skip inactive; collect truly unknown users
                        if str(user_id) not in inactive_device_ids:
                            device_user = device_user_map.get(str(user_id))
                            if device_user:
                                non_existence_employees.append(device_user)
                        continue

                    # Fetch all existing hr.attendance records for
                    # this employee once per user, not once per punch record.
                    db_attendances = att_obj.search(
                        [("employee_id", "=", employee.id)],
                        order="id",
                    )

                    for attendance_rec in each_user_attendances:
                        atten_time = datetime.strptime(
                            attendance_rec.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                            "%Y-%m-%d %H:%M:%S",
                        )
                        local_dt = local_tz.localize(atten_time, is_dst=None)
                        utc_dt = local_dt.astimezone(pytz.utc)
                        atten_time = datetime.strptime(
                            utc_dt.strftime("%Y-%m-%d %H:%M:%S"),
                            "%Y-%m-%d %H:%M:%S",
                        )
                        atten_time_str = fields.Datetime.to_string(atten_time)

                        if (str(user_id), atten_time_str) in existing_zk_set:
                            continue

                        # Record is new — insert into zk_attendance staging table
                        zk_attendance.create(
                            {
                                "employee_id": employee.id,
                                "device_id": str(user_id),
                                "attendance_type": str(attendance_rec.status),
                                "punch_type": str(attendance_rec.punch),
                                "punching_time": atten_time,
                                "address_id": info.address_id.id,
                            }
                        )
                        # Add to the in-memory set so subsequent records in this
                        # batch don't trigger duplicate DB inserts
                        existing_zk_set.add((str(user_id), atten_time_str))

                        # Get the single most recent hr.attendance record whose
                        # check_in is before the current punch time.
                        prior = [r for r in db_attendances if r.check_in <= atten_time]
                        record = prior[-1] if prior else None

                        if record:
                            check_in = record.check_in
                            check_out = record.check_out
                            check_in_local = check_in.astimezone(local_tz)

                            same_day = local_dt.date() == check_in_local.date()
                            within_24h = (local_dt - check_in_local) / timedelta(
                                hours=1
                            ) < 24
                            open_and_recent = not check_out and (same_day or within_24h)
                            same_day_checkout = (
                                check_out
                                and check_out.astimezone(local_tz).date()
                                == local_dt.date()
                            )

                            if open_and_recent or same_day_checkout:
                                record.write({"check_out": atten_time})
                                continue
                            # else: record is too old or already closed on a different
                            # day — fall through to create a new check-in below

                        # No suitable open record found — create a new check-in
                        new_rec = att_obj.create(
                            {
                                "employee_id": employee.id,
                                "check_in": atten_time,
                            }
                        )
                        db_attendances |= new_rec

                if non_existence_employees:
                    self.non_existence_employee_error(non_existence_employees)

            finally:
                conn.disconnect()

        return True

    def non_existence_employee_error(self, non_existence_employees=None):
        if not non_existence_employees:
            return
        for index, device_user in enumerate(non_existence_employees):
            _logger.warning(
                "Biometric ID: {} are not linked to employee: {}".format(
                    device_user.user_id, device_user.name
                )
            )
