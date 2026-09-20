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

## 3. Ubuntu server deploy

```bash
sudo useradd --system --home /opt/lightlyyroll --shell /usr/sbin/nologin lightlyyroll
sudo mkdir -p /opt/lightlyyroll
# copy bot.py, requirements.txt and .env.example into /opt/lightlyyroll
sudo chown -R lightlyyroll:lightlyyroll /opt/lightlyyroll

cd /opt/lightlyyroll
sudo -u lightlyyroll python3 -m venv .venv
sudo -u lightlyyroll .venv/bin/pip install -r requirements.txt
sudo -u lightlyyroll cp .env.example .env
sudo -u lightlyyroll nano .env        # paste the token, leave GUILD_ID empty
sudo chmod 600 .env

sudo cp deploy/lightlyyroll.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lightlyyroll
sudo systemctl status lightlyyroll
journalctl -u lightlyyroll -f
```

Do not copy `.env` from your dev machine into git — it is gitignored for a reason.
