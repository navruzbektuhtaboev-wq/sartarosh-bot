import json
import os
from dataclasses import dataclass, field
from datetime import time
from zoneinfo import ZoneInfo


@dataclass
class Barber:
    id: str
    name: str
    telegram_id: int | None = None
    calendar_id: str | None = None


@dataclass
class Service:
    id: str
    name: str
    minutes: int
    price: int | None = None


@dataclass
class Config:
    shop_name: str
    address: str
    tz: ZoneInfo
    admin_ids: list[int]
    work_start: time
    work_end: time
    days_off: list[int]  # 0 = душанба ... 6 = якшанба
    slot_step: int
    days_ahead: int
    reminder_minutes: int
    barbers: dict[str, Barber] = field(default_factory=dict)
    services: dict[str, Service] = field(default_factory=dict)
    google_credentials_file: str | None = None


def _parse_time(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def load_config(path: str | None = None) -> Config:
    path = path or os.getenv("BOT_CONFIG", "config.json")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return Config(
        shop_name=raw.get("shop_name", "Сартарошхона"),
        address=raw.get("address", ""),
        tz=ZoneInfo(raw.get("timezone", "Asia/Tashkent")),
        admin_ids=[int(x) for x in raw.get("admin_ids", [])],
        work_start=_parse_time(raw.get("work_start", "09:00")),
        work_end=_parse_time(raw.get("work_end", "20:00")),
        days_off=list(raw.get("days_off", [])),
        slot_step=int(raw.get("slot_step_minutes", 30)),
        days_ahead=int(raw.get("days_ahead", 7)),
        reminder_minutes=int(raw.get("reminder_minutes_before", 120)),
        barbers={b["id"]: Barber(**b) for b in raw["barbers"]},
        services={s["id"]: Service(**s) for s in raw["services"]},
        google_credentials_file=raw.get("google_credentials_file"),
    )
