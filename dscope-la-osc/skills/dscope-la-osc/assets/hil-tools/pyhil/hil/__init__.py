"""pyhil — analysis layer for ds-capture output.

Loads .raw/.json captures from ds-capture, decodes protocols, measures
timing, and provides assertions for automated hardware-in-the-loop tests.
Protocol decoding uses the official DSView decoder scripts (PDs) where
available (see hil.pd); DSHOT uses the built-in decoder (no official PD).
Instrument control lives in hil.instrument (DSLogicLA / DSCopeScope).
"""

from .capture import LogicCapture, AnalogCapture, load_capture
from . import timing, expect, report, pd, instrument, analog

__all__ = [
    "LogicCapture", "AnalogCapture", "load_capture",
    "timing", "expect", "report", "pd", "instrument", "analog",
]
