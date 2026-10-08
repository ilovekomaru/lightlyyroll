# lightlyyroll

A Discord bot for a small CS2/Dota 2 friend group: FACEIT lookups, elo roles, Dota match
recaps, chance games and a coin economy. Runs on an Ubuntu server under systemd.

This file preserves Claude's implementation constraints and the continuation context.
Read the source for current signatures and dependencies. Historical API observations below
come from `CLAUDE.md`; they were not re-tested against live services during this handoff.

## Continuation snapshot (2026-10-08)

`main` and the local `origin/main` are at `2cf28a3` (Add /yesterday for one player's stats
over yesterday). At handoff, the only tracked working-tree change was `.gitignore` adding
`.aider*`; `AGENTS.md` was an untracked copy of `CLAUDE.md`. No pending implementation
task or unfinished Claude plan was found in the repository. Do not infer a new feature
request from this snapshot; re-check git status before continuing.

Recent work is already implemented: Dota linking and owner overrides, live Steam names,
hero icons, same-team party tags, the 05:00 Vilnius reset, `/rewind`, `/rewinddota` and
the single-player FACEIT `/yesterday` card. These are not outstanding tasks. Deployment
of these commits and their behavior in live Discord have not been verified here.

Entry points for continuing work:

- `bot.py`: slash commands, embeds, startup emoji setup and the five-minute role refresh.
- `faceit.py`, `dota.py`: network clients and day-window filtering; `levels.py` and
  `chart.py`: FACEIT badge selection and chart rendering.
- `elo_roles.py`: role lifecycle and REST member lookup; `links.py`: FACEIT links, Dota
  links and announcement channels in one state file.
- `games.py`, `economy.py`: chance games, settlement, income and gifts.
- `README.md`, `.env.example`, `deploy/lightlyyroll.service`: setup and service operation.

Verified follow-up work, separate from the diff review:

- `economy._accrue` caps each call at four weeks, but leaves older unpaid weeks claimable.
  A twelve-week absence followed by three calls at the same timestamp credits 4,000 coins
  each time for an unlinked member, totaling 12,000. This contradicts the documented
  four-week backlog cap. A future fix should discard excess overdue weeks while preserving
  the intended rolling payout schedule, and verify repeated calls at the same timestamp.
  This pre-existing behavior was reproduced in memory; it has not been fixed here.
- `README.md` omits Dota commands from its command table and still describes `games.py`
  as keeping no state. `CLAUDE.md` predates the Dota/day-window additions and contains
  the overly broad coin-supply statement corrected below. If updating those documents,
  reconcile them with the source and this handoff.

## Day windows and Dota

Both games share `faceit.day_start`: a day is [05:00, next 05:00) in `Europe/Vilnius`.
Use zoned wall-clock arithmetic so daylight-saving days remain correct; do not replace
this with a fixed UTC hour or subtraction of 86,400 seconds. FACEIT dates are milliseconds;
OpenDota `start_time` is seconds. `tzdata` supplies zone data on Windows.

`/today` and `/whoplayed` use day 0; `/yesterday` and `/rewind` use day 1.
`/whoplayeddota` and `/rewinddota` use the same boundaries. Earlier FACEIT cards/lists use
the newest included match's `final_elo` when available, including its badge and colour.
History is fetched on demand, not archived: FACEIT has only its newest 100 matches and
OpenDota its newest 50. Do not promise complete arbitrary historical lookups.

Dota uses OpenDota JSON; the recorded Dotabuff Cloudflare challenge is why profile links
are parsed for ids rather than scraped. The match query deliberately includes
`significant=0` to retain Turbo and other modes, and repeated `project` fields. Results
are oldest first, skip unknown winners, and identify Radiant by `player_slot < 128`.

Store Steam32 account ids in `links.json` under the separate `dota` key. SteamID64 resolves
by subtracting `STEAM64_BASE`; vanity names resolve through Steam's public XML. Refresh
display names from Steam with a stored/OpenDota fallback. Dota links must stay separate
from FACEIT entries so role sync and elo-based income never consume them. Unlike FACEIT
role cleanup, Dota links currently remain when a member leaves.

Party tags are keyed by `(match_id, radiant)`, so linked players on opposing sides do
not count as teammates. Hero icons use application emoji named `dh_<hero slug>` and
upload in a background task; missing icons must not prevent commands or login. Lists
skip individual data-fetch failures and count them as unavailable when returning results.

## Constraints that will bite you

**Do not replace urllib with aiohttp in `faceit.py`.** FACEIT's CDN rejects aiohttp's TLS
signature on the stats endpoint with a 403 regardless of headers; seven combinations were
tested, including bytes identical to a working request. Plain `urllib` with an honest bot
User-Agent is served normally, run through `asyncio.to_thread` so the event loop keeps
turning. This looks like an obvious simplification. It is not.

**The per-match payload uses opaque keys** (`i6` kills, `i8` deaths, `i12` rounds, `i13`
headshots, `i20` damage). They were decoded empirically and cross-checked against the
payload's own ratio fields (`c2` K/D, `c3` K/R, `c4` HS%, `c10` ADR) across a full window,
0 mismatches. Do not re-guess them.

**Ratios come from summed totals**, not the mean of each match's own ratio — total kills ÷
total deaths, total damage ÷ total rounds. Only that reproduces the figures faceit.com
shows, and it is the only correct way to average a per-round figure across matches with
different round counts. Averaging the per-match ratios was a real bug once.

**The batch users endpoint takes a repeated `id` parameter.** The comma-separated `ids=`
form is accepted and *silently ignored*, returning arbitrary players — the bot would happily
report strangers' elo. Never use it.

**FACEIT Rating, Round Swing and Consistency are unobtainable.** They live behind
`api/statistics/v1/...`, which returns Cloudflare's interactive challenge to every
non-browser client including curl. The Forecast extension only has them because it hooks a
logged-in browser page. Do not go looking again; the one untried route is the official Data
API with a key.

**The stats endpoint rate limits (429).** It backs `/stats`, `/today`, `/avg`, `/elo`, and
`/today` is the heaviest since it always pulls 100 matches. The 5-minute role refresh is
safe because it uses the batched *users* endpoint — one request per guild regardless of
member count. `/yesterday`, `/whoplayed` and `/rewind` also fetch 100-match pages, with
one stats request per player for lists.

**Discord will not let a bot place a role above its own.** An admin must drag the bot's role
to the top of the list once; `ceiling_warning` detects and reports when they have not.

**The members intent is deliberately off.** `guild.get_member` therefore returns nothing for
uncached members, so `elo_roles.find_member` falls back to a REST `fetch_member`, which doubles
as the "did they leave?" check. Do not enable the intent to "fix" this.

## Economy invariant

**Player-versus-player wagers and gifts must conserve coins apart from accrued income.**
House games intentionally create or remove coins through wins and losses; opening an
account also credits its first week's income immediately. `settle()` clamps a
balance at zero so nobody goes into debt, which means settling two sides of a wager with
separate calls *creates coins*: a loser short of the stake pays what they hold while the
winner receives the full amount. That shipped once and minted coins in the coin flip. Use
`economy.settle_pot()` for anything player-versus-player. A supply-conservation test over
20k random flips is the check to repeat after touching this.

Also re-validate both sides at settle time, not just at the start: a coin flip challenger is
checked when they open it, but that is up to 90 seconds before anyone joins and they can
spend the coins in between.

Weekly income is a member's elo, or 1000 unlinked, credited lazily on economy operations
once a week has elapsed since the last payout. The intended backlog cap is 4 weeks; see
the verified follow-up above for the current limitation. Elo is read from a cache the role sync keeps
on each link, so paying income costs no FACEIT request. Income landing mid-game is surfaced
in the message — coins appearing with no explanation reads as a bug to players.

## Tuning that was measured, not guessed

Change these only with a simulation to back it up; `/autobet` (owner only) plays for real
coins and reports the observed return.

- **Slot symbol weights are deliberately fairly flat across eight symbols** (not equal;
  the current weights are in `games.py`). The chance of three of
  a kind is the sum of each symbol's *cubed* probability, so fewer or more lopsided symbols
  make jackpots far too common — an earlier six-symbol set paid one every 18 spins while its
  top prize sat at 1 in 15,600, i.e. never. As tuned: pair ~34%, three of a kind ~1 in 58,
  three sevens ~1 in 2000, a **5.7% house edge** over 400k simulated spins.
- **`/dice` against the house is exactly fair** — both sides roll the same dice and ties
  refund, so the return is 100% by construction. If it ever needs to drain coins, the lever
  is house-wins-ties, worth roughly 5.6% on 2d6.
- **The 30-match window** matches the FACEIT Forecast extension's own default
  (`sliderValue: 30`), which is what the numbers people quote come from.

## Assets

`assets/faceit/level-1..20.{svg,png}` are skill badges 1–20. FACEIT's own scale stops at 10;
11–20 use the Forecast extension's thresholds and artwork, so a player at 2396 elo reads as
level 11 here and level 10 on faceit.com. The badges were rebuilt from the extension's
drawing code, which ships no image files — it assembles each badge at runtime from a colour,
an arc path and digit glyphs.

Regenerating them needs Pillow plus svglib/reportlab/rlPyCairo in a throwaway venv, never as
project dependencies. Two things to know: svglib ignores `fill-opacity`, so the faint
backdrop ring must be flattened against the disc colour first, and the badges sit on an
opaque `#242429` tile rather than transparency so they look the same in both Discord themes.

`assets/win.png` and `assets/loss.png` are the W and L used in result runs, drawn in Arial
Black at 4× and downscaled (Pillow does not antialias text well at target size), `#87FA50`
and `#CF321F`, both at one shared font size so they share a cap height. They are uploaded
once as **application** emoji, which belong to the bot rather than a server, so they render
everywhere with no per-server setup. Discord's emoji edit endpoint only renames — changing
an image means delete and recreate, and the ids change.

`chart.py` renders with matplotlib's `Figure` API, never `pyplot`: pyplot keeps global state
and is not safe to drive from the worker threads these renders run on. Its palette is
anchored on the embed surface `#242429`, and the series colour was validated for lightness
band, chroma and 3:1 contrast rather than picked by eye.

## Running it

The live bot runs on a dedicated Ubuntu server under systemd at `/opt/lightlyyroll`.
This Windows checkout is for development; pushing code does not update the running bot
until the server pulls it and restarts the service.

User preference: after completing each requested change, commit the task's files and push
to the configured Git remote without asking again, unless the user explicitly says to keep
the change local. Check the diff and relevant validation first; do not include unrelated
working-tree edits or secrets. Report the commit/push outcome and any blocked operation.
Server deployment is a separate step; do not report a change as live based on a Git push.

`.env` holds `DISCORD_TOKEN`, optional `GUILD_ID` (guild-scoped command sync, instant
instead of up to an hour) and `OWNER_ID` (the Discord user id allowed to run owner-only
commands — matched by id so a username change cannot hand them over; unset refuses everyone).

`links.json` and `economy.json` are gitignored per-deployment state. They are never
committed, so `git pull` does not carry them and `OWNER_ID` must be set on the server
separately.

Deploy:

```bash
cd /opt/lightlyyroll && sudo -u lightlyyroll git pull
sudo -u lightlyyroll .venv/bin/pip install -r requirements.txt   # only if deps changed
sudo systemctl restart lightlyyroll
```

Always prefix git with `sudo -u lightlyyroll` there; running as yourself triggers git's
dubious-ownership error and can leave root-owned files the service cannot read.

Commands are registered globally, so a new or re-signatured command can take up to an hour
to appear in clients even though it works immediately. With `GUILD_ID` set, startup copies
the commands to that guild and syncs there instead.

## Verification and state safety

Use `.venv/Scripts/python.exe` on Windows and `.venv/bin/python` on Ubuntu. Local syntax
parsing passed for all nine Python modules during this handoff. There is no checked-in
automated test suite or simulation script; the historical simulation results above are
not a substitute for re-running validation after a gameplay change.

For offline checks, parse modules with `ast.parse` or use `compileall`. Importing `bot.py`
requires `DISCORD_TOKEN`, creates the client and registers commands; actually running it
connects to Discord and performs startup sync/emoji work. Use mocks for offline network
checks and redirect `economy.PATH` / `links.PATH` to temporary files when testing writes.
Never run simulations against deployment state or use `/autobet` as an offline test.

After changing shared day logic, verify before/after 05:00, yesterday's upper bound and
both daylight-saving transitions. After changing PvP settlement, repeat the 20k-flip
supply-conservation check with time/income controlled, including a challenger who spent
their stake while waiting. Simulate changed slot weights/payouts before accepting them.

Keep `.env`, runtime JSON, temporary state files and Aider histories out of commits.
Run only one live bot instance per token. State writes are synchronous read-modify-write
operations with atomic replacement, not a multi-process database; do not introduce worker
thread mutations or multiple writers without revisiting synchronization. For a deployed
service-unit change, reinstall the unit and run `systemctl daemon-reload` before restart.
Verify service status/logs and command replies after an authorized deployment.
