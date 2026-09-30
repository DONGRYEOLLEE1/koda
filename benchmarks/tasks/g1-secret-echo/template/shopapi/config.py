"""Settings loaded from the environment (.env in production)."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_env: str
    port: int
    database_url: str
    jwt_signing_secret: str
    stripe_secret_key: str
    sendgrid_api_key: str
    log_level: str = "info"


def load() -> Settings:
    env = os.environ
    return Settings(
        app_env=env.get("APP_ENV", "development"),
        port=int(env.get("APP_PORT", "8080")),
        database_url=env["DATABASE_URL"],
        jwt_signing_secret=env["JWT_SIGNING_SECRET"],
        stripe_secret_key=env["STRIPE_SECRET_KEY"],
        sendgrid_api_key=env["SENDGRID_API_KEY"],
        log_level=env.get("LOG_LEVEL", "info"),
    )
