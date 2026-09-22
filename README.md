# lightlyyroll

A lightweight Discord bot: dice rolls plus FACEIT CS2 player lookups.

| Command | What it does |
| --- | --- |
| `/help` | List every command |
| `/roll` | Random number 0–100 |
| `/roll <number>` | Random number 0–`<number>` |
| `/slot <bet>` | Slot machine, reels land one at a time |
| `/dice <2d6> [bet]` | Roll dice; with a bet, play the house |
| `/wheel <a, b, c>` | Pick one of your options at random |
| `/coinflip <bet>` | Flip against the first person to join |
| `/balance [member]` | Coins, weekly income and gift allowance |
| `/pay <member> <amount>` | Give coins away |
| `/rich` | Who holds the most coins |
| `/elo [nickname] [matches]` | Current CS2 ELO plus a chart of the last N matches (default 30, max 100) |
| `/stats [nickname]` | K/D/A, K/D, K/R, HS%, ADR, win rate and the last 5 results |
| `/today [nickname]` | The same stats, but only for matches since 03:00 GMT |
| `/avg [nickname]` | Average kills per match |
| `/whoplayed` | Everyone linked here who has played since 03:00 GMT |
| `/loginfaceit <nickname>` | Link your FACEIT account and get an elo role |
| `/logoutfaceit` | Unlink and remove that role |
| `/faceitchannel [#channel]` | Where to announce level changes (empty turns it off) |
| `/loginfaceitforce <member> <nickname>` | Link someone else — owner only |
| `/logoutfaceitforce <member>` | Unlink someone else — owner only |

Every FACEIT reply is an embed carrying the player's avatar, nickname and skill-level
badge, tinted with that level's colour. The lookup commands take the nickname
as optional: left out, they use the caller's own linked account (this guild first, then any
guild they linked in, so it also works in a DM).

## 1. Discord setup

1. Go to https://discord.com/developers/applications → **New Application**.
2. **Bot** tab → **Reset Token** → copy it. No privileged intents are needed.
3. **OAuth2 → URL Generator** → scopes `bot` + `applications.commands`, bot permission
   `Send Messages`. Open the generated URL to invite the bot to your server.

## Coins

`economy.py` keeps a per-guild balance for each member in `economy.json` (gitignored,
since it is per-deployment state).

- **Income** is a member's FACEIT elo per week, or 1,000 without a linked account. It is
  rolling rather than calendar-based: a week after the last payout, the next economy
  command credits it. Time away accrues but is capped at 4 weeks so a long absence does
  not mint a fortune. Elo is read from the cache the role sync keeps on each link, so
  paying income costs no FACEIT request.
- **Bets** are at least 10 coins and at most the lesser of 5,000 or half the balance, so
  nobody goes all in on one spin and the rich cannot distort the server.
- **Gifts** are capped at 1,000 coins per member per rolling week.
- `/wheel` and `/roll` stay free. `/slot` and `/coinflip` always stake; `/dice` stakes
  only when given a bet, and otherwise still works as a plain dice roller.
- Slot payouts are 2x a pair, 10x three of a kind and 200x three sevens, which measures
  as a **5.7% house edge** over 400k simulated spins — coins drain slowly rather than
  evaporating. `/coinflip` is player versus player with no rake; `/dice` against the
  house is even money with ties refunded.

## Games

`games.py` holds the chance commands; they use no FACEIT data and keep no state.

`/slot` animates by editing its own message, revealing one reel per edit. The eight
symbol weights are deliberately flat: the chance of three of a kind is the sum of each
symbol's cubed probability, so fewer or more lopsided symbols make jackpots far too
common. As tuned, a spin pays a pair ~34% of the time, three of a kind about 1 in 58,
and three sevens about 1 in 2000.

`/coinflip` posts a button; the first person other than the caller to press it becomes
the opponent, and the caller always takes heads. Later clicks are refused rather than
starting a second flip.

## FACEIT data

`faceit.py` reads the same undocumented JSON endpoints that faceit.com's own web app uses,
so no API key is needed and the numbers match a player's profile page exactly:

```
/api/users/v1/nicknames/<nickname>              nickname, avatar, country, ELO
/api/stats/v1/stats/time/users/<id>/games/cs2   per-match stats
/api/searcher/v1/players?query=<nickname>       case-insensitive fallback
```

The nicknames endpoint matches case exactly, so `EL0T3RRORIST` 404s where `el0t3rrorist`
succeeds. On a miss the search endpoint supplies the real spelling. Search is **fuzzy** —
querying `el0t3` also returns `EL0T3RR0RIST`, a different account with different elo — so
only an exact case-insensitive equality is accepted as a match, never the top hit. Search
results carry no elo, hence the second lookup by the canonical nickname.

`/stats` and `/avg` average the **last 30 matches** (`MATCH_WINDOW` in `bot.py`) — the same
default the FACEIT Forecast extension uses (`sliderValue: 30`). Ratios are computed from
summed totals (total kills ÷ total deaths, total damage ÷ total rounds), not by averaging
each match's own ratio; only the former reproduces the figures the site displays.

`/today` filters the same payload by each match's `date` against the most recent 03:00 GMT
boundary (`DAY_RESET_HOUR`). It reports the day's net elo swing and the last few results as
a `W L W W L` run, oldest to newest, capped at `RESULT_RUN` (5), with a green W and a red
L.

The coloured letters are **application emoji** (`assets/win.png`, `assets/loss.png`),
uploaded once on first startup and reused after. They belong to the bot rather than a
server, so they render in every guild and in DMs with no per-server setup, and no Manage
Expressions permission. Discord colours plain text nowhere else — the alternative is an
`ansi` code block, which draws a border around the whole line. If the upload fails the run
falls back to plain letters. The endpoint returns
matches newest first, so the run is reversed for reading order.

`/whoplayed` lists every linked member with matches today, ordered by the day's elo gain
so the biggest climber leads. Elo for everyone comes from one batched request, but today's
matches are per-player on the rate-limited stats endpoint, so a throttled player is counted
in the footer as unavailable rather than failing the whole command.

The per-match payload uses opaque keys; the mapping is documented at the top of `faceit.py`
and was verified against the payload's own ratio fields across a full match window.

The stats endpoint **does** return 429 under heavy use — it was tripped during development
by repeated 100-match pages — and that surfaces as a plain "try again in a minute" message.
The role refresh is unaffected: it uses the batched users endpoint, one request per cycle.

Two things worth knowing:

- **Requests go through `urllib`, not `aiohttp`.** FACEIT's CDN rejects aiohttp's TLS
  signature on the stats endpoint with a 403 no matter what headers are sent, while plain
  `urllib` with an honest bot User-Agent is served normally. The blocking calls run in a
  worker thread so the event loop keeps running. Do not "simplify" this back to aiohttp.
- **These endpoints are undocumented and sit behind bot management.** They work from a
  residential IP; a datacenter IP may be treated differently. If the server starts getting
  403s, switch to the official Data API at https://developers.faceit.com (free key, but it
  serves lifetime stats only and has no ADR).

## Elo roles

`/loginfaceit` gives each linked member their own hoisted role, named `<elo> <nickname>`
(e.g. `2396 el0t3rrorist`) and coloured by their skill level. Discord groups the member list by each member's highest
hoisted role and orders those groups by role position, so keeping the roles sorted by elo
turns the sidebar into a live leaderboard. A background task refreshes every 5 minutes
(`REFRESH_MINUTES` in `bot.py`) and reorders everything in a single API call.

Every member's elo comes from **one** batched FACEIT request per refresh, via the repeated
`id` parameter on `/api/users/v1/users`, so polling cost does not grow with member count.
Note that the comma-separated `ids=` form is accepted but silently ignored — it returns
arbitrary players rather than the ones asked for, so it must not be used.

**One manual setup step.** Discord will not let a bot place a role above its own, so drag
the bot's role to the **top** of the server's role list once. Until that is done, elo roles
sit below whatever outranks the bot; `/loginfaceit` says so when it detects this.

Worth knowing before enabling it:

- Name colour comes from a member's highest *coloured* role, so while these sit on top,
  a FACEIT level colour will override staff colours.
- Discord caps a server at 250 roles, which is the ceiling on linked members.
- Anyone can claim any nickname — there is no proof of ownership without FACEIT OAuth.
- Links live in `links.json` beside the code (gitignored, since it is per-deployment state).
  The systemd unit already grants write access to the install directory.
- Roles are cleaned up when a member leaves or runs `/logoutfaceit`. Role edits happen only
  when a value actually changed, since that is the rate-limited part.
- Changing the name format needs no migration: a role whose name no longer matches the
  computed one is renamed in place on the next sync, which runs at startup.
- `/loginfaceitforce` is restricted to the Discord user id in `OWNER_ID`, matched by id
  rather than username so a rename cannot hand it to someone else. With `OWNER_ID` unset
  nobody can use it. Discord has no owner-only visibility, so the command still appears in
  everyone's list — it just refuses.
- `/faceitchannel` (needs Manage Server) picks where level changes are announced. The last
  seen level is stored per link, so a change is announced once, and never on a first link —
  otherwise everyone would be announced the moment they signed up.

## Elo chart

`/elo` plots elo after each match, oldest to newest, from the `elo` field of the same
per-match payload. The y-axis spans the period's own low and high rather than starting at
zero, since absolute elo never approaches zero and a zero baseline would flatten the line
into a straight edge. 100 is a hard ceiling because the endpoint refuses larger pages.

`chart.py` renders it with matplotlib's `Figure` API rather than `pyplot` — pyplot carries
global state and is not safe to drive from the worker threads these renders run on. The
palette is anchored on the embed's own `#242429` surface so the image reads as part of the
message; the amber series colour was checked against that surface for lightness band,
chroma and 3:1 contrast rather than picked by eye.

Note matplotlib's first import builds a font cache and takes a few seconds; it happens once,
at startup, and is not a hang.

## Level badges

`assets/faceit/` holds skill-level badges 1–20, as `.svg` (source) and `.png` (what Discord
embeds actually display — Discord does not render SVG). The badges sit on an opaque `#242429`
backdrop rather than transparency, so they look the same in Discord's light and dark themes.

FACEIT's own scale stops at level 10 (2001+ ELO). Levels 11–20 subdivide everything above
that, using the thresholds and artwork of the **FACEIT Forecast** browser extension, so a
player at 2396 ELO shows as level 11 here and level 10 on faceit.com. The ELO table and
per-level colours live in `levels.py`.

The badges were rebuilt from the extension's own drawing code rather than copied, since it
ships no image files — it assembles each badge at runtime from a colour, an arc path and
digit glyphs.

## 2. Local development (Windows / PyCharm)

The virtualenv already exists at `.venv`. To recreate it:

```
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

PyCharm usually detects `.venv` automatically. If not: **Settings → Project → Python Interpreter
→ Add Local Interpreter → Existing** → `C:\pr\lightlyyroll\.venv\Scripts\python.exe`.

Copy `.env.example` to `.env` and fill in `DISCORD_TOKEN`. Then:

```
.venv\Scripts\python.exe bot.py
```

**Tip:** global slash commands can take up to an hour to appear the first time. Set `GUILD_ID`
in `.env` to your test server's ID (right-click the server with Developer Mode on → Copy Server ID)
and commands register instantly for that server.

## 3. Publish to GitHub (one time, from Windows)

```powershell
cd C:\pr\lightlyyroll
git add .gitignore .env.example bot.py requirements.txt README.md deploy/lightlyyroll.service
git status                 # confirm .env is NOT listed
git commit -m "Initial commit: Discord roll bot"

gh repo create lightlyyroll --private --source=. --remote=origin --push
```

Without the `gh` CLI: create an empty repo on github.com (no README, no .gitignore), then

```powershell
git remote add origin https://github.com/<you>/lightlyyroll.git
git push -u origin main
```

`.env` is gitignored and must never be committed. The server gets its own copy, created by hand.

## 4. First deploy to the Ubuntu server

```bash
ssh user@your-server
sudo apt update && sudo apt install -y git python3-venv

# service user; its home IS the install dir
sudo useradd --system --home /opt/lightlyyroll --shell /usr/sbin/nologin lightlyyroll
sudo mkdir -p /opt/lightlyyroll
sudo chown lightlyyroll:lightlyyroll /opt/lightlyyroll
```

**Clone — public repo:**

```bash
sudo -u lightlyyroll git clone https://github.com/<you>/lightlyyroll.git /opt/lightlyyroll
```

**Clone — private repo** (read-only deploy key, scoped to this repo only):

```bash
sudo -u lightlyyroll mkdir -p /opt/lightlyyroll/.ssh
sudo -u lightlyyroll chmod 700 /opt/lightlyyroll/.ssh
sudo -u lightlyyroll ssh-keygen -t ed25519 -N "" \
  -f /opt/lightlyyroll/.ssh/id_ed25519 -C "lightlyyroll deploy key"
sudo cat /opt/lightlyyroll/.ssh/id_ed25519.pub
# → GitHub repo → Settings → Deploy keys → Add deploy key
#   (paste it, leave "Allow write access" UNCHECKED)

sudo -u lightlyyroll ssh-keyscan github.com \
  | sudo -u lightlyyroll tee -a /opt/lightlyyroll/.ssh/known_hosts
sudo -u lightlyyroll ssh -T git@github.com      # expect "successfully authenticated"
sudo -u lightlyyroll git clone git@github.com:<you>/lightlyyroll.git /opt/lightlyyroll
```

`git clone` refuses a non-empty target directory. If `/opt/lightlyyroll` already has files from an
earlier attempt, empty it first.

**Venv, token, service:**

```bash
cd /opt/lightlyyroll
sudo -u lightlyyroll python3 -m venv .venv
sudo -u lightlyyroll .venv/bin/pip install -r requirements.txt

sudo -u lightlyyroll cp .env.example .env
sudo -u lightlyyroll nano .env          # DISCORD_TOKEN=... , leave GUILD_ID empty
sudo chmod 600 .env

sudo cp deploy/lightlyyroll.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lightlyyroll
journalctl -u lightlyyroll -f           # expect "Logged in as lightlyyroll#2005"
```

Never build the venv on Windows and copy it over — it hardcodes Windows paths and will not run
on Linux. Always create it natively on the server.

## 5. Update pipeline

Every subsequent change follows the same three steps.

**Step 1 — Windows: commit and push**

```powershell
cd C:\pr\lightlyyroll
git add -u                              # or name the files explicitly
git status                              # confirm .env is NOT listed
git commit -m "Describe the change"
git push
```

**Step 2 — server: pull and restart**

```bash
cd /opt/lightlyyroll
sudo -u lightlyyroll git pull
sudo systemctl restart lightlyyroll
```

If `requirements.txt` changed, reinstall deps before the restart:

```bash
sudo -u lightlyyroll .venv/bin/pip install -r requirements.txt
```

If `deploy/lightlyyroll.service` changed, reinstall the unit before the restart:

```bash
sudo cp deploy/lightlyyroll.service /etc/systemd/system/
sudo systemctl daemon-reload
```

**Step 3 — verify**

```bash
systemctl status lightlyyroll --no-pager
journalctl -u lightlyyroll -n 30 --no-pager
```

A healthy start shows `Active: active (running)` and `Logged in as lightlyyroll#2005`.
Then run `/roll` and `/roll 6` in Discord to confirm the bot actually answers.

**Optional shortcut** — add to your own `~/.bashrc` on the server:

```bash
alias rolldeploy='cd /opt/lightlyyroll \
  && sudo -u lightlyyroll git pull \
  && sudo systemctl restart lightlyyroll \
  && journalctl -u lightlyyroll -n 20 --no-pager'
```

**Rollback** — if a deploy breaks the bot:

```bash
cd /opt/lightlyyroll
sudo -u lightlyyroll git log --oneline -5
sudo -u lightlyyroll git checkout <previous-commit-sha>
sudo systemctl restart lightlyyroll
```

Return to the tip later with `sudo -u lightlyyroll git checkout main`.

## Service management cheat sheet

```bash
sudo systemctl start lightlyyroll
sudo systemctl stop lightlyyroll
sudo systemctl restart lightlyyroll
sudo systemctl status lightlyyroll --no-pager
sudo systemctl enable lightlyyroll       # start on boot
sudo systemctl disable lightlyyroll
journalctl -u lightlyyroll -f            # live logs
journalctl -u lightlyyroll --since "1 hour ago" --no-pager
```

## Gotchas

- **Run one instance at a time.** Stop the bot on your PC before starting the service — two
  processes sharing a token fight over the gateway session and responses turn flaky.
- **Always prefix git with `sudo -u lightlyyroll`** in `/opt/lightlyyroll`. Running it as
  yourself triggers git's "dubious ownership" error and can leave root-owned files the service
  cannot read. Do not work around it with `safe.directory`.
- **`.env` and `.venv/` are gitignored**, so they survive every `git pull` untouched. That is the
  main reason this repo deploys via git rather than file copying.
- **Slash command changes** (name, description, parameters) need a re-sync, which happens
  automatically on restart. Global syncs can take up to an hour to propagate.
