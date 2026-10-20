"""Stata display formats.

Implements the ``%w.dg``/``%w.df``/``%w.de``/``%w.dfc`` numeric formats and
the ``%w.s``/``%tX`` text and date formats that OpenStat can reproduce
exactly.  Numeric output follows Stata's rules: ``g`` uses the fewest
characters that preserve the value, ``f`` is fixed point, ``e`` is
scientific and the ``c`` suffix inserts thousands separators.
"""
from __future__ import annotations

import math
import re

import numpy as np

NUMERIC = re.compile(r"^%(-?\d+)(?:\.(\d+))?([gfe])(c?)$", re.IGNORECASE)
TEXT = re.compile(r"^%(-?\d+)(?:\.(\d+))?s$", re.IGNORECASE)
DATE = re.compile(r"^%(t[cdwmqhyb]|td|tw|tm|th|tq|ty|tb)([A-Za-z0-9_./,:!+\- ]*)$", re.IGNORECASE)


class FormatError(ValueError):
    pass


def is_numeric_format(fmt: str) -> bool:
    return bool(NUMERIC.match(fmt.strip()))


def is_text_format(fmt: str) -> bool:
    return bool(TEXT.match(fmt.strip()))


def is_date_format(fmt: str) -> bool:
    return bool(DATE.match(fmt.strip()))


def _group(value: str) -> str:
    """Insert thousands separators into the integer part."""
    negative = value.startswith("-")
    if negative:
        value = value[1:]
    if "." in value:
        whole, fraction = value.split(".", 1)
        fraction = "." + fraction
    else:
        whole, fraction = value, ""
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    result = ",".join(groups) + fraction
    return ("-" + result) if negative else result


def _general(value: float, precision: int) -> str:
    """Format like Stata's ``%g``: compact, trailing zeros removed."""
    if value == 0:
        return "0"
    exponent = math.floor(math.log10(abs(value)))
    if exponent < -4 or exponent >= precision:
        text = f"{value:.{max(precision - 1, 0)}e}"
        mantissa, _, exp = text.partition("e")
        sign = "+" if not exp.startswith("-") else "-"
        digits = exp.lstrip("+-").lstrip("0") or "0"
        return f"{mantissa}e{sign}{digits}"
    decimals = precision - 1 - exponent
    decimals = max(0, min(decimals, 17))
    text = f"{value:.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if not text:
        return "0"
    # Stata's %g omits the leading zero of a fraction: .5, not 0.5.
    if text.startswith("0."):
        text = text[1:]
    elif text.startswith("-0."):
        text = "-" + text[2:]
    return text


def format_value(value, fmt: str) -> str:
    """Render ``value`` using a Stata display format."""
    if value is None:
        return "."
    if isinstance(value, str):
        if not is_text_format(fmt):
            raise FormatError(f"invalid %format {fmt}")
        return value
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise FormatError(f"invalid %format {fmt}") from error
    if math.isnan(number):
        return "."
    match = NUMERIC.match(fmt.strip())
    if not match:
        raise FormatError(f"invalid %format {fmt}")
    width = int(match[1])
    if width == 0:
        width = 1
    if width < 0:
        width = -width
    kind = match[3].lower()
    group = bool(match[4])
    precision = int(match[2]) if match[2] is not None else (0 if kind == "f" else 12)
    if kind == "g":
        if precision == 0:
            precision = 12
        text = _general(number, precision)
    elif kind == "f":
        text = f"{number:.{precision}f}"
    else:
        text = f"{number:.{max(precision - 1, 0)}e}"
        mantissa, _, exponent = text.partition("e")
        if "." in mantissa:
            mantissa = mantissa.rstrip("0").rstrip(".")
        sign = "-" if exponent.startswith("-") else "+"
        digits = exponent.lstrip("+-").lstrip("0") or "0"
        text = f"{mantissa}e{sign}{digits}"
    if group:
        text = _group(text)
    if len(text) > width:
        # Stata widens the field rather than truncating the value.
        width = len(text)
    return text.rjust(width)


def format_for_display(value, width: int = 1) -> str:
    """Stata's default numeric rendering used by ``display``."""
    if value is None:
        return "."
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if math.isnan(number):
        return "."
    if math.isinf(number):
        return "." if number > 0 else "."
    text = _general(number, 12)
    if len(text) < width:
        text = text.rjust(width)
    return text
