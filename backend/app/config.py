"""Application settings (env-driven)."""
from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./data/app.db"
    SECRET_KEY: str = "change-me"
    ADMIN_USER: str = "admin"
    ADMIN_PASS: str = "changeme"
    VITE_DEV_ORIGIN: str = "http://localhost:5173"
    FRONTEND_URL: str = ""

    model_config = {"env_file": ".env", "extra": "ignore"}

    def cors_origins(self) -> list[str]:
        origins = ["http://localhost:5173"]
        for raw in (self.VITE_DEV_ORIGIN, self.FRONTEND_URL):
            if not raw:
                continue
            for part in raw.split(","):
                part = part.strip()
                if part and part not in origins:
                    origins.append(part)
        return origins


settings = Settings()
