"""Google Calendar уланиши (ихтиёрий).

config.json да google_credentials_file ва сартарошда calendar_id бўлса:
  - ёзилиш сартарош календарига тушади, бекор қилинса ўчирилади;
  - сартарош календарга ўзи қўйган бандликлар (тушлик, дам) бўш вақтлардан чиқариб ташланади.
Акс ҳолда бот фақат ўз базаси билан ишлайди.
"""
import asyncio
import logging
from datetime import datetime

log = logging.getLogger(__name__)


class Calendar:
    def __init__(self, credentials_file: str | None):
        self.service = None
        if not credentials_file:
            return
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build

            creds = service_account.Credentials.from_service_account_file(
                credentials_file, scopes=["https://www.googleapis.com/auth/calendar"]
            )
            self.service = build("calendar", "v3", credentials=creds, cache_discovery=False)
            log.info("Google Calendar уланди")
        except Exception:
            log.exception("Google Calendar уланмади, фақат база ишлатилади")

    def enabled(self, calendar_id: str | None) -> bool:
        return bool(self.service and calendar_id)

    async def busy(self, calendar_id: str | None, start: datetime, end: datetime, own_event_ids: set[str]):
        if not self.enabled(calendar_id):
            return []

        def _call():
            items = (
                self.service.events()
                .list(calendarId=calendar_id, timeMin=start.isoformat(), timeMax=end.isoformat(),
                      singleEvents=True, orderBy="startTime")
                .execute()
                .get("items", [])
            )
            out = []
            for ev in items:
                if ev["id"] in own_event_ids or ev.get("transparency") == "transparent":
                    continue
                s, e = ev["start"], ev["end"]
                if "dateTime" in s:
                    out.append((datetime.fromisoformat(s["dateTime"]), datetime.fromisoformat(e["dateTime"])))
                else:  # бутун кунлик тадбир: кун тўлиқ банд
                    out.append((start, end))
            return out

        try:
            return await asyncio.to_thread(_call)
        except Exception:
            log.exception("Календардан ўқиб бўлмади")
            return []

    async def add(self, calendar_id: str | None, summary: str, description: str, start: datetime, end: datetime):
        if not self.enabled(calendar_id):
            return None
        body = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": end.isoformat()},
        }
        try:
            ev = await asyncio.to_thread(lambda: self.service.events().insert(calendarId=calendar_id, body=body).execute())
            return ev["id"]
        except Exception:
            log.exception("Календарга ёзиб бўлмади")
            return None

    async def remove(self, calendar_id: str | None, event_id: str | None):
        if not (self.enabled(calendar_id) and event_id):
            return
        try:
            await asyncio.to_thread(lambda: self.service.events().delete(calendarId=calendar_id, eventId=event_id).execute())
        except Exception:
            log.exception("Календардан ўчириб бўлмади")
