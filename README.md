# Web controller for a Minecraft server (Raspberry Pi)

A Flask application hosted on a Raspberry Pi that remotely controls an Ubuntu host PC dedicated to a Minecraft server:

- 🔋 **Wake** the PC via Wake-on-LAN
- 🚀 **Start** the Minecraft server (via SSH + `tmux`)
- 👀 **Live status** (PC off / server off / starting / available), with a list of connected players
- 🛑 **Shut down** the server and then the PC (administrators only), with confirmation if players are connected
- 😴 **Automatic shutdown** after 30 minutes with no connected players, with a **real-time countdown shown** in the interface
- 🔐 Access protected by a web session (username/password), designed to be exposed on the Internet behind an HTTPS reverse proxy
- 👥 **Multi-user accounts**: an administrator can generate single-use invitation links so other players can create their own account (permissions limited to starting + viewing status)

## Architecture

```
app/
  __init__.py     -> Flask application factory
  config.py       -> loading instance/config.py (+ default values)
  controller.py   -> WOL, SSH (paramiko), MC server queries (mcstatus), state machine, auto-idle shutdown
  db.py           -> SQLite database (user accounts, single-use invitations, activity log)
  auth.py         -> login/logout, anti-brute-force lockout, login_required/admin_required decorators
  admin.py        -> administration: invitations, registration, account management (edit/delete/activity)
  routes.py       -> home page + JSON API (/api/status, /api/start, /api/stop)
  templates/      -> login.html, index.html, admin.html, user_detail.html, register.html, error.html
  static/         -> style.css, app.js (polling + confirmation)
instance/
  config.example.py -> template to copy as config.py (never committed, see .gitignore)
  app.db             -> SQLite database generated automatically on first start (never committed)
deploy/
  mc-controller.service -> systemd unit (production, via waitress)
  Caddyfile.example      -> automatic HTTPS reverse proxy
  host-sudoers.example   -> sudo NOPASSWD rules to install on the host PC
run.py / wsgi.py  -> entry points (dev / prod)
generate_password_hash.py -> generates ADMIN_PASSWORD_HASH and SECRET_KEY
```

The **host PC** (Ubuntu) does not require any special software changes other than: `tmux` installed, Wake-on-LAN enabled, and a passwordless `sudo` rule limited to two exact commands (see below).
The Raspberry Pi uses SSH to wrap your existing start script in a named `tmux` session (default: `mcserver`), which then allows it to send the `stop` command cleanly.

## 1. Preparing the host PC (Ubuntu)

1. Make sure Wake-on-LAN persists after shutdown (it is often reset on boot):
   ```bash
   sudo apt install ethtool
   ip a   # find the interface name, e.g. enp3s0
   sudo ethtool enp3s0 | grep Wake-on
   ```
   If needed, make it persistent with a systemd service or a NetworkManager dispatcher file (`sudo nmcli connection modify "Connection name" 802-3-ethernet.wake-on-lan magic`).

2. Install `tmux`:
   ```bash
   sudo apt install tmux
   ```

3. Create (or reuse) a dedicated user account, and note the **absolute path** to your start script (`START_SCRIPT_PATH`).

4. Allow starting the script and shutting down **without a password**, but only for these two exact commands (never `NOPASSWD: ALL`):
   ```bash
   which bash      # verify the exact path (usually /usr/bin/bash)
   which shutdown  # verify the exact path (usually /usr/sbin/shutdown)
   sudo visudo -f /etc/sudoers.d/mc-controller
   ```
   Paste this in (adapted from `deploy/host-sudoers.example`):
   ```
   your_user ALL=(ALL) NOPASSWD: /usr/bin/bash /path/to/script.sh
   your_user ALL=(ALL) NOPASSWD: /usr/sbin/shutdown -h now
   ```

5. Allow SSH access from the Raspberry Pi (see step 3 below).

## 2. Installing on the Raspberry Pi

```bash
git clone <your_repo> mc-controller
cd mc-controller
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 3. SSH authentication Raspberry Pi → host PC (passwordless)

On the **Raspberry Pi**:
```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_mchost -N ""
ssh-copy-id -i ~/.ssh/id_ed25519_mchost.pub your_user@HOST_PC_IP
```
Test that the connection works **without a password**:
```bash
ssh -i ~/.ssh/id_ed25519_mchost your_user@HOST_PC_IP
```
This key path (`~/.ssh/id_ed25519_mchost`) is the value to put in `SSH_KEY_PATH`.

## 4. Application configuration

```bash
cp instance/config.example.py instance/config.py
python generate_password_hash.py   # prints ADMIN_PASSWORD_HASH and SECRET_KEY to paste in
```
Edit `instance/config.py` and fill in at least:
`HOST_MAC`, `HOST_IP`, `BROADCAST_IP`, `SSH_USER`, `SSH_KEY_PATH`,
`START_SCRIPT_PATH`, `ADMIN_USERNAME`, `ADMIN_PASSWORD_HASH`, `SECRET_KEY`.

This file contains all your confidential data: it is listed in `.gitignore` and must **never** be pushed to GitHub.

## 5. Local test

```bash
python run.py
```
Open `http://RASPBERRY_IP:8000` in a browser on your local network, log in, then test start/stop.

## 6. User accounts and invitations

On first start, the application automatically creates an administrator account in a SQLite database (`instance/app.db`, generated and ignored by git) from `ADMIN_USERNAME` / `ADMIN_PASSWORD_HASH` defined in `instance/config.py`. Changing those values later has no effect: the database is now the source of truth.

To invite other people to create their own account:

1. Log in with the administrator account.
2. Click **Administration** at the top of the dashboard.
3. Choose the role for the new account, then click **Generate invitation link**:
   - **User**: can start the PC + server and view status.
   - **Administrator**: full access, including shutting down the server and generating new invitations.
4. Copy the displayed link (`https://.../register/<token>`) and send it to the relevant person. The link is valid for 48 hours (`INVITE_LIFETIME_HOURS`) and becomes invalid as soon as it has been used once.
5. The person opens the link, chooses a username and password (minimum 8 characters), then can log in normally.

From the **Administration** page, each listed account has a **Manage** link to its detailed page, where the administrator can:

- **Edit** the account username and/or role.
- **Reset** its password (without needing to know the old one).
- **Delete** the account permanently.
- **View activity**: login history (date/time + IP) and the start/shutdown actions triggered by that account.

A few safeguards prevent you from locking yourself out of administration:
- You cannot delete your own account.
- You cannot delete or demote the **last** remaining administrator account (create a second admin first if needed).

To fully reset accounts (for example during testing), stop the service and delete `instance/app.db`: it will be recreated with a new admin account based on `instance/config.py` on the next start.

## 7. Production deployment (systemd + waitress)

```bash
sudo cp deploy/mc-controller.service /etc/systemd/system/
sudo nano /etc/systemd/system/mc-controller.service   # adjust paths/user
sudo systemctl daemon-reload
sudo systemctl enable --now mc-controller
sudo systemctl status mc-controller
```

## 8. Exposing it on the Internet (remote access requested)

**Never** forward the application port directly to the Internet. Use an HTTPS reverse proxy on the Raspberry Pi (for example Caddy, which automatically handles Let's Encrypt certificates):

```bash
sudo apt install caddy   # or follow the official Caddy docs for Raspberry Pi OS
sudo cp deploy/Caddyfile.example /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile   # replace with your domain name
sudo systemctl restart caddy
```

On your router, forward only ports **80** (ACME validation) and **443** (HTTPS) to the Raspberry Pi — never the Flask app's port 8000.

Once HTTPS is active, enable the secure session cookie in `instance/config.py`:
```python
SESSION_COOKIE_SECURE = True
```

You will also need a domain name (or a dynamic DNS service such as DuckDNS/No-IP if your public IP is not static) pointing to your router, with port 443 forwarded to the Raspberry Pi.

## How the states work

| Displayed state      | Meaning                                                   |
|----------------------|-----------------------------------------------------------|
| PC off               | The host PC does not respond (SSH port unreachable)       |
| Server off           | PC is on, but the Minecraft server is not responding      |
| Starting             | WOL sent and/or start script currently running            |
| Shutdown in progress | Server stop then PC shutdown currently running            |
| Server available     | The Minecraft server is responding (player list shown)    |

The **Start** button sends WOL (if needed), waits for the PC to respond, creates a named `tmux` session running `sudo bash START_SCRIPT_PATH`, then waits for the Minecraft server to respond.

The **Shut down** button first checks whether players are connected: if so, a confirmation dialog appears before proceeding. Once confirmed (or if nobody is connected), the app sends the `stop` command to the `tmux` session, waits for the server to stop, then runs `sudo shutdown -h now` on the host PC. **For safety, if the server does not stop cleanly within the allotted time, the PC is not shut down** (to avoid losing the world save).

The inactivity monitor runs in the background (every 60 seconds): as soon as the number of connected players drops to 0 for `AUTO_SHUTDOWN_MINUTES` (30 by default) consecutive minutes, the shutdown sequence is triggered automatically, exactly like clicking the **Shut down** button without requiring confirmation.

As soon as no players are connected anymore (and the server is available), a countdown reading **"⏳ Automatic shutdown in MM:SS"** appears on the dashboard, visible to all accounts (admin or not). The value is calculated server-side on each `/api/status` call (accurate to the second, independently of the 60-second monitoring cycle) and counted down locally in the browser between refreshes. The countdown disappears as soon as a player reconnects.

## Security

- Flask session signed by `SECRET_KEY`, `HttpOnly` + `SameSite=Lax` cookie (and `Secure` once HTTPS is enabled).
- User accounts stored in SQLite, passwords hashed (PBKDF2 via Werkzeug), never in plain text. The **Shut down** button is visible and enabled only for administrator accounts (`/api/stop` returns 403 otherwise).
- Single-use invitations (random 256-bit token, 48-hour expiry), consumed atomically to prevent any reuse, including in case of simultaneous double submission.
- Temporary lockout after multiple consecutive failed login attempts from the same IP (`LOGIN_MAX_ATTEMPTS` / `LOGIN_LOCKOUT_SECONDS`).
- CSRF protection (Flask-WTF) on all requests that change state (start/stop/logout).
- On the host PC, passwordless `sudo` is restricted to two exact commands (never `ALL`).
- All sensitive data (MAC, IP, credentials, script path, SSH key, secrets) stays in `instance/config.py`, excluded from git.

## Troubleshooting

- **"instance/config.py file not found"**: follow step 4.
- **Startup fails at the SSH step**: check `SSH_KEY_PATH`, `SSH_USER`, and that `ssh -i ... user@host` works without a password or interaction.
- **The script starts but the server never responds**: the displayed error message includes the latest lines from the `tmux` session (equivalent to `tmux capture-pane`) for diagnosis.
- **The PC does not shut down**: check the sudoers rule for `shutdown` and make sure the command path matches exactly (`which shutdown`).
