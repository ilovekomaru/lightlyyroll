"""Extended FACEIT skill levels (1-20) and their badge icons.

FACEIT itself stops at level 10 (2001+ ELO). Levels 11-20 subdivide the range
above that, using the same thresholds and artwork as the FACEIT Forecast
extension, so the badges here match what that extension shows in the browser.
"""
from pathlib import Path

ICON_DIR = Path(__file__).parent / "assets" / "faceit"

# Upper bound of each level, index 0 = level 1. Level 20 is open-ended.
ELO_MAX = [500, 750, 900, 1050, 1200, 1350, 1530, 1750, 2000,
           2250, 2500, 2750, 3000, 3250, 3500, 3750, 4000, 4250, 4500]

LEVEL_COLOR = {
    1: 0xEEEEEE, 2: 0x1CE400, 3: 0x1CE400, 4: 0xFFC800, 5: 0xFFC800,
    6: 0xFFC800, 7: 0xFFC800, 8: 0xFF6309, 9: 0xFF6309, 10: 0xFE1F00,
    11: 0xFE0123, 12: 0xFD0346, 13: 0xFE0379, 14: 0xFF019B, 15: 0xCC29C8,
    16: 0x4693EC, 17: 0x1FB2F7, 18: 0x00CBFF, 19: 0x4CDBFF, 20: 0xFFFFFF,
}


def level_for_elo(elo: int) -> int:
    for index, upper in enumerate(ELO_MAX):
        if elo <= upper:
            return index + 1
    return 20


def icon_file(level: int) -> Path:
    return ICON_DIR / f"level-{level}.png"
