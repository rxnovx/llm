FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt requirements-postgres.txt requirements-gitlab.txt requirements-mcp-client.txt requirements-knowledge.txt requirements-artifacts.txt ./
RUN pip install --no-cache-dir -r requirements-postgres.txt -r requirements-mcp-client.txt -r requirements-knowledge.txt -r requirements-artifacts.txt \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home app
COPY --chown=app:app . .
RUN mkdir -p /app/data/avatars && chown -R app:app /app/data
USER app
EXPOSE 8000
CMD ["python", "main.py"]
