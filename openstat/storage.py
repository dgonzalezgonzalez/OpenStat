"""Stata's storage types and the display formats that go with them.

Stata distinguishes five numeric storage types -- ``byte``, ``int``,
``long``, ``float`` and ``double`` -- plus string types.  A variable's
*storage type* decides both how many bytes a value keeps and which display
format the variable gets when it is created:

=========  ========  ==========================================
Storage    Format    Notes
=========  ========  ==========================================
``byte``   ``%8.0g``   -127 .. 100
``int``    ``%8.0g``   -32767 .. 32740
``long``   ``%12.0g``  -2147483647 .. 2147483620
``float``  ``%9.0g``   single precision
``double`` ``%10.0g``  double precision
=========  ========  ==========================================

``generate`` creates a **float** variable unless a type is named, and
``set type`` changes what an untyped ``generate`` creates.

The values themselves are truncated the way Stata truncates them: an
out-of-range integer becomes *missing*, not a clamped value.
"""
from __future__ import annotations

#: The default display format of each numeric storage type.
DEFAULT_FORMAT = {
    "byte": "%8.0g",
    "int": "%8.0g",
    "long": "%12.0g",
    "float": "%9.0g",
    "double": "%10.0g",
}

#: The inclusive value range of each integer storage type.
INTEGER_RANGE = {
    "byte": (-127, 100),
    "int": (-32767, 32740),
    "long": (-2147483647, 2147483620),
}

#: The storage type ``generate`` uses when the command names none.
DEFAULT_TYPE = "float"

#: How ReadStat names the storage types, mapped onto Stata's names.
READSTAT_TYPE = {
    "int8": "byte",
    "int16": "int",
    "int32": "long",
    "float": "float",
    "double": "double",
    "string": "str",
}
