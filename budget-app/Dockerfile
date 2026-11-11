# Standardbibliothek plus python-fints für den Bankabruf.
FROM python:3.13-alpine

RUN pip install --no-cache-dir fints==5.0.1

COPY budget.py buch.py ausgaben.py bank.py depot.py panel.html /app/

EXPOSE 8099
CMD ["python3", "-u", "/app/budget.py"]
