from datetime import date, datetime, timedelta

from .config import Config


def available_days(cfg: Config, now: datetime) -> list[date]:
    days = []
    for i in range(cfg.days_ahead):
        d = (now + timedelta(days=i)).date()
        if d.weekday() not in cfg.days_off:
            days.append(d)
    return days


def free_slots(cfg: Config, day: date, minutes: int, busy: list[tuple[datetime, datetime]], now: datetime) -> list[datetime]:
    """Хизмат давомийлиги сиғадиган бўш бошланиш вақтлари."""
    start = datetime.combine(day, cfg.work_start, tzinfo=cfg.tz)
    end = datetime.combine(day, cfg.work_end, tzinfo=cfg.tz)
    length = timedelta(minutes=minutes)
    step = timedelta(minutes=cfg.slot_step)
    earliest = now + timedelta(minutes=15)

    slots = []
    t = start
    while t + length <= end:
        if t >= earliest and not any(b0 < t + length and b1 > t for b0, b1 in busy):
            slots.append(t)
        t += step
    return slots
