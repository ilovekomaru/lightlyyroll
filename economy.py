"""Coin balances, weekly income and transfers, stored per guild in economy.json.

Income is rolling rather than tied to a calendar week: a week after an account was
last paid, the next economy command credits it. Nobody has to remember to claim,
and time away still pays out — capped at a few weeks so a long absence does not
mint a fortune.

Shape: {guild_id: {user_id: {coins, paid_at, sent, sent_since}}}, ids as strings
because JSON object keys always are. Writes go to a temp file and are moved into
place so an interrupted run cannot leave a half-written file.
"""
import json
import os
import time
from pathlib import Path

PATH = Path(__file__).parent / "economy.json"

WEEK = 7 * 24 * 3600
BASE_INCOME = 1000          # for members with no linked FACEIT account
MAX_ACCRUED_WEEKS = 4       # a long absence pays out, but not unboundedly
MIN_BET = 10
BET_CEILING = 5000
BET_BALANCE_SHARE = 0.5     # never stake more than half of what you hold
GIFT_CAP = 1000             # coins each member may give away per rolling week


class EconomyError(Exception):
    """A rule the player broke, phrased for them to read."""


def _read() -> dict:
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _write(data: dict) -> None:
    temp = PATH.with_suffix(".json.tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temp, PATH)


def _accrue(entry: dict, income: int, now: float) -> int:
    """Credit whole weeks owed since the last payout. Returns the amount added."""
    weeks = min(int((now - entry["paid_at"]) // WEEK), MAX_ACCRUED_WEEKS)
    if weeks:
        entry["coins"] += income * weeks
        entry["paid_at"] += weeks * WEEK
    if now - entry["sent_since"] >= WEEK:
        entry["sent"] = 0
        entry["sent_since"] = now
    return income * weeks


def _entry(data: dict, guild_id: int, user_id: int, income: int, now: float) -> tuple[dict, int]:
    members = data.setdefault(str(guild_id), {})
    entry = members.get(str(user_id))
    if entry is None:
        # A new account opens with its first week already paid.
        entry = {"coins": income, "paid_at": now, "sent": 0, "sent_since": now}
        members[str(user_id)] = entry
        return entry, income
    return entry, _accrue(entry, income, now)


def account(guild_id: int, user_id: int, income: int) -> dict:
    """The member's account, with any weekly income owed credited first."""
    data = _read()
    now = time.time()
    entry, credited = _entry(data, guild_id, user_id, income, now)
    if credited or entry.get("sent_since") == now:
        _write(data)
    return {**entry, "credited": credited,
            "next_payout": entry["paid_at"] + WEEK,
            "gift_left": max(0, GIFT_CAP - entry["sent"])}


def max_bet(coins: int) -> int:
    """Largest legal stake, or 0 when the balance cannot cover the minimum."""
    if coins < MIN_BET:
        return 0
    return min(coins, max(MIN_BET, min(BET_CEILING, int(coins * BET_BALANCE_SHARE))))


def check_bet(coins: int, amount: int) -> None:
    if amount < MIN_BET:
        raise EconomyError(f"The minimum bet is {MIN_BET} coins.")
    if coins < MIN_BET:
        raise EconomyError(f"You need at least {MIN_BET} coins to bet. You have {coins}.")
    allowed = max_bet(coins)
    if amount > allowed:
        raise EconomyError(f"Your maximum bet right now is **{allowed}** coins "
                           f"(half your balance, capped at {BET_CEILING}).")


def settle(guild_id: int, user_id: int, income: int, delta: int) -> int:
    """Apply a win or loss and return the new balance."""
    data = _read()
    now = time.time()
    entry, _ = _entry(data, guild_id, user_id, income, now)
    entry["coins"] = max(0, entry["coins"] + delta)
    _write(data)
    return entry["coins"]


def transfer(guild_id: int, sender_id: int, sender_income: int,
             recipient_id: int, recipient_income: int, amount: int) -> tuple[int, int]:
    """Move coins between two members. Returns the sender's balance and gift allowance left."""
    if amount < 1:
        raise EconomyError("Send at least 1 coin.")
    if sender_id == recipient_id:
        raise EconomyError("You cannot pay yourself.")

    data = _read()
    now = time.time()
    sender, _ = _entry(data, guild_id, sender_id, sender_income, now)
    recipient, _ = _entry(data, guild_id, recipient_id, recipient_income, now)

    if sender["coins"] < amount:
        raise EconomyError(f"You only have {sender['coins']} coins.")
    remaining = GIFT_CAP - sender["sent"]
    if amount > remaining:
        raise EconomyError(f"You can only give away {GIFT_CAP} coins a week, "
                           f"and you have {max(0, remaining)} left.")

    sender["coins"] -= amount
    sender["sent"] += amount
    recipient["coins"] += amount
    _write(data)
    return sender["coins"], GIFT_CAP - sender["sent"]


def richest(guild_id: int, limit: int = 10) -> list[tuple[int, int]]:
    members = _read().get(str(guild_id), {})
    ranked = sorted(members.items(), key=lambda kv: kv[1]["coins"], reverse=True)
    return [(int(user_id), entry["coins"]) for user_id, entry in ranked[:limit]]
