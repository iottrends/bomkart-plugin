"""
Electronics Component Value Normalizer

Converts various notations to a canonical numeric form for matching:
  100nF = 0.1uF = 100n       → 1e-7
  4k7 = 4.7k = 4700          → 4700
  2R2 = 2.2 = 2.2ohm         → 2.2
  10uH = 10µH                → 1e-5

Also handles unit extraction and display formatting.
"""

import re
from typing import Optional, Tuple


# SI prefix multipliers
SI_PREFIXES = {
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6, "µ": 1e-6, "μ": 1e-6,
    "m": 1e-3,
    "": 1.0,
    "k": 1e3, "K": 1e3,
    "M": 1e6,
    "G": 1e9,
}

# Unit suffixes to strip before parsing
UNIT_SUFFIXES = ["F", "f", "H", "h", "Ω", "ohm", "Ohm", "OHM", "V", "v", "A", "a", "Hz", "hz"]


def normalize_value(value: str) -> Optional[float]:
    """
    Parse a component value string into a float.
    Returns None if value is not a parseable number (e.g., IC part numbers).

    Examples:
        "100nF"  → 1e-7
        "4k7"    → 4700.0
        "2R2"    → 2.2
        "0.1uF"  → 1e-7
        "10M"    → 1e7
        "STM32F103" → None (not a value)
    """
    if not value or not isinstance(value, str):
        return None

    val = value.strip()

    # Strip known unit suffixes
    for suffix in sorted(UNIT_SUFFIXES, key=len, reverse=True):
        if val.endswith(suffix):
            val = val[:-len(suffix)].strip()
            break

    # Handle R/K/M notation: "4k7" → 4.7k, "2R2" → 2.2, "1M5" → 1.5M
    rk_match = re.match(r"^(\d+)([RrKkMmUuNnPp])(\d+)$", val)
    if rk_match:
        whole, prefix_char, frac = rk_match.groups()
        prefix_lower = prefix_char.lower()
        # 'r' means ohms (×1)
        if prefix_lower == "r":
            multiplier = 1.0
        else:
            multiplier = SI_PREFIXES.get(prefix_lower, SI_PREFIXES.get(prefix_char, None))
            if multiplier is None:
                return None
        return (float(whole) + float(frac) / (10 ** len(frac))) * multiplier

    # Standard notation: "100n", "4.7k", "0.1u", "10", "3.3"
    std_match = re.match(r"^([0-9]*\.?[0-9]+)\s*([a-zA-Zµμ]?)$", val)
    if std_match:
        number_str, prefix = std_match.groups()
        try:
            number = float(number_str)
        except ValueError:
            return None
        multiplier = SI_PREFIXES.get(prefix, SI_PREFIXES.get(prefix.lower(), None))
        if multiplier is not None:
            return number * multiplier
        # No prefix — bare number
        if prefix == "":
            return number
        return None

    # Try bare number
    try:
        return float(val)
    except ValueError:
        return None


def values_match(val_a: str, val_b: str, tolerance: float = 0.01) -> bool:
    """
    Check if two value strings represent the same electrical value.
    tolerance: fractional tolerance for matching (0.01 = 1%).
    """
    a = normalize_value(val_a)
    b = normalize_value(val_b)
    if a is None or b is None:
        # Fallback: case-insensitive string match
        return val_a.strip().lower() == val_b.strip().lower()
    if a == 0 and b == 0:
        return True
    denom = max(abs(a), abs(b), 1e-30)
    return abs(a - b) / denom <= tolerance


def format_value(numeric: float, unit: str = "") -> str:
    """
    Format a numeric value back to a human-readable string with SI prefix.
    E.g., 1e-7 → "100nF" (if unit="F"), 4700 → "4.7kΩ" (if unit="Ω")
    """
    if numeric == 0:
        return f"0{unit}"

    abs_val = abs(numeric)
    prefix_order = [
        ("G", 1e9), ("M", 1e6), ("k", 1e3), ("", 1.0),
        ("m", 1e-3), ("µ", 1e-6), ("n", 1e-9), ("p", 1e-12),
    ]

    for prefix, mult in prefix_order:
        if abs_val >= mult * 0.999:
            display = numeric / mult
            # Clean up trailing zeros
            if display == int(display):
                return f"{int(display)}{prefix}{unit}"
            return f"{display:.2g}{prefix}{unit}"

    return f"{numeric:.2e}{unit}"
