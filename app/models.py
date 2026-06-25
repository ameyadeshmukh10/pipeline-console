"""Pydantic request/response models for the API."""
from typing import Any, Dict, Optional

from pydantic import BaseModel


class SettingsUpdate(BaseModel):
    """Partial settings update; only provided keys are written."""
    values: Dict[str, Any]


class ForecastRunRequest(BaseModel):
    owner_id: str
    target: Optional[float] = None
