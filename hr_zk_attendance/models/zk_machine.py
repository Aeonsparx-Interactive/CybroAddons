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
import logging
from struct import unpack
from itertools import groupby

from . import zk_pairing
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
    machine_timezone = fields.Char(
        string="Machine Timezone",
        required=True,
        default=lambda self: self.env.user.tz or "Asia/Kuala_Lumpur",
        help="IANA timezone of the wall clock shown on the biometric device. "
        "Punch timestamps from the device are interpreted in this timezone. "
        "All pairing decisions are made in this timezone, independent of "
        "the Odoo server's system timezone.",
    )
    cutoff_hour = fields.Integer(
        string="Night-Shift Cutoff Hour",
        default=6,
        help="Punches on a new calendar day *before* this hour close the "
        "previous day's open record (night-shift check-out). Punches at or "
        "after this hour start a new check-in and any stale open record is "
        "force-closed at this hour.",
    )
    last_sync_time = fields.Datetime(
        string="Last Synced Punch",
        readonly=True,
        copy=False,
        help="Watermark (UTC): punches at/before this time are staged but "
        "never re-paired into HR attendance. This makes wiping the staging "
        "table ('Clear Data') safe: the next sync re-downloads the device "
        "history without rewriting existing attendance records.",
    )

    @api.constrains("machine_timezone")
    def _check_machine_timezone(self):
        for rec in self:
            try:
                pytz.timezone(rec.machine_timezone)
            except pytz.exceptions.UnknownTimeZoneError:
                raise ValidationError(
                    _("Unknown timezone: %s") % rec.machine_timezone
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

                # All punch/pairing math happens in the machine's wall-clock
                # timezone.  hr.attendance stores naive UTC.
                wall_tz = pytz.timezone(info.machine_timezone)
                watermark = info.last_sync_time  # naive UTC or None
                max_punch_utc = None

                def _uid_key(punch):
                    # pyzk user_id may be str or int; sort/group consistently.
                    try:
                        return int(punch.user_id)
                    except (TypeError, ValueError):
                        return punch.user_id

                attendance.sort(key=_uid_key)
                grouped_attendances = [
                    list(group)
                    for _, group in groupby(attendance, key=_uid_key)
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

                    for attendance_rec in each_user_attendances:
                        wall_local = zk_pairing.to_wall_local(
                            attendance_rec.timestamp, wall_tz
                        )
                        atten_time = zk_pairing.to_naive_utc(wall_local)
                        atten_time_str = fields.Datetime.to_string(atten_time)

                        if (str(user_id), atten_time_str) in existing_zk_set:
                            continue

                        # Record is new — insert into zk_attendance staging
                        # table.  Staging insert and HR pairing commit in the
                        # same transaction, so "already staged" always implies
                        # "already paired".
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
                        if max_punch_utc is None or atten_time > max_punch_utc:
                            max_punch_utc = atten_time

                        # Watermark: punches at/before the last synced punch are
                        # history being re-staged (e.g. after 'Clear Data' wiped
                        # the staging table).  They are never re-paired, which
                        # prevents the re-sync from rewriting/overwriting
                        # existing hr.attendance records.
                        if watermark and atten_time <= watermark:
                            continue

                        # Pair with the employee's newest OPEN record
                        # (check_out unset).  Punches toggle: they close the
                        # open record, otherwise they start a new check-in.
                        open_rec = att_obj.search(
                            [
                                ("employee_id", "=", employee.id),
                                ("check_out", "=", False),
                            ],
                            order="check_in desc",
                            limit=1,
                        )
                        if open_rec:
                            ci_local = zk_pairing.to_aware_utc(
                                open_rec.check_in
                            ).astimezone(wall_tz)
                            action = zk_pairing.pairing_decision(
                                wall_local, ci_local, info.cutoff_hour
                            )
                            if action == "ignore":
                                _logger.warning(
                                    "Punch %s for device %s (%s) is at/before "
                                    "open record %s check_in %s; staged only.",
                                    atten_time_str,
                                    user_id,
                                    employee.name,
                                    open_rec.id,
                                    open_rec.check_in,
                                )
                                continue
                            if action == "close":
                                open_rec.write({"check_out": atten_time})
                                continue
                            # 'cutoff': stale open record from a previous day —
                            # force-close it at the daily cutoff, the punch
                            # starts a new working day.
                            close_utc = zk_pairing.to_naive_utc(
                                zk_pairing.cutoff_moment(
                                    ci_local, wall_tz, info.cutoff_hour
                                )
                            )
                            open_rec.write(
                                {"check_out": max(close_utc, open_rec.check_in)}
                            )
                        att_obj.create(
                            {
                                "employee_id": employee.id,
                                "check_in": atten_time,
                            }
                        )

                if max_punch_utc and (not watermark or max_punch_utc > watermark):
                    info.last_sync_time = max_punch_utc

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
