from __future__ import annotations

from abc import ABC, abstractmethod


class IssueTracker(ABC):
    @abstractmethod
    async def file_issue(self, title: str, body: str) -> str:
        """File an issue. Returns the URL of the created issue."""
        ...

    @property
    @abstractmethod
    def is_configured(self) -> bool: ...

    @property
    @abstractmethod
    def tracker_name(self) -> str:
        """Human-readable tracker name, e.g. 'GitHub'."""
        ...
