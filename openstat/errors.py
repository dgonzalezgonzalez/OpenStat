"""The runtime's error type.

It lives in its own module so that command-group mixins can raise it
without importing :mod:`openstat.engine`, which imports them.
"""
from __future__ import annotations


class StataError(Exception):
    """A Stata-style failure with a return code and a source line.

    ``rc`` follows Stata's ``r(###)`` convention; ``reported`` marks a
    failure whose message has already been written to the transcript, so
    the caller does not print it twice.
    """

    def __init__(self, message, rc=198, line=None):
        super().__init__(message)
        self.rc, self.line = rc, line
        #: Set when the message has already been written to the transcript.
        self.reported = False
