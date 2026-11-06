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
"""Pure punch-pairing logic for ZK biometric punches.

No Odoo imports here: the decision rules are unit-testable standalone.

Timezone convention (single source of truth):
  * Device punch timestamps from pyzk are NAIVE and equal the machine's
    wall clock.  They are localized with the machine's configured
    ``machine_timezone`` -> aware "wall local" datetimes.  (Newer pyzk
    releases return aware UTC; that is accepted too.)
  * ``hr.attendance.check_in`` / ``check_out`` are stored as NAIVE UTC.
  * All comparisons happen between AWARE datetimes only.  Never call
    ``astimezone`` on a naive datetime (it silently assumes the OS system
    timezone) and never ``localize`` an already-UTC value (double shift).
"""
import pytz
from datetime import datetime, time, timedelta


def to_wall_local(timestamp, wall_tz):
    """pyzk punch timestamp -> aware datetime in the machine wall timezone."""
    if timestamp.tzinfo is None:
        return wall_tz.localize(timestamp)
    return timestamp.astimezone(wall_tz)


def to_aware_utc(naive_utc):
    """Naive UTC value from an Odoo Datetime field -> aware UTC datetime."""
    return pytz.utc.localize(naive_utc)


def to_naive_utc(aware_dt):
    """Aware datetime -> naive UTC for storage in Odoo Datetime fields."""
    return aware_dt.astimezone(pytz.utc).replace(tzinfo=None)


def cutoff_moment(open_check_in_local, wall_tz, cutoff_hour):
    """Force-close point for a stale open record: the first daily cutoff
    strictly after its check_in (aware, machine wall tz)."""
    cutoff = _cutoff_time(cutoff_hour)
    moment = wall_tz.localize(datetime.combine(open_check_in_local.date(), cutoff))
    if moment <= open_check_in_local:
        moment = wall_tz.localize(
            datetime.combine(open_check_in_local.date() + timedelta(days=1), cutoff)
        )
    return moment


def pairing_decision(punch_local, open_check_in_local, cutoff_hour):
    """Decide how ``punch_local`` relates to the newest OPEN record whose
    check_in is ``open_check_in_local`` (both aware, machine wall tz).

    Returns one of:
      'close'   -- punch closes the open record: same local day, or a
                   pre-cutoff punch after midnight (night-shift checkout).
      'cutoff'  -- punch starts a new working day: it becomes a new
                   check-in and the stale open record is force-closed at
                   ``cutoff_moment``.
      'ignore'  -- punch at/before the open record's check_in (clock skew
                   / duplicate); record untouched.
    """
    if punch_local <= open_check_in_local:
        return "ignore"
    cutoff = _cutoff_time(cutoff_hour)
    p_date = punch_local.date()
    ci_date = open_check_in_local.date()
    if p_date == ci_date:
        return "close"
    # Punch on a later local day: before the daily cutoff it still belongs
    # to the open (overnight) shift; at/after it, it is a new working day.
    if p_date == ci_date + timedelta(days=1) and punch_local.time() < cutoff:
        return "close"
    return "cutoff"


def _cutoff_time(cutoff_hour):
    return time(max(0, min(23, int(cutoff_hour))), 0, 0)
