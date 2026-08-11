"""Stable identifier normalization helpers."""

import re
import unicodedata

_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")


def normalize_token(value: str) -> str:
    """Return a deterministic lower-case, hyphen-delimited token."""

    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return _NON_ALPHANUMERIC.sub("-", ascii_value.lower()).strip("-")
