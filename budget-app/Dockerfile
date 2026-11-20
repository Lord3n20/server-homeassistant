# Plain Python standard library, nothing to install.
FROM python:3.13-alpine

COPY budget.py buch.py ausgaben.py bank.py depot.py lokal_ki.py panel.html /app/
# Lokale KI: App „KI (colibri)“ aus demselben Repository (Hostname im App-Netz).
ENV BUDGET_KI_URL=http://91c9b2fc-colibri:8100 \
    BUDGET_SUCHE_URL=http://91c9b2fc-searxng:8080

EXPOSE 8099
CMD ["python3", "-u", "/app/budget.py"]
