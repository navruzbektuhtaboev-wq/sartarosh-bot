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


def upd_group(text, chat_id=-100555):
    global uid
    uid += 1
    return Update.model_validate({"update_id": uid, "message": {
        "message_id": uid, "date": int(datetime.now().timestamp()),
        "chat": {"id": chat_id, "type": "supergroup", "title": "Усталар"}, "from": USER, "text": text}})


def upd_webapp(data):
    global uid
    uid += 1
    return Update.model_validate({"update_id": uid, "message": {
        "message_id": uid, "date": int(datetime.now().timestamp()), "chat": {"id": 42, "type": "private"}, "from": USER,
        "web_app_data": {"data": data, "button_text": "📅 Вақтни танлаш"}}})


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
    raw["require_confirm"] = False
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

    # Mini App: URL ичидаги бўш вақтлар ва web_app_data орқали ёзилиш
    import base64
    raw["webapp_url"] = "https://example.github.io/sartarosh-bot/webapp/"
    json.dump(raw, open(cfgpath, "w", encoding="utf-8"), ensure_ascii=False)
    m.cfg = load_config(cfgpath)
    sent.clear()
    await feed(upd_msg(m.BTN_BOOK))
    btn = sent[-1].reply_markup.keyboard[0][0]
    assert btn.web_app and btn.web_app.url.startswith(raw["webapp_url"] + "?d="), btn
    enc = btn.web_app.url.split("?d=")[1]
    payload = json.loads(base64.urlsafe_b64decode(enc + "=" * (-len(enc) % 4)))
    assert [b[1] for b in payload["b"]][0] == "Катта уста Икромжон"
    assert len(btn.web_app.url) < 4000, len(btn.web_app.url)
    day_iso, masks = payload["d"][1]
    mask = int(masks[1], 16)
    idx = (mask & -mask).bit_length() - 1
    h, mi = divmod(int(payload["s"][:2]) * 60 + int(payload["s"][3:]) + idx * payload["p"], 60)
    await feed(upd_webapp(json.dumps({"b": payload["b"][1][0], "d": day_iso, "t": f"{h:02d}{mi:02d}"})))
    assert "confirm" in last_kb(), texts()[-3:]
    await feed(upd_cb("confirm"))
    ok = [b for b in m.db.user_upcoming(42, m.now()) if b.barber_id == payload["b"][1][0]]
    assert ok and f"{ok[0].start:%H%M}" == f"{h:02d}{mi:02d}", ok
    # Энди шу вақт банд: такрор юборилса рад этилади
    await feed(upd_webapp(json.dumps({"b": payload["b"][1][0], "d": day_iso, "t": f"{h:02d}{mi:02d}"})))
    assert "банд" in texts()[-1]
    await feed(upd_webapp("бузуқ"))
    assert "Хатолик" in texts()[-1]
    await feed(upd_msg(m.BTN_CHAT))
    assert "b:b1" in last_kb()

    # Туннел бор бўлса: inline web_app тугмаси ва чатда ёзилиш варианти
    from bot import web
    web.live_url = "https://abc.trycloudflare.com/"
    await feed(upd_msg(m.BTN_BOOK))
    kb = sent[-1].reply_markup.inline_keyboard
    assert kb[0][0].web_app.url == web.live_url and kb[1][0].callback_data == "chatbook"
    await feed(upd_cb("chatbook"))
    assert "b:b1" in last_kb()
    web.live_url = None

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

    # Гуруҳ: админ /guruh ёзади, ёзилишлар гуруҳга ҳам боради; оддий хабарга жавоб бермайди
    sent.clear()
    await feed(upd_group("салом"))
    assert not sent, "гуруҳда ортиқча жавоб"
    await feed(upd_group("/guruh"))
    assert json.load(open(cfgpath, encoding="utf-8"))["group_chat_id"] == -100555
    soon2 = m.now() + timedelta(days=1)
    soon2 = soon2.replace(hour=12, minute=0, second=0, microsecond=0)
    bk2 = await m.place_booking(bot, 77, "Гуруҳ синов", "+998900000000", "b2", "s1", soon2)
    assert bk2 and any(isinstance(x, SendMessage) and x.chat_id == -100555 and "Янги ёзилиш" in x.text for x in sent)

    # Уста ўзини улайди: /usta → танлайди → админ тасдиқлайди
    USER["id"] = 500
    await feed(upd_msg("/usta"))
    assert "usta:b3" in last_kb()
    await feed(upd_cb("usta:b3"))
    assert "ustaok:b3:500" in last_kb()
    await feed(upd_cb("ustaok:b3:500"))
    assert m.cfg.barbers["b3"].telegram_id is None, "админ бўлмаган тасдиқлай олмайди"
    USER["id"] = 42
    await feed(upd_cb("ustaok:b3:500"))
    assert m.cfg.barbers["b3"].telegram_id == 500
    assert [b for b in json.load(open(cfgpath, encoding="utf-8"))["barbers"] if b["id"] == "b3"][0]["telegram_id"] == 500

    # Уста тасдиғи: сўров «pending», уста қабул қилади ёки рад этади
    m.cfg.require_confirm = True
    sent.clear()
    t1 = (m.now() + timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)
    p1 = await m.place_booking(bot, 900, "Мижоз", "+998901234567", "b4", "s1", t1)
    assert p1.status == "pending" and f"acc:{p1.id}" in last_kb()
    assert t1 not in m.free_slots(m.cfg, t1.date(), 30, await m.barber_busy("b4", t1.date()), m.now()), "кутилаётган вақт банд"
    USER["id"] = 777  # бегона
    await feed(upd_cb(f"acc:{p1.id}"))
    assert m.db.get(p1.id).status == "pending"
    USER["id"] = 42  # админ
    await feed(upd_cb(f"acc:{p1.id}"))
    assert m.db.get(p1.id).status == "active"
    assert any(isinstance(x, SendMessage) and x.chat_id == 900 and "тасдиқлади" in x.text for x in sent)
    p2 = await m.place_booking(bot, 901, "Мижоз2", "+998901234568", "b4", "s1", t1 + timedelta(hours=1))
    USER["id"] = 500  # b3 устаси, b4 эмас — лекин гуруҳдан эмас, рухсат йўқ
    await feed(upd_cb(f"rej:{p2.id}"))
    assert m.db.get(p2.id).status == "pending"
    USER["id"] = 42
    await feed(upd_cb(f"rej:{p2.id}"))
    assert m.db.get(p2.id).status == "rejected"
    assert any(isinstance(x, SendMessage) and x.chat_id == 901 and "банд" in x.text for x in sent)
    assert t1 + timedelta(hours=1) in m.free_slots(m.cfg, t1.date(), 30, await m.barber_busy("b4", t1.date()), m.now()), "рад этилгач бўшайди"

    shutil.rmtree(tmp)
    print("OK — барча синовлар ўтди")


asyncio.run(run())
