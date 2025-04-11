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
import sys
import datetime
from datetime import timedelta
import logging
import binascii

from . import zklib
from .zkconst import *
from struct import unpack
from odoo import api, fields, models
from odoo import _
from odoo.exceptions import UserError, ValidationError
from itertools import groupby

_logger = logging.getLogger(__name__)
try:
    from zk import ZK, const
except ImportError:
    _logger.error("Please Install pyzk library.")

_logger = logging.getLogger(__name__)


class HrAttendance(models.Model):
    _inherit = "hr.attendance"

    device_id = fields.Char(string="Biometric Device ID")

    @api.constrains("check_in", "check_out", "employee_id")
    def _check_validity(self):
        """Customization
        To remove the constraint checking
        """
        # return False

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
        except:
            return False

    def clear_attendance(self):
        for info in self:
            try:
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
                    raise UserError(_("Please install it with 'pip3 install pyzk'."))
                conn = self.device_connect(zk)
                if conn:
                    conn.enable_device()
                    clear_data = zk.get_attendance()
                    if clear_data:
                        self._cr.execute("""delete from zk_machine_attendance""")
                        # conn.clear_attendance()
                        conn.disconnect()
                    else:
                        raise UserError(
                            _(
                                "Unable to clear Attendance log. Are you sure attendance log is not empty."
                            )
                        )
                else:
                    raise UserError(
                        _(
                            "Unable to connect to Attendance Device. Please use Test Connection button to verify."
                        )
                    )
            except:
                raise ValidationError(
                    "Unable to clear Attendance log. Are you sure attendance device is connected & record is not empty."
                )

    def getSizeUser(self, zk):
        """Checks a returned packet to see if it returned CMD_PREPARE_DATA,
        indicating that data packets are to be sent

        Returns the amount of bytes that are going to be sent"""
        command = unpack("HHHH", zk.data_recv[:8])[0]
        if command == CMD_PREPARE_DATA:
            size = unpack("I", zk.data_recv[8:12])[0]
            print("size", size)
            return size
        else:
            return False

    def zkgetuser(self, zk):
        """Start a connection with the time clock"""
        try:
            users = zk.get_users()
            return users
        except:
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
                        "Pyzk module not Found. Please install it with 'pip3 install pyzk'."
                    )
                )
            conn = self.device_connect(zk)
            if conn:
                # conn.disable_device() #Device Cannot be used during this time.
                try:
                    device_users = conn.get_users()
                except:
                    device_users = False

                if not device_users:
                    raise UserError(
                        _(
                            "There is no user created yet. Please create at least one user."
                        )
                    )

                try:
                    attendance = conn.get_attendance()
                    attendance.sort(key=lambda x: int(x.user_id), reverse=False)
                    grouped_attendances = [
                        list(group)
                        for key, group in groupby(
                            iterable=attendance, key=lambda x: x.user_id
                        )
                    ]
                except Exception as e:
                    attendance = False
                    grouped_attendances = False
                # return;
                if grouped_attendances:
                    non_existence_employees = []

                    for i, each_user_attendances in enumerate(grouped_attendances):

                        each_user_attendances.sort(key=lambda x: x.timestamp)
                        for j, attendance in enumerate(each_user_attendances):
                            employee = self.env["hr.employee"].search(
                                [("device_id", "=", attendance.user_id)]
                            )

                            if employee:
                                # Converting datetime
                                atten_time = attendance.timestamp
                                atten_time = datetime.strptime(
                                    atten_time.strftime("%Y-%m-%d %H:%M:%S"),
                                    "%Y-%m-%d %H:%M:%S",
                                )
                                local_tz = pytz.timezone(
                                    self.env.user.partner_id.tz or "GMT"
                                )
                                local_dt = local_tz.localize(atten_time, is_dst=None)
                                utc_dt = local_dt.astimezone(pytz.utc)
                                utc_dt = utc_dt.strftime("%Y-%m-%d %H:%M:%S")
                                atten_time = datetime.strptime(
                                    utc_dt, "%Y-%m-%d %H:%M:%S"
                                )
                                atten_time_str = fields.Datetime.to_string(atten_time)
                                # End Converting datetime
                                db_attendances = att_obj.search(
                                    domain=[
                                        ("employee_id", "=", employee[0].id),
                                        "|",
                                        ("check_in", "<=", atten_time_str),
                                        ("check_out", "=", False),
                                    ],
                                    order="id",
                                )

                                # if local_dt.strftime('%Y-%m-%d') <= "2023-02-10":
                                #     continue

                                # zk_machine_table
                                # searching for record
                                duplicate_atten_ids = zk_attendance.search(
                                    [
                                        ("device_id", "=", attendance.user_id),
                                        ("punching_time", "=", atten_time),
                                    ]
                                )

                                if not duplicate_atten_ids:
                                    zk_attendance.create(
                                        {
                                            "employee_id": employee[0].id,
                                            "device_id": attendance.user_id,
                                            "attendance_type": str(attendance.status),
                                            "punch_type": str(attendance.punch),
                                            "punching_time": atten_time,
                                            "address_id": info.address_id.id,
                                        }
                                    )

                                    # if no record found
                                    if not db_attendances:
                                        att_obj.create(
                                            {
                                                "employee_id": employee[0].id,
                                                "check_in": atten_time,
                                            }
                                        )
                                    else:
                                        record = db_attendances[-1]
                                        check_in = record.check_in
                                        check_out = record.check_out

                                        if check_out == False:
                                            if local_dt.date() == check_in.astimezone(
                                                local_tz
                                            ).date() or (
                                                (    
                                                    local_dt
                                                    - check_in.astimezone(local_tz)
                                                ) / timedelta(hours=1)
                                                < 24
                                            ):
                                                record.write({"check_out": atten_time})
                                                continue
                                        # Fix duplicated checked in
                                        # if check_out and current_attendance at the same day
                                        # overwrite it
                                        elif (
                                            check_out.astimezone(local_tz).date()
                                            == local_dt.date()
                                        ):
                                            record.write({"check_out": atten_time})
                                            continue

                                        # if nothing above match, create a new record
                                        att_obj.create(
                                            (
                                                {
                                                    "employee_id": employee[0].id,
                                                    "check_in": atten_time,
                                                }
                                            )
                                        )
                                        continue
                            else:
                                ### Exclude inactive employee
                                inactive_employee = self.env["hr.employee"].search(
                                    [
                                        ("device_id", "=", attendance.user_id),
                                        ("active", "=", False)
                                    ]
                                )
                                for device_user in device_users:                              
                                    if not inactive_employee and device_user.user_id == attendance.user_id:
                                        non_existence_employees.append(device_user)
                                break

                    ### Raise Non Existence Employees Error
                    if non_existence_employees:
                        """
                        If there is no biometric device id found in employees,
                        raise an exception/warning instead of create the new employee
                        """
                        self.non_existence_employee_error(non_existence_employees)

                    # zk.enableDevice()
                    conn.disconnect
                    return True
                else:
                    raise UserError(
                        _("Unable to get the attendance log, please try again later.")
                    )
            else:
                raise UserError(
                    _(
                        "Unable to connect, please check the parameters and network connections."
                    )
                )

    def non_existence_employee_error(self, non_existence_employees=[]):
        logs = ""
        for index, device_user in enumerate(non_existence_employees):
            logs += "\r\n======\r\n{}. Name: {}\r\nBiometric Device ID: {}".format(
                index + 1, device_user.name, device_user.user_id
            )

        raise UserError(
            _(
                "The following user(s) is haven't linked or created{}\r\n======".format(
                    logs
                )
            )
        )
