# -----------------------------------------------------------------------
# Copy this file as "config.py" (same folder), then fill in your real
# values. "instance/config.py" is ignored by git: NEVER commit your actual
# values (MAC, IP, credentials, script path...).
# -----------------------------------------------------------------------

# --- Minecraft host PC ---
HOST_MAC = "AA:BB:CC:DD:EE:FF"      # MAC address of the PC network card (for Wake-on-LAN)
HOST_IP = "192.168.1.50"            # Local IP (static / DHCP reservation) of the host PC
BROADCAST_IP = "192.168.1.255"      # Local network broadcast address (e.g. x.x.x.255)

# --- SSH connection from the Raspberry Pi to the host PC ---
SSH_USER = "your_user"
SSH_KEY_PATH = "/home/pi/.ssh/id_ed25519_mchost"   # dedicated private key (see README)
SSH_PORT = 22

# --- Minecraft server ---
MC_PORT = 25565
TMUX_SESSION_NAME = "mcserver"       # name of the tmux session created by the app on the host PC
START_SCRIPT_PATH = "/path/to/your/start_script.sh"

# --- Web authentication ---
# This account is used only to create the very first administrator account in
# the database (on first start). Additional accounts (including other admins)
# are then created via invitation links from the /admin page - changing these
# values afterward has no effect.
ADMIN_USERNAME = "admin"
# Generate this hash with: python generate_password_hash.py
ADMIN_PASSWORD_HASH = "pbkdf2:sha256:REPLACE_ME"
# Generate a long random value, for example with: python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY = "REPLACE_ME_WITH_A_LONG_RANDOM_VALUE"

# --- Behavior ---
AUTO_SHUTDOWN_MINUTES = 30           # automatic shutdown after N minutes with no players connected
# INVITE_LIFETIME_HOURS = 48         # validity duration for invitation links (48h by default)

# --- Security (enable once the app is served over HTTPS behind a reverse proxy) ---
# SESSION_COOKIE_SECURE = True
