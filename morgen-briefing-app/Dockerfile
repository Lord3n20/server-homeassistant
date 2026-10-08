# Plain Python standard library; tzdata for Europe/Berlin.
FROM python:3.13-alpine

RUN apk add --no-cache tzdata

COPY briefing.py /briefing.py

EXPOSE 8080
CMD ["python3", "-u", "/briefing.py"]
