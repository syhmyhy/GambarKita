"""Sanitisation for short user-supplied text (event names, captions, uploader names)."""

import unicodedata

# Zero-width and bidirectional control characters that enable visual spoofing
# (e.g. U+202E RIGHT-TO-LEFT OVERRIDE). U+200D (ZWJ) is kept for emoji sequences.
_INVISIBLE_CONTROLS = {
    ord(char): None
    for char in "​‎‏‪‫‬‭‮⁦⁧⁨⁩؜﻿"
}
_REPLACE_WITH_SPACE = {"Cc", "Zl", "Zp"}
_DROP = {"Cs"}  # lone surrogates cannot be encoded as UTF-8


def clean_text(value: str) -> str:
    """Normalise to NFC, strip control / invisible characters and collapse whitespace."""
    normalized = unicodedata.normalize("NFC", value).translate(_INVISIBLE_CONTROLS)
    chars = []
    for char in normalized:
        category = unicodedata.category(char)
        if category in _DROP:
            continue
        chars.append(" " if category in _REPLACE_WITH_SPACE else char)
    return " ".join("".join(chars).split())
