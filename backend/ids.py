"""Helpers for stable, human-readable identifiers."""

from __future__ import annotations

import re
import unicodedata


def slugify(value: str) -> str:
    """ASCII, lower-case, hyphen-separated slug.

    ``Cálculo   Numérico`` -> ``calculo-numerico``.  The result is stable across
    platforms and safe to use inside ids, URLs and filenames.
    """

    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_text = ascii_text.lower().replace("&", " y ")
    ascii_text = re.sub(r"[^a-z0-9]+", "-", ascii_text)
    return ascii_text.strip("-") or "sin-nombre"


def natural_key(value: str) -> list[object]:
    """Sort ``CLE01`` before ``CLE10`` (unlike plain lexical sorting)."""

    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", value)]
