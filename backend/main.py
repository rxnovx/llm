"""Run from this directory with python main.py, or import app.main:app."""
import uvicorn

from app.core.config import get_settings

if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run("app.main:app", host=settings.server_host,
                port=settings.server_port, reload=settings.server_reload,
                proxy_headers=False)
