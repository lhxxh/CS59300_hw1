FROM python:3.13-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DATA_DIR=/app/data
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY app.py ./
COPY research_assistant ./research_assistant
COPY templates ./templates
COPY static ./static
RUN useradd --create-home appuser && mkdir -p /app/data && chown appuser:appuser /app/data
USER appuser
EXPOSE 8000
# One process owns the background executor; threads serve polling and other requests.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", "--timeout", "300", "app:app"]
