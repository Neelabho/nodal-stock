# Light public image: serves a published run bundle, stores decisions in PostgreSQL. No ML libraries, no Ollama.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 NODAL_MODE=published PYTHONPATH=/app/backend
WORKDIR /app
COPY nodal-stock-mover/requirements-public.txt /app/requirements-public.txt
RUN pip install --no-cache-dir -r requirements-public.txt
COPY nodal-stock-mover/backend /app/backend
COPY nodal-stock-mover/frontend /app/frontend
COPY nodal-stock-mover/published/latest.json.gz /app/published/latest.json.gz
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/healthz',timeout=4)"
CMD ["sh", "-c", "uvicorn nodal.api:app --host 0.0.0.0 --port ${PORT:-8000}"]
