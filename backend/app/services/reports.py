"""Monthly value report for a business.

This is a retention tool, not an analytics feature. A clinic sees the invoice
every month but never sees the calls that were answered while the front desk was
busy, so by month two the service feels like it does nothing. The numbers that
change that opinion are already recorded on every call: when it came in, whether
anyone at the business could have taken it, and whether it became an
appointment.

Two figures do most of the work:

  * calls answered outside working hours, which nobody at the business could
    have picked up
  * appointments booked, which is revenue that would otherwise have depended on
    the caller trying again later

Everything is derived from the Call and Appointment tables, so no new data is
collected and a report can be produced for any month already recorded.
"""

import calendar
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Appointment, AppointmentStatus, Business, Call, CallOutcome

# Outcomes that represent the agent doing the job it was bought for.
PRODUCTIVE = {CallOutcome.BOOKED, CallOutcome.RESCHEDULED, CallOutcome.CANCELLED}


@dataclass
class MonthlyReport:
    business_name: str
    period_label: str
    period_start: datetime
    period_end: datetime

    calls_total: int = 0
    calls_out_of_hours: int = 0
    calls_answered_seconds: int = 0

    booked: int = 0
    rescheduled: int = 0
    cancelled: int = 0
    enquiries: int = 0
    unresolved: int = 0

    busiest_hour: int | None = None
    busiest_day: str = ""
    repeat_callers: int = 0
    by_outcome: dict[str, int] = field(default_factory=dict)

    @property
    def minutes_answered(self) -> int:
        return round(self.calls_answered_seconds / 60)

    @property
    def out_of_hours_share(self) -> int:
        if not self.calls_total:
            return 0
        return round(self.calls_out_of_hours / self.calls_total * 100)

    def headline(self) -> str:
        """One sentence an owner can read without studying a table."""
        if not self.calls_total:
            return f"No calls were answered in {self.period_label}."

        parts = [f"answered {self.calls_total} calls"]
        if self.calls_out_of_hours:
            parts.append(f"{self.calls_out_of_hours} of them outside your opening hours")
        if self.booked:
            parts.append(f"and booked {self.booked} appointments")
        return f"In {self.period_label} your assistant " + ", ".join(parts) + "."


def _month_bounds(year: int, month: int, tz: str) -> tuple[datetime, datetime, str]:
    """The month in the business's own timezone, converted to UTC for querying.

    Doing this in UTC would put a 9pm call on the last day of the month into the
    following month for an Indian business, which is exactly the out-of-hours
    call the report exists to highlight.
    """
    zone = ZoneInfo(tz)
    start_local = datetime(year, month, 1, tzinfo=zone)
    last_day = calendar.monthrange(year, month)[1]
    end_local = datetime(year, month, last_day, 23, 59, 59, tzinfo=zone) + timedelta(seconds=1)
    label = start_local.strftime("%B %Y")
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc), label


def _is_out_of_hours(moment: datetime, business: Business, zone: ZoneInfo) -> bool:
    """A call nobody at the business could have taken.

    Counts both closed days and times outside opening hours. This is the number
    that justifies the subscription, so it is deliberately conservative: a call
    during opening hours is never counted, even if the line was in fact busy.
    """
    local = moment.astimezone(zone)
    working_days = business.working_days or []
    if working_days and local.isoweekday() not in working_days:
        return True
    return not (business.opens_at <= local.time() < business.closes_at)


async def build_monthly_report(
    db: AsyncSession, business: Business, year: int, month: int
) -> MonthlyReport:
    start, end, label = _month_bounds(year, month, business.timezone)
    zone = ZoneInfo(business.timezone)

    report = MonthlyReport(
        business_name=business.name,
        period_label=label,
        period_start=start,
        period_end=end,
    )

    calls = (
        await db.execute(
            select(Call).where(
                Call.business_id == business.id,
                Call.created_at >= start,
                Call.created_at < end,
            )
        )
    ).scalars().all()

    report.calls_total = len(calls)

    hour_counts: dict[int, int] = {}
    day_counts: dict[str, int] = {}
    caller_counts: dict[str, int] = {}

    for call in calls:
        moment = call.started_at or call.created_at
        report.calls_answered_seconds += call.duration_seconds or 0

        if _is_out_of_hours(moment, business, zone):
            report.calls_out_of_hours += 1

        local = moment.astimezone(zone)
        hour_counts[local.hour] = hour_counts.get(local.hour, 0) + 1
        day_name = local.strftime("%A")
        day_counts[day_name] = day_counts.get(day_name, 0) + 1

        if call.caller_number:
            caller_counts[call.caller_number] = caller_counts.get(call.caller_number, 0) + 1

        outcome = call.outcome.value if call.outcome else "unknown"
        report.by_outcome[outcome] = report.by_outcome.get(outcome, 0) + 1

        if call.outcome == CallOutcome.BOOKED:
            report.booked += 1
        elif call.outcome == CallOutcome.RESCHEDULED:
            report.rescheduled += 1
        elif call.outcome == CallOutcome.CANCELLED:
            report.cancelled += 1
        elif call.outcome == CallOutcome.ENQUIRY:
            report.enquiries += 1
        elif call.outcome in (CallOutcome.NO_DETAILS, CallOutcome.FAILED):
            report.unresolved += 1

    if hour_counts:
        report.busiest_hour = max(hour_counts, key=lambda h: hour_counts[h])
    if day_counts:
        report.busiest_day = max(day_counts, key=lambda d: day_counts[d])

    # Someone who called more than once in a month is a returning customer, which
    # is a stronger signal for a clinic than raw call volume.
    report.repeat_callers = sum(1 for count in caller_counts.values() if count > 1)

    return report


def render_text(report: MonthlyReport) -> str:
    """Plain text, so it can be pasted into WhatsApp or an email without a PDF."""
    lines = [
        f"{report.business_name}",
        f"Assistant report for {report.period_label}",
        "",
        report.headline(),
        "",
        f"Calls answered            {report.calls_total}",
        f"  outside opening hours   {report.calls_out_of_hours} ({report.out_of_hours_share}%)",
        f"  total time on calls     {report.minutes_answered} minutes",
        "",
        f"Appointments booked       {report.booked}",
        f"Appointments moved        {report.rescheduled}",
        f"Appointments cancelled    {report.cancelled}",
        f"General enquiries         {report.enquiries}",
    ]

    if report.unresolved:
        lines.append(f"Calls that ended early    {report.unresolved}")

    if report.busiest_day or report.busiest_hour is not None:
        lines.append("")
        if report.busiest_day:
            lines.append(f"Busiest day               {report.busiest_day}")
        if report.busiest_hour is not None:
            hour = time(report.busiest_hour).strftime("%I %p").lstrip("0")
            lines.append(f"Busiest time              around {hour}")

    if report.repeat_callers:
        lines.append(f"People who called back    {report.repeat_callers}")

    return "\n".join(lines)
