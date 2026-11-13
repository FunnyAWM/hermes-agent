"""Character counting shared with the Android client.

Android counts a code point plus its cluster tail as one character: combining
marks, variation selectors, emoji modifiers, tag sequences, regional-indicator
pairs and ZWJ chains do not add to the count. The memory limits must agree on
both ends, otherwise the same file reports two different usage percentages.
"""

from __future__ import annotations

import unicodedata

_ZERO_WIDTH_JOINER = 0x200D
_VARIATION_SELECTORS = frozenset({0xFE0E, 0xFE0F})
_TAG_CHARS = range(0xE0020, 0xE0080)
_EMOJI_MODIFIERS = range(0x1F3FB, 0x1F400)
_VARIATION_SELECTORS_SUPPLEMENT = range(0xE0100, 0xE01F0)
_REGIONAL_INDICATORS = range(0x1F1E6, 0x1F200)


def _is_regional_indicator(code_point: int) -> bool:
    return code_point in _REGIONAL_INDICATORS


def _is_cluster_extension(code_point: int) -> bool:
    if code_point in _VARIATION_SELECTORS:
        return True
    if code_point in _VARIATION_SELECTORS_SUPPLEMENT:
        return True
    if code_point in _EMOJI_MODIFIERS:
        return True
    if code_point in _TAG_CHARS:
        return True
    return unicodedata.category(chr(code_point)) in {"Mn", "Me"}


def char_count(text: str) -> int:
    """Count characters with Android's code-point + cluster-tail rule."""
    count = 0
    index = 0
    length = len(text)
    while index < length:
        code_point = ord(text[index])
        index += 1
        count += 1

        if _is_regional_indicator(code_point) and index < length:
            next_code_point = ord(text[index])
            if _is_regional_indicator(next_code_point):
                index += 1

        while index < length:
            next_code_point = ord(text[index])
            if next_code_point == _ZERO_WIDTH_JOINER:
                index += 1
                if index < length:
                    index += 1
            elif _is_cluster_extension(next_code_point):
                index += 1
            else:
                break
    return count
