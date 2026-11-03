"""Ботни Telegram'сиз синаш: сохта сессия орқали тўлиқ ёзилиш жараёни."""
import asyncio
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage, EditMessageReplyMarkup
from aiogram.types import Update, Message, Chat, User

import bot.main as m
from bot.config import load_config
from bot.db import DB
from bot.gcal import Calendar
from bot.slots import free_slots

sent = []


class FakeSession(BaseSession):
    async def make_request(self, bot, method, timeout=None):
        sent.append(method)
        if isinstance(method, (SendMessage, EditMessageText)):
            return Message(message_id=len(sent), date=datetime.now(), chat=Chat(id=1, type="private"),
                           text=getattr(method, "text", ""))
        return True

    async def stream_content(self, *a, **k):
        yield b""

    async def close(self):
        pass


USER = {"id": 42, "is_bot": False, "first_name": "Али <test>"}
uid = 0


def upd_msg(text=None, contact=None):
    global uid
    uid += 1
    msg = {"message_id": uid, "date": int(datetime.now().timestamp()), "chat": {"id": 42, "type": "private"}, "from": USER}
    if text:
        msg["text"] = text
    if contact:
        msg["contact"] = {"phone_number": contact, "first_name": "Али", "user_id": 42}
    return Update.model_validate({"update_id": uid, "message": msg})


def upd_cb(data):
    global uid
    uid += 1
    return Update.model_validate({"update_id": uid, "callback_query": {
        "id": str(uid), "from": USER, "chat_instance": "x", "data": data,
        "message": {"message_id": 1, "date": int(datetime.now().timestamp()), "chat": {"id": 42, "type": "private"}, "text": "x"}}})


def last_kb():
    for meth in reversed(sent):
        kb = getattr(meth, "reply_markup", None)
        if kb is not None and hasattr(kb, "inline_keyboard"):
            return [b.callback_data for row in kb.inline_keyboard for b in row]
    return []


def texts():
    return [getattr(x, "text", None) or "" for x in sent]


async def run():
    tmp = tempfile.mkdtemp()
    cfgpath = os.path.join(tmp, "config.json")
    raw = json.load(open("config.example.json", encoding="utf-8"))
    raw["admin_ids"] = [999]
    raw["work_start"], raw["work_end"] = "00:00", "23:59"
    raw["days_off"] = []
    raw["services"] = [
        {"id": "s1", "name": "Соч олиш", "price": 50000, "minutes": 30},
        {"id": "s3", "name": "Соч + соқол", "price": 70000, "minutes": 60},
    ]
    json.dump(raw, open(cfgpath, "w", encoding="utf-8"), ensure_ascii=False)
    m.cfg = load_config(cfgpath)
    m.db = DB(os.path.join(tmp, "t.db"))
    m.cal = Calendar(None)

    bot = Bot("123:abc", session=FakeSession())
    dp = Dispatcher()
    dp.include_router(m.router)
    feed = lambda u: dp.feed_update(bot, u)

    await feed(upd_msg("/start"))
    await feed(upd_msg(m.BTN_BOOK))
    assert "b:b1" in last_kb()
    await feed(upd_cb("b:b1"))
    assert "s:s3" in last_kb()
    await feed(upd_cb("s:s3"))
    day = [d for d in last_kb() if d.startswith("d:")][1]  # эртага
    await feed(upd_cb(day))
    slot = [d for d in last_kb() if d.startswith("t:")][0]
    await feed(upd_cb(slot))
    await feed(upd_msg(contact="+998901112233"))
    assert "confirm" in last_kb()
    await feed(upd_cb("confirm"))
    assert any("Сиз ёзилдингиз" in t for t in texts()), texts()[-5:]
    staff = [x for x in sent if isinstance(x, SendMessage) and x.chat_id == 999]
    assert staff and "&lt;test&gt;" in staff[-1].text, "админга хабар"

    bookings = m.db.user_upcoming(42, m.now())
    assert len(bookings) == 1
    b = bookings[0]
    assert (b.end - b.start) == timedelta(minutes=60)

    # Шу вақт энди бўш эмас
    await feed(upd_msg(m.BTN_BOOK)); await feed(upd_cb("b:b1")); await feed(upd_cb("s:s1")); await feed(upd_cb(day))
    assert slot not in last_kb(), "банд вақт кўринмаслиги керак"
    await feed(upd_cb("abort"))

    # Икки марта ёзиш (race) — иккинчиси None
    assert m.db.create(7, "x", "1", "b1", "s1", b.start, b.end) is None

    # Вақтни ўзгартириш: телефон сўралмаслиги, эскиси бекор бўлиши керак
    await feed(upd_cb(f"resch:{b.id}"))
    await feed(upd_cb(day))
    slot2 = [d for d in last_kb() if d.startswith("t:")][-1]
    await feed(upd_cb(slot2))
    assert "confirm" in last_kb()
    await feed(upd_cb("confirm"))
    ups = m.db.user_upcoming(42, m.now())
    assert len(ups) == 1 and ups[0].id != b.id, ups

    # Бекор қилиш
    await feed(upd_cb(f"cancel:{ups[0].id}"))
    await feed(upd_cb(f"cancelok:{ups[0].id}"))
    assert m.db.user_upcoming(42, m.now()) == []

    # Бошқа одам бировнинг ёзилишини бекор қила олмайди
    bk = m.db.create(77, "y", "1", "b2", "s1", b.start, b.end)
    await feed(upd_cb(f"cancelok:{bk.id}"))
    assert m.db.get(bk.id).status == "active"

    # Эслатма
    soon = m.now() + timedelta(minutes=60)
    rb = m.db.create(55, "z", "1", "b3", "s1", soon, soon + timedelta(minutes=30))
    due = m.db.due_reminders(m.now(), m.now() + timedelta(minutes=m.cfg.reminder_minutes))
    assert rb.id in [d.id for d in due]

    # Слотлар: иш вақтидан чиқмайди, бандликка тўқнашмайди
    c = m.cfg; c.work_start = datetime.strptime("09:00", "%H:%M").time(); c.work_end = datetime.strptime("12:00", "%H:%M").time()
    d0 = (m.now() + timedelta(days=2)).date()
    busy = [(datetime.combine(d0, datetime.strptime("10:00", "%H:%M").time(), tzinfo=c.tz),
             datetime.combine(d0, datetime.strptime("10:30", "%H:%M").time(), tzinfo=c.tz))]
    got = [f"{s:%H:%M}" for s in free_slots(c, d0, 60, busy, m.now())]
    assert got == ["09:00", "10:30", "11:00"], got

    # Фақат навбат (битта хизмат, нархсиз): хизмат қадами ва нархлар йўқ
    raw["services"] = [{"id": "s1", "name": "Навбат", "minutes": 30, "price": None}]
    json.dump(raw, open(cfgpath, "w", encoding="utf-8"), ensure_ascii=False)
    m.cfg = load_config(cfgpath)
    sent.clear()
    await feed(upd_msg("/start"))
    menu = [b.text for row in sent[-1].reply_markup.keyboard for b in row]
    assert m.BTN_PRICES not in menu, menu
    await feed(upd_msg(m.BTN_BOOK)); await feed(upd_cb("b:b4"))
    assert any(d.startswith("d:") for d in last_kb()), "сартарошдан кейин дарҳол кун"
    assert "back:barber" in last_kb()
    day = [d for d in last_kb() if d.startswith("d:")][1]
    await feed(upd_cb(day)); await feed(upd_cb([d for d in last_kb() if d.startswith("t:")][0]))
    assert "confirm" in last_kb()
    conf = [t for t in texts() if "Маълумотларни" in t][-1]
    assert "Хизмат" not in conf and "сўм" not in conf, conf
    await feed(upd_cb("confirm"))
    done = [t for t in texts() if "Сиз ёзилдингиз" in t]
    assert done and "сўм" not in done[-1], done

    # /admin: админ йўқ бўлса биринчи ёзган админ бўлади ва файлга сақланади
    raw["admin_ids"] = []
    json.dump(raw, open(cfgpath, "w", encoding="utf-8"), ensure_ascii=False)
    os.environ["BOT_CONFIG"] = cfgpath
    m.cfg = load_config(cfgpath)
    await feed(upd_msg("/admin"))
    assert json.load(open(cfgpath, encoding="utf-8"))["admin_ids"] == [42]
    USER["id"] = 43
    await feed(upd_msg("/admin"))
    assert m.cfg.admin_ids == [42], "иккинчи одам админ бўлолмайди"
    USER["id"] = 42

    shutil.rmtree(tmp)
    print("OK — барча синовлар ўтди")


asyncio.run(run())
