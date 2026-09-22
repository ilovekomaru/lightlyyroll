# lightlyyroll

A Discord bot for a small CS2 friend group: FACEIT lookups, elo roles, chance games and a
coin economy. Runs on an Ubuntu server under systemd.

This file records what the code cannot tell you. Everything else — structure, dependencies,
signatures — read from the source.

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
safe because it uses the batched *users* endpoint — one request regardless of member count.

**Discord will not let a bot place a role above its own.** An admin must drag the bot's role
to the top of the list once; `ceiling_warning` detects and reports when they have not.

**The members intent is deliberately off.** `guild.get_member` therefore returns nothing for
uncached members, so `elo_roles._member` falls back to a REST `fetch_member`, which doubles
as the "did they leave?" check. Do not enable the intent to "fix" this.

## Economy invariant

**The total coin supply must only change through weekly income.** `settle()` clamps a
balance at zero so nobody goes into debt, which means settling two sides of a wager with
separate calls *creates coins*: a loser short of the stake pays what they hold while the
winner receives the full amount. That shipped once and minted coins in the coin flip. Use
`economy.settle_pot()` for anything player-versus-player. A supply-conservation test over
20k random flips is the check to repeat after touching this.

Also re-validate both sides at settle time, not just at the start: a coin flip challenger is
checked when they open it, but that is up to 90 seconds before anyone joins and they can
spend the coins in between.

Weekly income is a member's elo, or 1000 unlinked, credited automatically a week after the
last payout and capped at 4 weeks of backlog. Elo is read from a cache the role sync keeps
on each link, so paying income costs no FACEIT request. Income landing mid-game is surfaced
in the message — coins appearing with no explanation reads as a bug to players.

## Tuning that was measured, not guessed

Change these only with a simulation to back it up; `/autobet` (owner only) plays for real
coins and reports the observed return.

- **Slot symbol weights are deliberately flat across eight symbols.** The chance of three of
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
to appear in clients even though it works immediately.
