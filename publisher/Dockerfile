FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src
WORKDIR /app
COPY publisher/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --uid 10001 --create-home gateway \
    && mkdir /data && chown gateway:gateway /data
COPY backend/src/mavo /app/src/mavo
USER gateway
EXPOSE 8741
CMD ["python", "-m", "uvicorn", "mavo.publisher_gateway:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8741", "--workers", "1", "--no-access-log", "--no-proxy-headers", "--limit-concurrency", "32"]
