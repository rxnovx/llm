FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 KNOWLEDGE_OCR_LANGUAGES=kor+eng
WORKDIR /app
COPY requirements.txt requirements-postgres.txt ./
RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils tesseract-ocr tesseract-ocr-kor \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements-postgres.txt \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home app
COPY --chown=app:app . .
RUN mkdir -p /app/data/avatars && chown -R app:app /app/data
USER app
EXPOSE 8000
CMD ["python", "main.py"]
