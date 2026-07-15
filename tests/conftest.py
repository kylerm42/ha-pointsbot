"""Shared test fixtures and fakes for PointsBot unit tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class FakeStore:
    """Minimal in-memory replacement for homeassistant.helpers.storage.Store."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._data: dict[str, Any] | None = None
        self.save_count = 0

    async def async_load(self) -> dict[str, Any] | None:
        return self._data

    async def async_save(self, data: dict[str, Any]) -> None:
        import copy
        self._data = copy.deepcopy(data)
        self.save_count += 1

    def seed(self, data: dict[str, Any]) -> None:
        """Pre-populate with data for test setup."""
        import copy
        self._data = copy.deepcopy(data)


@pytest.fixture
def fake_hass() -> MagicMock:
    """A minimal mock HomeAssistant instance."""
    return MagicMock()


@pytest.fixture
def fake_store() -> FakeStore:
    """A fresh FakeStore instance."""
    return FakeStore()
