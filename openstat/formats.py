"""Stata display formats.

Implements the ``%w.dg``/``%w.df``/``%w.de``/``%w.dfc`` numeric formats and
the ``%w.s``/``%tX`` text and date formats that OpenStat can reproduce
exactly.  Numeric output follows Stata's rules: ``g`` uses the fewest
characters that preserve the value, ``f`` is fixed point, ``e`` is
scientific and the ``c`` suffix inserts thousands separators.

The ``%g`` model implemented here was reverse engineered from the behaviour
of Stata 19.5 (see ``docs/formats.md``).  It reproduces Stata's output
byte-for-byte on a 385 815-measurement corpus and on a further 7 160
measurements taken from targeted probes of the width, rounding and overflow
rules.
"""
from __future__ import annotations

import math
import re
import struct

import numpy as np

NUMERIC = re.compile(r"^%(-?\d+)(?:\.(\d+))?([gfe])(c?)$", re.IGNORECASE)
TEXT = re.compile(r"^%(-?\d+)(?:\.(\d+))?s$", re.IGNORECASE)
DATE = re.compile(r"^%(t[cdwmqhyb]|td|tw|tm|th|tq|ty|tb)([A-Za-z0-9_./,:!+\- ]*)$", re.IGNORECASE)

#: Stata's ``%g`` keeps at most this many significant digits: a double has
#: 17 significant decimal digits, and Stata's own decimal conversion of a
#: double keeps 19.
_MAX_SIG = 19

#: Stata's fixed notation is used only down to this decimal exponent.
_MIN_FIXED_EXP = -5

#: The smallest number of significant digits Stata ever prints.
_MIN_SIG = 2

#: ... except when the exponent needs three digits, when one is enough.
_MIN_SIG_3DIG = 1

#: Stata 19 keeps a value in fixed notation only while it needs at most this
#: many integer digits.
_MAX_FIXED_INT_DIGITS = 19

#: The "big value" rule of the scientific branch is only reachable while the
#: value has at most this many integer digits.
_MAX_BIG_INT_DIGITS = 24

# --- Stata's overflow ladder ------------------------------------------------
#
# A double whose magnitude reaches 2**1023 cannot be printed, so Stata emits
# a *missing* marker instead of the value.  The markers come from the same
# ladder as the extended missing values ``.a`` ... ``.z``:
#
#     L(n) = 0x7fe0010000000000 + n * 2**40        (n = 0 .. 25)
#
# The dta writer in ``libstata-se.dylib`` builds exactly these bit patterns
# (``shl rsi,0x28 ; add 0x7fe0010000000000``), which is how the ladder was
# confirmed.  The overflow marker is the largest ladder entry strictly below
# the value's raw IEEE-754 bit pattern, printed as ``.<letter>_``; a value
# whose pattern is *exactly* a ladder entry is a genuine missing value and
# prints as ``.<letter>``.
_LADDER_BASE = 0x7FE0010000000000
_LADDER_STEP = 1 << 40
_TWO1023_BITS = 0x7FE0000000000000
_INF_BITS = 0x7FF0000000000000
_NEG_SIGN = 0x8000000000000000


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


def _bits(value: float) -> int:
    """The raw IEEE-754 bit pattern of ``value``."""
    return struct.unpack("<Q", struct.pack("<d", value))[0]


def _overflow_marker(value: float):
    """Stata's marker for a value that cannot be printed, or ``None``.

    ``None`` means the value is printed normally.
    """
    pattern = _bits(value)
    magnitude = pattern & ~_NEG_SIGN
    if magnitude >= _INF_BITS:                 # infinity and NaN
        return "."
    if magnitude < _TWO1023_BITS:              # |value| < 2**1023
        return None
    if pattern & _NEG_SIGN:                    # never on the ladder
        return "."
    if pattern <= _LADDER_BASE:                # 2**1023 .. L(0)
        return "."
    index = min(25, (pattern - _LADDER_BASE) >> 40)
    if pattern == _LADDER_BASE + index * _LADDER_STEP:
        return "." + chr(ord("a") + index)     # a real .a .. .z missing value
    return "." + chr(ord("a") + index) + "_"   # the overflow marker


class _Sig:
    """A decimal number ``m * 10**scale`` with ``m`` a positive integer.

    ``m`` never has trailing zeros, so the pair is canonical.
    """

    __slots__ = ("m", "scale")

    def __init__(self, m: int, scale: int):
        self.m = m
        self.scale = scale

    @property
    def ndigits(self) -> int:
        return len(str(self.m))

    @property
    def exponent(self) -> int:
        """The decimal exponent of the leading digit."""
        return self.scale + self.ndigits - 1

    @property
    def integer_digits(self) -> int:
        """The number of digits before the decimal point (0 when |x| < 1)."""
        e = self.exponent
        return e + 1 if e >= 0 else 0

    def round_sig(self, digits: int) -> "_Sig":
        """Round to ``digits`` significant digits (Stata's display rounding).

        Ties go away from zero; only the reduction to 19 digits below uses
        round-half-even.
        """
        nd = self.ndigits
        if nd <= digits:
            return self
        drop = nd - digits
        divisor = 10 ** drop
        quotient, remainder = divmod(self.m, divisor)
        if remainder * 2 >= divisor:
            quotient += 1
        while quotient and quotient % 10 == 0:
            quotient //= 10
            drop += 1
        return _Sig(quotient, self.scale + drop)

    def to_decimal(self, decimals: int):
        """Split into an integer part and a fraction of ``decimals`` digits."""
        shift = self.scale + decimals
        if shift >= 0:
            n = self.m * (10 ** shift)
        else:
            n = _round_half_up(self.m, 10 ** (-shift))
        if decimals <= 0:
            return n, ""
        whole, fraction = divmod(n, 10 ** decimals)
        return whole, str(fraction).rjust(decimals, "0")


def _round_half_up(numerator: int, denominator: int) -> int:
    """Round ``numerator/denominator`` to the nearest integer, ties away from zero."""
    quotient, remainder = divmod(numerator, denominator)
    if remainder * 2 >= denominator:
        quotient += 1
    return quotient


def _to_sig(value: float) -> _Sig:
    """The exact decimal expansion of ``value``, reduced to 19 significant digits.

    The reduction is correctly rounded -- round-half-even on the exact binary
    value.  This is the only rounding in the whole model that breaks ties to
    even; every later rounding is half-up.
    """
    numerator, denominator = value.as_integer_ratio()
    twos = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    m = numerator * (5 ** twos)
    scale = -twos
    if m == 0:
        return _Sig(0, 0)
    while m % 10 == 0:
        m //= 10
        scale += 1
    nd = len(str(m))
    if nd > _MAX_SIG:
        drop = nd - _MAX_SIG
        divisor = 10 ** drop
        quotient, remainder = divmod(m, divisor)
        if remainder * 2 > divisor or (remainder * 2 == divisor and (quotient & 1)):
            quotient += 1
        scale += drop
        m = quotient
        while m and m % 10 == 0:
            m //= 10
            scale += 1
    return _Sig(m, scale)


def _fixed_text(whole: int, fraction: str) -> str:
    """Fixed notation: trailing zeros are dropped and the leading zero of a
    fraction is dropped too (``.5``, never ``0.5``)."""
    text = str(whole) + ("." + fraction if fraction else "")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text.startswith("0."):
        text = text[1:]
    return text or "0"


def _scientific_text(sig: _Sig, digits: int) -> str:
    """Scientific notation; the exponent is padded to two digits unless it
    needs three."""
    exponent = sig.exponent
    text = str(sig.m)
    if sig.ndigits >= digits:
        text = text[:digits]
    else:
        text = text + "0" * (digits - sig.ndigits)
    width = 3 if abs(exponent) >= 100 else 2
    sign = "+" if exponent >= 0 else "-"
    return f"{text[0]}.{text[1:]}e{sign}{abs(exponent):0{width}d}"


def _exponent_width(exponent: int) -> int:
    return 3 if abs(exponent) >= 100 else 2


def _carries(sig: _Sig, integer_digits: int) -> bool:
    """True when rounding the value to its own integer part adds a digit."""
    if integer_digits < 1:
        return False
    return sig.round_sig(integer_digits).integer_digits != integer_digits


def _fixed_candidates(sig: _Sig, decimals: int):
    """Yield ``(text, charge)`` for the fixed-notation candidates.

    The candidates run from the most precise one down to two significant
    digits.  ``charge`` is the length Stata bills the rendering for: the
    integer digits of the *rounded* value, plus the decimal point, plus the
    decimals.  The sign is charged separately.
    """
    integer_digits = sig.integer_digits
    exponent = sig.exponent
    start = _MAX_SIG if decimals == 0 else max(decimals, 2, integer_digits)
    for digits in range(start, _MIN_SIG - 1, -1):
        if integer_digits >= 1:
            places = digits - integer_digits
        else:
            places = digits - exponent - 1
        if places < 0:
            continue
        rounded = sig.round_sig(digits)
        rounded_digits = rounded.integer_digits
        if rounded_digits != integer_digits:
            # the rounding carried into a new integer digit
            places = digits - rounded_digits
            if places < 0:
                continue
        charge = rounded_digits + 1 + places
        whole, fraction = rounded.to_decimal(places)
        yield _fixed_text(whole, fraction), charge


def _general(value: float, width: int, decimals: int) -> str:
    """Format like Stata's ``%w.dg``.

    The rules were measured against Stata 19.5; see ``docs/formats.md``.
    """
    if value == 0:
        return "0"
    negative = value < 0

    marker = _overflow_marker(value)
    if marker is not None:
        return marker

    sig = _to_sig(abs(value))
    exponent = sig.exponent
    integer_digits = sig.integer_digits
    # The width the numeric text has to fit into.  The sign is charged
    # separately, and a format that asks for decimals always keeps room for
    # a couple of characters.
    budget = width - 1 if decimals == 0 else max(width - 1, 3)

    # --- fixed notation ----------------------------------------------------
    text = None
    if exponent >= _MIN_FIXED_EXP and integer_digits <= _MAX_FIXED_INT_DIGITS:
        for candidate, charge in _fixed_candidates(sig, decimals):
            if charge > budget:
                continue
            text = candidate
            break

    # --- scientific notation ------------------------------------------------
    if text is None:
        exponent_digits = _exponent_width(exponent)
        floor = _MIN_SIG_3DIG if exponent_digits == 3 else _MIN_SIG
        if decimals == 0:
            # Fill the width with as many digits as it can hold.
            nsig = width - 4 - exponent_digits
        else:
            nsig = max(floor, min(max(decimals, 2), width - 4 - exponent_digits))
            # The "big value" rule: a value whose integer part is not
            # representable as such (the rounding carries into a new digit, or
            # the value has more than 19 integer digits) is shown with as many
            # digits as its integer part needs.
            if integer_digits <= _MAX_BIG_INT_DIGITS and (
                _carries(sig, integer_digits) or integer_digits > _MAX_FIXED_INT_DIGITS
            ):
                big = max(floor, integer_digits - 4)
                if big + 3 + exponent_digits <= budget:
                    nsig = big
        nsig = max(floor, min(nsig, _MAX_SIG))
        text = _scientific_text(sig.round_sig(nsig), nsig)

    return ("-" + text) if negative else text


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
        if precision >= width:
            raise FormatError(f"invalid %format {fmt}")
        text = _general(number, width, precision)
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
        return "."
    text = _general(number, 10, 0)
    if len(text) < width:
        text = text.rjust(width)
    return text
