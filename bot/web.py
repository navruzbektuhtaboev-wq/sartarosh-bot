"""Mini App учун кичик веб-сервер ва туннел.

Бот компьютерда ишлагани учун унинг интернетдаги манзили йўқ. cloudflared бепул
«quick tunnel» орқали https://....trycloudflare.com манзилини беради; бот уни ҳар
ишга тушганда меню тугмасига ёзиб қўяди. Шунда мижоз ботдаги «Ёзилиш» тугмасини
босганда саҳифа бутун экранда сайт каби очилади ва бўш вақтларни шу сервердан олади.
"""
import asyncio
import json
import logging
import os
import re
import shutil
from datetime import date, datetime
from pathlib import Path

from aiohttp import web
from aiogram import Bot
from aiogram.types import MenuButtonWebApp, WebAppInfo
from aiogram.utils.web_app import safe_parse_webapp_init_data

log = logging.getLogger(__name__)

WEBAPP_DIR = Path(__file__).resolve().parent.parent / "webapp"
PHONE_RE = re.compile(r"^\+?\d{9,15}$")
TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

live_url: str | None = None


def _user(m, init_data: str):
    """Telegram imzosini текширади; сохта сўровлар рад этилади."""
    try:
        parsed = safe_parse_webapp_init_data(m.BOT_TOKEN, init_data)
    except (ValueError, TypeError):
        raise web.HTTPUnauthorized(text="bad initData")
    if not parsed.user:
        raise web.HTTPUnauthorized(text="no user")
    return parsed.user


def make_app(m, bot: Bot) -> web.Application:
    async def index(request):
        return web.FileResponse(WEBAPP_DIR / "index.html", headers={"Cache-Control": "no-store"})

    async def init(request):
        body = await request.json()
        user = _user(m, body.get("initData", ""))
        return web.json_response({"data": await m.webapp_data(), "phone": m.db.last_phone(user.id) or ""})

    async def book(request):
        body = await request.json()
        user = _user(m, body.get("initData", ""))
        phone = str(body.get("phone", "")).replace(" ", "").replace("-", "")
        if not PHONE_RE.match(phone):
            return web.json_response({"ok": False, "error": "Телефон рақам нотўғри. Масалан: +998901234567"})
        try:
            barber_id = str(body["b"])
            day = date.fromisoformat(body["d"])
            hhmm = str(body["t"])
            start = datetime(day.year, day.month, day.day, int(hhmm[:2]), int(hhmm[2:]), tzinfo=m.cfg.tz)
        except (KeyError, ValueError):
            return web.json_response({"ok": False, "error": "Хатолик, қайтадан уриниб кўринг."})
        if barber_id not in m.cfg.barbers or day not in m.available_days(m.cfg, m.now()):
            return web.json_response({"ok": False, "error": "Хатолик, қайтадан уриниб кўринг."})
        service = next(iter(m.cfg.services.values()))
        free = m.free_slots(m.cfg, day, service.minutes, await m.barber_busy(barber_id, day), m.now())
        booking = None
        if start in free:
            full_name = " ".join(p for p in (user.first_name, user.last_name) if p)
            booking = await m.place_booking(bot, user.id, full_name, phone, barber_id, service.id, start)
        if not booking:
            return web.json_response({"ok": False, "busy": True,
                                      "error": "Афсуски, бу вақт банд бўлиб қолди. Бошқа вақтни танланг."})
        try:
            await bot.send_message(user.id, m.booked_text(booking), reply_markup=m.main_menu())
        except Exception:
            log.warning("Тасдиқ хабари юборилмади: %s", user.id)
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_post("/api/init", init)
    app.router.add_post("/api/book", book)
    return app


async def start_server(m, bot: Bot, port: int) -> None:
    runner = web.AppRunner(make_app(m, bot))
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", port).start()
    log.info("Веб-сервер: http://127.0.0.1:%s", port)


def find_cloudflared() -> str | None:
    for p in (os.getenv("CLOUDFLARED"), "cloudflared.exe", "cloudflared"):
        if p and (os.path.isfile(p) or shutil.which(p)):
            return p if os.path.isfile(p) else shutil.which(p)
    return None


async def run_tunnel(bot: Bot, port: int) -> None:
    """cloudflared ни ишга туширади, манзилини меню тугмасига ёзади; узилса қайта уланади."""
    global live_url
    exe = find_cloudflared()
    if not exe:
        log.warning("cloudflared топилмади: меню тугмаси ўрнатилмайди, Mini App эски усулда ишлайди")
        return
    while True:
        proc = await asyncio.create_subprocess_exec(
            exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        async for line in proc.stderr:
            found = TUNNEL_RE.search(line.decode(errors="ignore"))
            if found and found.group(0) + "/" != live_url:
                live_url = found.group(0) + "/"
                try:
                    await bot.set_chat_menu_button(
                        menu_button=MenuButtonWebApp(text="Ёзилиш", web_app=WebAppInfo(url=live_url))
                    )
                    print(f"\nMini App manzili: {live_url}\n")
                except Exception:
                    log.exception("Меню тугмаси ўрнатилмади")
        await proc.wait()
        live_url = None
        log.warning("Туннел узилди, 10 сониядан кейин қайта уланади")
        await asyncio.sleep(10)
