"""One helper for the places that read a JSON number as a float: an integer of 309 or more digits
is a valid Python ``int`` that ``float()`` refuses with ``OverflowError`` (Foundation refuses the
whole document instead). Pure; imports only ``usage_model.is_number``."""
from __future__ import annotations

import math
from typing import Any

from .usage_model import is_number


def finite_number(value: Any) -> float | None:
    """``value`` as a float if it is a JSON number (not a bool) that is finite and fits a double,
    else None. Never raises."""
    if not is_number(value):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None
