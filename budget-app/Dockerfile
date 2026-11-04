# Plain Python standard library, nothing to install.
FROM python:3.13-alpine

COPY budget.py buch.py ausgaben.py panel.html /app/

EXPOSE 8099
CMD ["python3", "-u", "/app/budget.py"]
