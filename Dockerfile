FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PORT=3000 DB_PATH=/data/chi_tieu.db
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
RUN mkdir -p /data
EXPOSE 3000
CMD ["sh", "-c", "gunicorn -b 0.0.0.0:${PORT} --workers 1 --threads 4 app:app"]
