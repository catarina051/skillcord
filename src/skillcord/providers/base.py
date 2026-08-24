"""Provider discovery interfaces and structured errors."""

from pathlib import Path
from typing import Protocol

from skillcord.models.provider import ProviderSnapshot


class ProviderDiscoveryError(Exception):
    """Discovery could not safely produce a normalized provider snapshot."""

    def __init__(self, provider_id: str, path: Path, reason: str) -> None:
        self.provider_id = provider_id
        self.path = path
        self.reason = reason
        super().__init__(f"{provider_id}: {path}: {reason}")


class ProviderAdapter(Protocol):
    """A read-only adapter for one provider layout."""

    provider_id: str

    def supports(self, root: Path) -> bool:
        """Return whether this adapter recognizes the explicit source root."""

    def discover(self, root: Path) -> ProviderSnapshot:
        """Produce a normalized, read-only snapshot of the provider source."""
