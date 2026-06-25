"""Thin Anthropic client accessor."""
from functools import lru_cache

from ..config import settings


@lru_cache(maxsize=1)
def get_async_client():
    from anthropic import AsyncAnthropic
    return AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
