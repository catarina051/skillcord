"""Stable identifier normalization helpers."""

import re
import unicodedata

_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")
_CANONICAL_IDENTIFIER = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")


def normalize_token(value: str) -> str:
    """Return a deterministic lower-case, hyphen-delimited token."""

    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return _NON_ALPHANUMERIC.sub("-", ascii_value.lower()).strip("-")


def validate_canonical_identifier(value: str) -> str:
    """Return ``value`` when it uses the safe canonical identifier grammar."""

    if _CANONICAL_IDENTIFIER.fullmatch(value) is None:
        raise ValueError(
            "value must be a canonical identifier containing lowercase letters, digits, "
            "single hyphens, or single underscores"
        )
    return value


def validate_canonical_capability_id(value: str) -> str:
    """Validate one provider-scoped capability identifier defensively."""

    parts = value.split(".")
    if len(parts) != 2:
        raise ValueError("value must be a canonical capability identifier")
    try:
        for part in parts:
            validate_canonical_identifier(part)
    except ValueError as error:
        raise ValueError("value must be a canonical capability identifier") from error
    return value
