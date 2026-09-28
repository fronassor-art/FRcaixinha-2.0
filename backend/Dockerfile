FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /code
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade -r requirements.txt
COPY app ./app
COPY start.sh ./start.sh
COPY alembic.ini ./
COPY alembic ./alembic
RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /code
USER appuser
EXPOSE 8000
CMD ["sh", "./start.sh"]
