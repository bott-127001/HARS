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
        "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
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
            "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz",
        ),
    )


settings = load_settings()

_DEFAULT_JWT = "change-me-in-production-min-32-chars-xx"


def missing_required_settings() -> list[str]:
    """Env vars that must be set before running in production."""
    missing: list[str] = []
    if not settings.upstox_analytics_token:
        missing.append("UPSTOX_ANALYTICS_TOKEN")
    if not settings.dashboard_username:
        missing.append("DASHBOARD_USERNAME")
    if not settings.dashboard_password:
        missing.append("DASHBOARD_PASSWORD")
    if not settings.mongodb_uri:
        missing.append("MONGODB_URI")
    if not settings.jwt_secret or settings.jwt_secret == _DEFAULT_JWT:
        missing.append("JWT_SECRET")
    return missing
