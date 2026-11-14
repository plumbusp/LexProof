import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

_PROVIDER_URLS = {
    "openai": None,  # SDK default
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
}


@dataclass(frozen=True)
class Settings:
    exa_api_key: str = os.getenv("EXA_API_KEY", "")
    ai_provider: str = os.getenv("AI_PROVIDER", "gemini").lower()
    ai_api_key: str = os.getenv("AI_API_KEY", "")
    ai_model: str = os.getenv("AI_MODEL", "")
    ai_base_url_override: str = os.getenv("AI_BASE_URL", "")
    data_dir: Path = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
    user_agent: str = "Mozilla/5.0 (compatible; complaint-checker/0.1)"
    # legislation.gov.uk asks crawlers to space requests; we are an on-demand
    # client with a cache, but still throttle.
    legislation_min_interval_s: float = 1.0

    @property
    def exa_enabled(self) -> bool:
        return bool(self.exa_api_key)

    @property
    def ai_enabled(self) -> bool:
        return bool(self.ai_api_key and self.ai_model)

    @property
    def ai_base_url(self):
        return self.ai_base_url_override or _PROVIDER_URLS.get(self.ai_provider)


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
