# lightlyyroll

A minimal Discord bot with one slash command:

- `/roll` — random number 0–100
- `/roll <number>` — random number 0–`<number>`

## 1. Discord setup

1. Go to https://discord.com/developers/applications → **New Application**.
2. **Bot** tab → **Reset Token** → copy it. No privileged intents are needed.
3. **OAuth2 → URL Generator** → scopes `bot` + `applications.commands`, bot permission
   `Send Messages`. Open the generated URL to invite the bot to your server.

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
