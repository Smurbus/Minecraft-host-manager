# -----------------------------------------------------------------------
# Copiez ce fichier en "config.py" (même dossier) puis renseignez vos
# valeurs réelles. "instance/config.py" est ignoré par git : ne JAMAIS
# committer vos vraies valeurs (MAC, IP, identifiants, chemin du script...).
# -----------------------------------------------------------------------

# --- PC hôte Minecraft ---
HOST_MAC = "AA:BB:CC:DD:EE:FF"      # Adresse MAC de la carte réseau du PC (pour le Wake-on-LAN)
HOST_IP = "192.168.1.50"            # IP locale (fixe / réservée par DHCP) du PC hôte
BROADCAST_IP = "192.168.1.255"      # Adresse de broadcast du réseau local (ex : x.x.x.255)

# --- Connexion SSH du Raspberry vers le PC hôte ---
SSH_USER = "votre_utilisateur"
SSH_KEY_PATH = "/home/pi/.ssh/id_ed25519_mchost"   # clé privée dédiée (voir README)
SSH_PORT = 22

# --- Serveur Minecraft ---
MC_PORT = 25565
TMUX_SESSION_NAME = "mcserver"       # nom de la session tmux créée par l'appli sur le PC hôte
START_SCRIPT_PATH = "/chemin/vers/votre/script_de_lancement.sh"

# --- Authentification web (un seul compte administrateur) ---
ADMIN_USERNAME = "admin"
# Générez ce hash avec : python generate_password_hash.py
ADMIN_PASSWORD_HASH = "pbkdf2:sha256:REMPLACEZ_MOI"
# Générez une valeur aléatoire longue, par ex. avec : python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY = "REMPLACEZ_MOI_PAR_UNE_VALEUR_ALEATOIRE_LONGUE"

# --- Comportement ---
AUTO_SHUTDOWN_MINUTES = 30           # extinction auto après N minutes sans aucun joueur

# --- Sécurité (à activer une fois l'app servie en HTTPS derrière un reverse proxy) ---
# SESSION_COOKIE_SECURE = True
