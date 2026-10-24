FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot ./bot
ENV BOT_CONFIG=/data/config.json BOT_DB=/data/bookings.db TZ=Asia/Tashkent
CMD ["python", "-m", "bot.main"]
