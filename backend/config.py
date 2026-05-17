import os
from dataclasses import dataclass


def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return v if v is not None else default


@dataclass
class Settings:
    upstox_analytics_token: str = ""
    dashboard_username: str = ""
    dashboard_password: str = ""
    jwt_secret: str = "change-me-in-production-min-32-chars-xx"
    mongodb_uri: str = ""
    mongodb_db_name: str = "hars_dashboard"
    upstox_api_base: str = "https://api.upstox.com"
    upstox_instruments_json_gz_url: str = (
        "https://assets.upstox.com/market/instruments/json/full/final/full_instruments.json.gz"
    )


def load_settings() -> Settings:
    return Settings(
        upstox_analytics_token=_env("UPSTOX_ANALYTICS_TOKEN"),
        dashboard_username=_env("DASHBOARD_USERNAME"),
        dashboard_password=_env("DASHBOARD_PASSWORD"),
        jwt_secret=_env("JWT_SECRET", "change-me-in-production-min-32-chars-xx"),
        mongodb_uri=_env("MONGODB_URI"),
        mongodb_db_name=_env("MONGODB_DB_NAME", "hars_dashboard"),
        upstox_api_base=_env("UPSTOX_API_BASE", "https://api.upstox.com").rstrip("/"),
        upstox_instruments_json_gz_url=_env(
            "UPSTOX_INSTRUMENTS_JSON_GZ_URL",
            "https://assets.upstox.com/market/instruments/json/full/final/full_instruments.json.gz",
        ),
    )


settings = load_settings()
