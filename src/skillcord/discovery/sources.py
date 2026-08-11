"""Explicit provider-adapter registry for read-only source discovery."""

from dataclasses import dataclass
from pathlib import Path

from skillcord.providers.base import ProviderAdapter
from skillcord.providers.ecc import ECCAdapter
from skillcord.providers.generic import GenericSkillAdapter
from skillcord.providers.humanizer import HumanizerAdapter
from skillcord.providers.open_design import OpenDesignAdapter
from skillcord.providers.superpowers import SuperpowersAdapter


@dataclass(frozen=True)
class AdapterRegistration:
    """A known provider identifier and its deliberately selected adapter."""

    provider_id: str
    adapter: ProviderAdapter


class AdapterRegistry:
    """Route known sources explicitly and unknown sources through no implicit search."""

    def __init__(self, registrations: tuple[AdapterRegistration, ...]) -> None:
        self._registrations = {registration.provider_id: registration.adapter for registration in registrations}

    @classmethod
    def default(cls) -> "AdapterRegistry":
        """Build the V1 registry with exactly the supported provider adapters."""

        return cls(
            (
                AdapterRegistration("superpowers", SuperpowersAdapter()),
                AdapterRegistration("ecc", ECCAdapter()),
                AdapterRegistration("open_design", OpenDesignAdapter()),
                AdapterRegistration("humanizer", HumanizerAdapter()),
            )
        )

    @property
    def known_provider_ids(self) -> frozenset[str]:
        """Return the identifiers that have provider-specific support in V1."""

        return frozenset(self._registrations)

    def adapter_for(self, provider_id: str, root: Path) -> ProviderAdapter | None:
        """Return a supporting explicit adapter, or a generic adapter for an explicit skill root."""

        adapter = self._registrations.get(provider_id)
        if adapter is not None:
            return adapter if adapter.supports(root) else None

        generic_adapter = GenericSkillAdapter(provider_id=provider_id)
        return generic_adapter if generic_adapter.supports(root) else None
