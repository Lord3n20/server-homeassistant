# Plain Python standard library, nothing to install.
FROM python:3.13-alpine

COPY budget.py buch.py mcp.py panel.html /app/

EXPOSE 8099 8098
CMD ["python3", "-u", "/app/budget.py"]
