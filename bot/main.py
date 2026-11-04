import asyncio
import base64
import html
import json
import logging
import os
import re
from datetime import date, datetime, timedelta

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    WebAppInfo,
)

from .config import Config, load_config
from .db import DB, Booking
from .gcal import Calendar
from .slots import available_days, free_slots

log = logging.getLogger(__name__)

WEEKDAYS = ["Душанба", "Сешанба", "Чоршанба", "Пайшанба", "Жума", "Шанба", "Якшанба"]
MONTHS = ["январ", "феврал", "март", "апрел", "май", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр"]

BTN_BOOK = "✂️ Ёзилиш"
BTN_MY = "📋 Ёзилишларим"
BTN_PRICES = "💈 Нархлар"
BTN_ADDRESS = "📍 Манзил"
BTN_WEBAPP = "📅 Вақтни танлаш"
BTN_CHAT = "💬 Чатда ёзилиш"
BTN_MENU = "⬅️ Меню"

cfg: Config
db: DB
cal: Calendar
router = Router()


class Booking_(StatesGroup):
    barber = State()
    service = State()
    day = State()
    slot = State()
    phone = State()
    confirm = State()


# ---------- ёрдамчи функциялар ----------

def now() -> datetime:
    return datetime.now(cfg.tz)


def single_service() -> bool:
    return len(cfg.services) == 1


def show_prices() -> bool:
    return any(s.price for s in cfg.services.values())


def service_line(service) -> str:
    """Битта хизмат бўлса (фақат навбат) ва нарх бўлмаса, хизмат қатори кўрсатилмайди."""
    if service is None or single_service():
        return ""
    return f"\n✂️ Хизмат: {service.name}" + (f" — {fmt_price(service.price)}" if service.price else "")


def fmt_price(p: int) -> str:
    return f"{p:,}".replace(",", " ") + " сўм"


def fmt_minutes(m: int) -> str:
    h, m = divmod(m, 60)
    return " ".join(p for p in (f"{h} соат" if h else "", f"{m} дақиқа" if m else "") if p)


def fmt_day(d: date) -> str:
    today = now().date()
    prefix = "Бугун" if d == today else "Эртага" if d == today + timedelta(days=1) else WEEKDAYS[d.weekday()]
    return f"{prefix}, {d.day} {MONTHS[d.month - 1]}"


def fmt_booking(b: Booking) -> str:
    barber = cfg.barbers.get(b.barber_id)
    service = cfg.services.get(b.service_id)
    t = b.start.astimezone(cfg.tz)
    return (
        f"📅 {fmt_day(t.date())}, {t:%H:%M}\n"
        f"💇 Сартарош: {barber.name if barber else b.barber_id}"
        + service_line(service)
    )


def main_menu() -> ReplyKeyboardMarkup:
    second = [KeyboardButton(text=BTN_MY)]
    if show_prices():
        second.append(KeyboardButton(text=BTN_PRICES))
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_BOOK)],
            second,
            [KeyboardButton(text=BTN_ADDRESS)],
        ],
        resize_keyboard=True,
    )


def ikb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows]
    )


def chunks(items, n):
    return [items[i:i + n] for i in range(0, len(items), n)]


async def notify_staff(bot: Bot, barber_id: str, text: str):
    targets = set(cfg.admin_ids)
    barber = cfg.barbers.get(barber_id)
    if barber and barber.telegram_id:
        targets.add(int(barber.telegram_id))
    for chat_id in targets:
        try:
            await bot.send_message(chat_id, text)
        except Exception:
            log.warning("Хабар юборилмади: %s", chat_id)


async def barber_busy(barber_id: str, day: date):
    start = datetime.combine(day, cfg.work_start, tzinfo=cfg.tz)
    end = datetime.combine(day, cfg.work_end, tzinfo=cfg.tz)
    busy = db.busy_intervals(barber_id, start, end)
    barber = cfg.barbers[barber_id]
    if cal.enabled(barber.calendar_id):
        own = {b.calendar_event_id for b in db.between(start, end) if b.calendar_event_id}
        busy += await cal.busy(barber.calendar_id, start, end, own)
    return busy


# ---------- клавиатуралар ----------

def barbers_kb():
    rows = [[(f"💇 {b.name}", f"b:{b.id}")] for b in cfg.barbers.values()]
    rows.append([("❌ Бекор қилиш", "abort")])
    return ikb(rows)


def services_kb():
    rows = [[(s.name + (f" — {fmt_price(s.price)}" if s.price else ""), f"s:{s.id}")] for s in cfg.services.values()]
    rows.append([("⬅️ Орқага", "back:barber"), ("❌ Бекор қилиш", "abort")])
    return ikb(rows)


def days_kb(back: str):
    days = available_days(cfg, now())
    rows = [[(fmt_day(d), f"d:{d.isoformat()}")] for d in days]
    rows.append([("⬅️ Орқага", back), ("❌ Бекор қилиш", "abort")])
    return ikb(rows)


def slots_kb(slots: list[datetime]):
    btns = [(f"{s:%H:%M}", f"t:{s:%H%M}") for s in slots]
    rows = chunks(btns, 4)
    rows.append([("⬅️ Орқага", "back:day"), ("❌ Бекор қилиш", "abort")])
    return ikb(rows)


# ---------- асосий меню ----------

@router.message(CommandStart())
async def start(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer(
        f"Ассалому алайкум! <b>{cfg.shop_name}</b> ботига хуш келибсиз.\n\n"
        "Бу ерда сартарошга навбатсиз ёзилишингиз мумкин. Қуйидаги тугмани босинг 👇",
        reply_markup=main_menu(),
    )


@router.message(F.text == BTN_PRICES)
async def prices(msg: Message):
    lines = [f"• {s.name} — {fmt_price(s.price)} ({s.minutes} дақ.)" for s in cfg.services.values() if s.price]
    if not lines:
        await msg.answer("Нархларни сартарошхонада билишингиз мумкин.")
        return
    await msg.answer("💈 <b>Нархлар</b>\n\n" + "\n".join(lines))


@router.message(F.text == BTN_ADDRESS)
async def address(msg: Message):
    hours = f"{cfg.work_start:%H:%M} – {cfg.work_end:%H:%M}"
    off = ", ".join(WEEKDAYS[d] for d in cfg.days_off)
    await msg.answer(f"📍 {cfg.address}\n🕘 Иш вақти: {hours}" + (f"\n🚫 Дам олиш: {off}" if off else ""))


@router.message(F.text == BTN_MY)
async def my_bookings(msg: Message):
    items = db.user_upcoming(msg.from_user.id, now())
    if not items:
        await msg.answer("Сизда фаол ёзилишлар йўқ.", reply_markup=main_menu())
        return
    for b in items:
        await msg.answer(
            fmt_booking(b),
            reply_markup=ikb([[("🔁 Вақтни ўзгартириш", f"resch:{b.id}"), ("🗑 Бекор қилиш", f"cancel:{b.id}")]]),
        )


# ---------- ёзилиш жараёни ----------

# ---------- Mini App ----------

def use_webapp() -> bool:
    return bool(cfg.webapp_url) and single_service()


async def webapp_payload() -> str:
    """Mini App га бўш вақтлар битмаска қилиб URL орқали берилади: алоҳида сервер керак эмас."""
    service = next(iter(cfg.services.values()))
    grid_start = datetime.combine(date.today(), cfg.work_start)
    grid_end = datetime.combine(date.today(), cfg.work_end)
    count = int((grid_end - grid_start).total_seconds() // 60 // cfg.slot_step)
    days = []
    for d in available_days(cfg, now()):
        masks = []
        for bid in cfg.barbers:
            mask = 0
            for s in free_slots(cfg, d, service.minutes, await barber_busy(bid, d), now()):
                idx = ((s.hour * 60 + s.minute) - (cfg.work_start.hour * 60 + cfg.work_start.minute)) // cfg.slot_step
                mask |= 1 << idx
            masks.append(format(mask, "x"))
        days.append([d.isoformat(), masks])
    data = {
        "n": cfg.shop_name,
        "s": f"{cfg.work_start:%H:%M}",
        "p": cfg.slot_step,
        "c": count,
        "b": [[b.id, b.name] for b in cfg.barbers.values()],
        "d": days,
    }
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


@router.message(F.text == BTN_BOOK)
async def book_start(msg: Message, state: FSMContext):
    await state.clear()
    if use_webapp():
        url = cfg.webapp_url + ("&" if "?" in cfg.webapp_url else "?") + "d=" + await webapp_payload()
        await msg.answer(
            "Пастдаги <b>«📅 Вақтни танлаш»</b> тугмасини босинг 👇",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(text=BTN_WEBAPP, web_app=WebAppInfo(url=url))],
                    [KeyboardButton(text=BTN_CHAT), KeyboardButton(text=BTN_MENU)],
                ],
                resize_keyboard=True,
            ),
        )
        return
    await chat_booking(msg, state)


@router.message(F.text == BTN_CHAT)
async def chat_booking(msg: Message, state: FSMContext):
    await state.clear()
    await state.set_state(Booking_.barber)
    await msg.answer("Сартарошни танланг:", reply_markup=barbers_kb())


@router.message(F.text == BTN_MENU)
async def back_to_menu(msg: Message, state: FSMContext):
    await state.clear()
    await msg.answer("Асосий меню:", reply_markup=main_menu())


@router.message(F.web_app_data)
async def webapp_booking(msg: Message, state: FSMContext):
    try:
        got = json.loads(msg.web_app_data.data)
        barber_id, day, hhmm = got["b"], date.fromisoformat(got["d"]), str(got["t"])
        start = datetime(day.year, day.month, day.day, int(hhmm[:2]), int(hhmm[2:]), tzinfo=cfg.tz)
    except Exception:
        await msg.answer("Хатолик юз берди, қайтадан уриниб кўринг.", reply_markup=main_menu())
        return
    service = next(iter(cfg.services.values()))
    if barber_id not in cfg.barbers or day not in available_days(cfg, now()):
        await msg.answer("Хатолик юз берди, қайтадан уриниб кўринг.", reply_markup=main_menu())
        return
    if start not in free_slots(cfg, day, service.minutes, await barber_busy(barber_id, day), now()):
        await msg.answer("Афсуски, бу вақт банд бўлиб қолди. «✂️ Ёзилиш» ни босиб, бошқа вақтни танланг.",
                         reply_markup=main_menu())
        return
    await state.clear()
    await state.update_data(barber_id=barber_id, service_id=service.id, day=day.isoformat(), start=start.isoformat())
    phone = db.last_phone(msg.from_user.id)
    if phone:
        await state.update_data(phone=phone)
        await msg.answer("Деярли тайёр!", reply_markup=main_menu())
        await ask_confirm(msg, state, edit=False)
    else:
        await state.set_state(Booking_.phone)
        await msg.answer(
            f"🕘 {fmt_day(day)}, {start:%H:%M}\n\n"
            "Телефон рақамингизни юборинг (тугмани босинг ёки ёзинг, масалан +998901234567):",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="📱 Рақамни юбориш", request_contact=True)]],
                resize_keyboard=True,
                one_time_keyboard=True,
            ),
        )


@router.callback_query(F.data == "abort")
async def abort(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("Ёзилиш бекор қилинди.")
    await cb.answer()


@router.callback_query(F.data.startswith("b:"))
async def pick_barber(cb: CallbackQuery, state: FSMContext):
    barber_id = cb.data[2:]
    if barber_id not in cfg.barbers:
        await cb.answer("Топилмади", show_alert=True)
        return
    await state.update_data(barber_id=barber_id)
    if single_service():
        await state.update_data(service_id=next(iter(cfg.services)))
        await state.set_state(Booking_.day)
        await cb.message.edit_text(
            f"💇 {cfg.barbers[barber_id].name}\n\nКунни танланг:", reply_markup=days_kb("back:barber")
        )
        await cb.answer()
        return
    await state.set_state(Booking_.service)
    await cb.message.edit_text(
        f"💇 {cfg.barbers[barber_id].name}\n\nХизматни танланг:", reply_markup=services_kb()
    )
    await cb.answer()


@router.callback_query(F.data.startswith("s:"))
async def pick_service(cb: CallbackQuery, state: FSMContext):
    service_id = cb.data[2:]
    if service_id not in cfg.services:
        await cb.answer("Топилмади", show_alert=True)
        return
    await state.update_data(service_id=service_id)
    await state.set_state(Booking_.day)
    await cb.message.edit_text("Кунни танланг:", reply_markup=days_kb("back:service"))
    await cb.answer()


async def show_slots(cb: CallbackQuery, state: FSMContext, day: date):
    data = await state.get_data()
    service = cfg.services[data["service_id"]]
    busy = await barber_busy(data["barber_id"], day)
    slots = free_slots(cfg, day, service.minutes, busy, now())
    if not slots:
        await cb.answer("Бу кунда бўш вақт қолмаган, бошқа кунни танланг", show_alert=True)
        return False
    await state.update_data(day=day.isoformat())
    await state.set_state(Booking_.slot)
    await cb.message.edit_text(
        f"{fmt_day(day)}\nБўш вақтни танланг:", reply_markup=slots_kb(slots)
    )
    return True


@router.callback_query(F.data.startswith("d:"))
async def pick_day(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if "service_id" not in data:
        await cb.answer("Илтимос, қайтадан бошланг", show_alert=True)
        return
    if await show_slots(cb, state, date.fromisoformat(cb.data[2:])):
        await cb.answer()


@router.callback_query(F.data.startswith("t:"))
async def pick_slot(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if "day" not in data:
        await cb.answer("Илтимос, қайтадан бошланг", show_alert=True)
        return
    hhmm = cb.data[2:]
    day = date.fromisoformat(data["day"])
    start = datetime(day.year, day.month, day.day, int(hhmm[:2]), int(hhmm[2:]), tzinfo=cfg.tz)
    await state.update_data(start=start.isoformat())

    phone = data.get("phone") or db.last_phone(cb.from_user.id)
    if phone:
        await state.update_data(phone=phone)
        await ask_confirm(cb.message, state, edit=True)
    else:
        await state.set_state(Booking_.phone)
        await cb.message.edit_text(f"🕘 {fmt_day(day)}, {start:%H:%M}")
        await cb.message.answer(
            "Телефон рақамингизни юборинг (тугмани босинг ёки ёзинг, масалан +998901234567):",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="📱 Рақамни юбориш", request_contact=True)]],
                resize_keyboard=True,
                one_time_keyboard=True,
            ),
        )
    await cb.answer()


@router.message(Booking_.phone)
async def got_phone(msg: Message, state: FSMContext):
    if msg.contact:
        phone = msg.contact.phone_number
    else:
        phone = (msg.text or "").strip().replace(" ", "")
        digits = phone.lstrip("+")
        if not digits.isdigit() or len(digits) < 9:
            await msg.answer("Рақам нотўғри. Масалан: +998901234567")
            return
    await state.update_data(phone=phone)
    await msg.answer("Раҳмат!", reply_markup=ReplyKeyboardRemove())
    await ask_confirm(msg, state, edit=False)


async def ask_confirm(msg: Message, state: FSMContext, edit: bool):
    data = await state.get_data()
    barber = cfg.barbers[data["barber_id"]]
    service = cfg.services[data["service_id"]]
    start = datetime.fromisoformat(data["start"])
    text = (
        "Маълумотларни текширинг:\n\n"
        f"📅 {fmt_day(start.date())}, {start:%H:%M}\n"
        f"💇 Сартарош: {barber.name}"
        + service_line(service)
        + f"\n📱 Телефон: {data['phone']}"
    )
    kb = ikb([[("✅ Тасдиқлаш", "confirm")], [("⬅️ Орқага", "back:slot"), ("❌ Бекор қилиш", "abort")]])
    await state.set_state(Booking_.confirm)
    if edit:
        await msg.edit_text(text, reply_markup=kb)
    else:
        await msg.answer(text, reply_markup=kb)


@router.callback_query(F.data == "confirm")
async def confirm(cb: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    if "start" not in data:
        await cb.answer("Илтимос, қайтадан бошланг", show_alert=True)
        return
    barber = cfg.barbers[data["barber_id"]]
    service = cfg.services[data["service_id"]]
    start = datetime.fromisoformat(data["start"])
    end = start + timedelta(minutes=service.minutes)

    # Календардаги бандликни ҳам охирги марта текширамиз
    busy_cal = await barber_busy(barber.id, start.date())
    if start < now() or any(b0 < end and b1 > start for b0, b1 in busy_cal):
        await cb.answer("Афсуски, бу вақт банд бўлиб қолди. Бошқа вақтни танланг.", show_alert=True)
        await show_slots(cb, state, start.date())
        return

    raw_name = cb.from_user.full_name or "Мижоз"
    name = html.escape(raw_name)
    booking = db.create(cb.from_user.id, raw_name, data["phone"], barber.id, service.id, start, end)
    if not booking:
        await cb.answer("Афсуски, бу вақт банд бўлиб қолди. Бошқа вақтни танланг.", show_alert=True)
        await show_slots(cb, state, start.date())
        return

    event_id = await cal.add(
        barber.calendar_id,
        raw_name if single_service() else f"{service.name} — {raw_name}",
        f"Телефон: {data['phone']}\nTelegram: {cb.from_user.id}\nБот орқали ёзилди (№{booking.id})",
        start,
        end,
    )
    if event_id:
        db.set_event_id(booking.id, event_id)

    old_id = data.get("resched_id")
    if old_id:
        old = db.get(old_id)
        if old and old.status == "active" and old.user_id == cb.from_user.id:
            db.cancel(old.id)
            await cal.remove(cfg.barbers[old.barber_id].calendar_id, old.calendar_event_id)

    await state.clear()
    await cb.message.edit_text(
        "✅ <b>Сиз ёзилдингиз!</b>\n\n" + fmt_booking(booking) +
        f"\n\n📍 {cfg.address}\nВақтидан {fmt_minutes(cfg.reminder_minutes)} олдин эслатаман."
    )
    await cb.message.answer("Асосий меню:", reply_markup=main_menu())
    await cb.answer()

    head = "🔁 Ёзилиш вақти ўзгартирилди" if old_id else "🆕 Янги ёзилиш"
    await notify_staff(bot, barber.id, f"{head} №{booking.id}\n\n{fmt_booking(booking)}\n👤 {name}\n📱 {data['phone']}")


@router.callback_query(F.data.startswith("back:"))
async def back(cb: CallbackQuery, state: FSMContext):
    step = cb.data[5:]
    data = await state.get_data()
    if step == "barber":
        await state.set_state(Booking_.barber)
        await cb.message.edit_text("Сартарошни танланг:", reply_markup=barbers_kb())
    elif step == "service":
        await state.set_state(Booking_.service)
        await cb.message.edit_text("Хизматни танланг:", reply_markup=services_kb())
    elif step == "day":
        await state.set_state(Booking_.day)
        back_to = "abort" if data.get("resched_id") else "back:barber" if single_service() else "back:service"
        await cb.message.edit_text("Кунни танланг:", reply_markup=days_kb(back_to))
    elif step == "slot" and "day" in data:
        await show_slots(cb, state, date.fromisoformat(data["day"]))
    await cb.answer()


# ---------- бекор қилиш / ўзгартириш ----------

@router.callback_query(F.data.startswith("cancel:"))
async def cancel_ask(cb: CallbackQuery):
    bid = int(cb.data.split(":")[1])
    await cb.message.edit_reply_markup(
        reply_markup=ikb([[("Ҳа, бекор қилинсин", f"cancelok:{bid}"), ("Йўқ", f"keep:{bid}")]])
    )
    await cb.answer("Ростдан бекор қиласизми?")


@router.callback_query(F.data.startswith("keep:"))
async def cancel_keep(cb: CallbackQuery):
    bid = int(cb.data.split(":")[1])
    await cb.message.edit_reply_markup(
        reply_markup=ikb([[("🔁 Вақтни ўзгартириш", f"resch:{bid}"), ("🗑 Бекор қилиш", f"cancel:{bid}")]])
    )
    await cb.answer()


@router.callback_query(F.data.startswith("cancelok:"))
async def cancel_ok(cb: CallbackQuery, bot: Bot):
    b = db.get(int(cb.data.split(":")[1]))
    if not b or b.user_id != cb.from_user.id or b.status != "active":
        await cb.answer("Ёзилиш топилмади", show_alert=True)
        return
    db.cancel(b.id)
    await cal.remove(cfg.barbers[b.barber_id].calendar_id if b.barber_id in cfg.barbers else None, b.calendar_event_id)
    await cb.message.edit_text("🗑 Бекор қилинди:\n\n" + fmt_booking(b))
    await cb.answer()
    await notify_staff(bot, b.barber_id, f"❌ Мижоз бекор қилди №{b.id}\n\n{fmt_booking(b)}\n👤 {html.escape(b.client_name)}\n📱 {b.phone}")


@router.callback_query(F.data.startswith("resch:"))
async def reschedule(cb: CallbackQuery, state: FSMContext):
    b = db.get(int(cb.data.split(":")[1]))
    if not b or b.user_id != cb.from_user.id or b.status != "active":
        await cb.answer("Ёзилиш топилмади", show_alert=True)
        return
    await state.clear()
    await state.update_data(barber_id=b.barber_id, service_id=b.service_id, phone=b.phone, resched_id=b.id)
    await state.set_state(Booking_.day)
    await cb.message.edit_text("Янги кунни танланг:", reply_markup=days_kb("abort"))
    await cb.answer()


# ---------- ходимлар учун ----------

def staff_barber_ids(user_id: int) -> list[str] | None:
    """Админ бўлса ҳаммаси, сартарош бўлса фақат ўзи, бошқаларга None."""
    if user_id in cfg.admin_ids:
        return list(cfg.barbers)
    own = [b.id for b in cfg.barbers.values() if b.telegram_id and int(b.telegram_id) == user_id]
    return own or None


async def day_report(msg: Message, offset: int):
    ids = staff_barber_ids(msg.from_user.id)
    if ids is None:
        return
    d = now().date() + timedelta(days=offset)
    start = datetime.combine(d, datetime.min.time(), tzinfo=cfg.tz)
    items = [b for b in db.between(start, start + timedelta(days=1)) if b.barber_id in ids]
    if not items:
        await msg.answer(f"{fmt_day(d)}: ёзилишлар йўқ.")
        return
    lines = [f"<b>{fmt_day(d)}</b> — {len(items)} та ёзилиш"]
    for bid in ids:
        mine = [b for b in items if b.barber_id == bid]
        if not mine:
            continue
        lines.append(f"\n💇 <b>{cfg.barbers[bid].name}</b>")
        for b in mine:
            s = cfg.services.get(b.service_id)
            what = "" if single_service() or not s else f"{s.name}, "
            lines.append(f"{b.start.astimezone(cfg.tz):%H:%M} — {what}{html.escape(b.client_name)}, {b.phone}")
    await msg.answer("\n".join(lines))


@router.message(Command("bugun"))
async def today_cmd(msg: Message):
    await day_report(msg, 0)


@router.message(Command("ertaga"))
async def tomorrow_cmd(msg: Message):
    await day_report(msg, 1)


@router.message(Command("admin"))
async def become_admin(msg: Message):
    """Ҳали админ бўлмаса, /admin ёзган биринчи одам (эгаси) админ бўлади ва config.json га сақланади."""
    if cfg.admin_ids:
        if msg.from_user.id in cfg.admin_ids:
            await msg.answer("Сиз аллақачон админсиз. /bugun ва /ertaga буйруқлари ишлайди.")
        return
    cfg.admin_ids.append(msg.from_user.id)
    path = os.getenv("BOT_CONFIG", "config.json")
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    raw["admin_ids"] = cfg.admin_ids
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    await msg.answer("✅ Сиз админ бўлдингиз. Энди ҳар бир ёзилиш ҳақида хабар оласиз.\n/bugun — бугунги рўйхат, /ertaga — эртанги рўйхат.")


@router.message(Command("myid"))
async def myid(msg: Message):
    await msg.answer(f"Сизнинг Telegram ID: <code>{msg.from_user.id}</code>")


@router.message()
async def fallback(msg: Message):
    await msg.answer(
        "Ёзилиш учун пастдаги <b>«✂️ Ёзилиш»</b> тугмасини босинг ва сартарош, кун, вақтни танланг 👇\n"
        "Ёзма хабарни бот тушунмайди.",
        reply_markup=main_menu(),
    )


# ---------- эслатмалар ----------

async def reminder_loop(bot: Bot):
    while True:
        try:
            t = now()
            for b in db.due_reminders(t, t + timedelta(minutes=cfg.reminder_minutes)):
                try:
                    await bot.send_message(b.user_id, "⏰ <b>Эслатма</b>: сиз ёзилгансиз\n\n" + fmt_booking(b) + f"\n\n📍 {cfg.address}")
                except Exception:
                    log.warning("Эслатма юборилмади: %s", b.id)
                db.mark_reminded(b.id)
        except Exception:
            log.exception("Эслатма циклида хато")
        await asyncio.sleep(60)


TOKEN_FILE = "token.txt"
TOKEN_RE = re.compile(r"\d{6,}:[A-Za-z0-9_-]{30,}")


def get_token() -> str:
    """Токенни BOT_TOKEN ёки token.txt дан олади; топилмаса сўрайди ва сақлайди.

    Матндан фақат токенни ажратиб олади, шунинг учун BotFather хабари бутунлай қўйилса ҳам ишлайди.
    """
    for raw in (os.getenv("BOT_TOKEN"), _read(TOKEN_FILE)):
        m = TOKEN_RE.search(raw or "")
        if m:
            return m.group(0)
    while True:
        print("\nBotFather bergan tokenni shu yerga qo'ying (masalan 7123456789:AAH...) va Enter bosing.")
        m = TOKEN_RE.search(input("Token: "))
        if m:
            with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                f.write(m.group(0))
            return m.group(0)
        print("Bu token emas. BotFather yuborgan uzun kodni to'liq nusxalang.")


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    except OSError:
        return None


async def main():
    global cfg, db, cal
    logging.basicConfig(level=logging.INFO)
    cfg = load_config()
    db = DB(os.getenv("BOT_DB", "bookings.db"))
    cal = Calendar(cfg.google_credentials_file)

    token = get_token()

    from aiogram.client.default import DefaultBotProperties
    from aiogram.exceptions import TelegramUnauthorizedError
    bot = Bot(token, default=DefaultBotProperties(parse_mode="HTML"))
    try:
        me = await bot.get_me()
    except TelegramUnauthorizedError:
        print("\nTelegram bu tokenni qabul qilmadi. BotFather'dagi tokenni qaytadan kiriting.\n")
        if os.path.exists(TOKEN_FILE):
            os.remove(TOKEN_FILE)
        await bot.session.close()
        return
    print(f"\nBot ishlayapti: @{me.username}  Bu oynani yopmang.\n")
    dp = Dispatcher()
    dp.include_router(router)
    asyncio.create_task(reminder_loop(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
