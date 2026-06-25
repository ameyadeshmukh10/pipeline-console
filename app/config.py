"""Runtime configuration. Reads .env at the project root (gitignored)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


class Settings:
    # HubSpot
    HUBSPOT_ACCESS_TOKEN: str = os.getenv("HUBSPOT_ACCESS_TOKEN", "")
    HUBSPOT_CLIENT_SECRET: str = os.getenv("HUBSPOT_CLIENT_SECRET", "")
    HUBSPOT_BASE_URL: str = os.getenv("HUBSPOT_BASE_URL", "https://api.hubapi.com").rstrip("/")
    PIPELINE_ID: str = os.getenv("PIPELINE_ID", "default")

    # Anthropic
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    FORECAST_MODEL: str = os.getenv("FORECAST_MODEL", "claude-opus-4-8")
    CHEAP_MODEL: str = os.getenv("CLAUDE_SOURCING_MODEL", "claude-haiku-4-5")

    # App
    PORT: int = int(os.getenv("PORT", "8787"))
    QUARTER_START: str = os.getenv("QUARTER_START", "2026-04-01")
    TIMEZONE: str = os.getenv("TIMEZONE", "America/New_York")

    # Paths
    DATA_DIR: Path = ROOT / "data"
    DB_PATH: str = str(ROOT / "data" / "pipeline.db")
    WEB_DIR: Path = ROOT / "web"

    @property
    def hubspot_ready(self) -> bool:
        return bool(self.HUBSPOT_ACCESS_TOKEN)

    @property
    def anthropic_ready(self) -> bool:
        return bool(self.ANTHROPIC_API_KEY)


settings = Settings()
settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
