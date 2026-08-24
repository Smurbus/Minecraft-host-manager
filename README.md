# Contrôleur web pour serveur Minecraft (Raspberry Pi)

Application Flask hébergée sur un Raspberry Pi qui pilote à distance un PC
hôte Ubuntu dédié à un serveur Minecraft :

- 🔋 **Réveil** du PC par Wake-on-LAN
- 🚀 **Lancement** du serveur Minecraft (via SSH + `tmux`)
- 👀 **Statut en direct** (PC éteint / serveur éteint / démarrage / disponible), avec liste des joueurs connectés
- 🛑 **Extinction** du serveur puis du PC (réservée aux administrateurs), avec confirmation si des joueurs sont connectés
- 😴 **Extinction automatique** après 30 minutes sans aucun joueur connecté
- 🔐 Accès protégé par une session web (login/mot de passe), pensé pour être exposé sur Internet derrière un reverse proxy HTTPS
- 👥 **Comptes multi-utilisateurs** : un administrateur peut générer des liens d'invitation à usage unique pour que d'autres joueurs créent leur propre compte (droits limités à démarrer + consulter le statut)

## Architecture

```
app/
  __init__.py     -> application factory Flask
  config.py       -> chargement de instance/config.py (+ valeurs par défaut)
  controller.py   -> WOL, SSH (paramiko), requêtes serveur MC (mcstatus), machine à états, veille auto
  db.py           -> base SQLite (comptes utilisateurs + invitations à usage unique)
  auth.py         -> login/logout, verrouillage anti brute-force, décorateurs login_required/admin_required
  admin.py        -> page d'administration (créer des invitations), page d'inscription via lien d'invitation
  routes.py       -> page d'accueil + API JSON (/api/status, /api/start, /api/stop)
  templates/       -> login.html, index.html, admin.html, register.html, error.html
  static/          -> style.css, app.js (polling + confirmation)
instance/
  config.example.py -> modèle à copier en config.py (jamais commité, voir .gitignore)
  app.db             -> base SQLite générée automatiquement au 1er lancement (jamais commitée)
deploy/
  mc-controller.service -> unité systemd (production, via waitress)
  Caddyfile.example      -> reverse proxy HTTPS automatique
  host-sudoers.example   -> règles sudo NOPASSWD à installer sur le PC hôte
run.py / wsgi.py  -> points d'entrée (dev / prod)
generate_password_hash.py -> génère ADMIN_PASSWORD_HASH et SECRET_KEY
```

Le **PC hôte** (Ubuntu) n'a besoin d'aucune modification logicielle
particulière autre que : `tmux` installé, Wake-on-LAN actif, et une règle
`sudo` sans mot de passe limitée à deux commandes précises (voir plus bas).
C'est le Raspberry qui, via SSH, encapsule votre script existant dans une
session `tmux` nommée (par défaut `mcserver`), ce qui permet ensuite d'y
envoyer la commande `stop` proprement.

## 1. Préparation du PC hôte (Ubuntu)

1. Vérifiez que le Wake-on-LAN persiste après extinction (souvent réinitialisé au boot) :
   ```bash
   sudo apt install ethtool
   ip a   # repérez le nom de l'interface, ex: enp3s0
   sudo ethtool enp3s0 | grep Wake-on
   ```
   Si besoin, rendez-le permanent avec un service systemd ou un fichier
   NetworkManager dispatcher (`sudo nmcli connection modify "Nom connexion" 802-3-ethernet.wake-on-lan magic`).

2. Installez `tmux` :
   ```bash
   sudo apt install tmux
   ```

3. Créez (ou réutilisez) un utilisateur dédié, et notez le **chemin absolu**
   de votre script de lancement (`START_SCRIPT_PATH`).

4. Autorisez le lancement du script et l'extinction **sans mot de passe**,
   mais uniquement pour ces deux commandes précises (jamais `NOPASSWD: ALL`) :
   ```bash
   which bash      # vérifiez le chemin exact (souvent /usr/bin/bash)
   which shutdown  # vérifiez le chemin exact (souvent /usr/sbin/shutdown)
   sudo visudo -f /etc/sudoers.d/mc-controller
   ```
   Collez-y (adapté depuis `deploy/host-sudoers.example`) :
   ```
   votre_utilisateur ALL=(ALL) NOPASSWD: /usr/bin/bash /chemin/vers/script.sh
   votre_utilisateur ALL=(ALL) NOPASSWD: /usr/sbin/shutdown -h now
   ```

5. Autorisez la connexion SSH depuis le Raspberry (voir étape 3 ci-dessous).

## 2. Installation sur le Raspberry Pi

```bash
git clone <votre_repo> mc-controller
cd mc-controller
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 3. Authentification SSH Raspberry → PC hôte (sans mot de passe)

Sur le **Raspberry** :
```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_mchost -N ""
ssh-copy-id -i ~/.ssh/id_ed25519_mchost.pub votre_utilisateur@IP_DU_PC_HOTE
```
Testez que la connexion fonctionne **sans mot de passe** :
```bash
ssh -i ~/.ssh/id_ed25519_mchost votre_utilisateur@IP_DU_PC_HOTE
```
Ce chemin de clé (`~/.ssh/id_ed25519_mchost`) est celui à renseigner dans
`SSH_KEY_PATH`.

## 4. Configuration de l'application

```bash
cp instance/config.example.py instance/config.py
python generate_password_hash.py   # affiche ADMIN_PASSWORD_HASH et SECRET_KEY à coller
```
Éditez `instance/config.py` et renseignez au minimum :
`HOST_MAC`, `HOST_IP`, `BROADCAST_IP`, `SSH_USER`, `SSH_KEY_PATH`,
`START_SCRIPT_PATH`, `ADMIN_USERNAME`, `ADMIN_PASSWORD_HASH`, `SECRET_KEY`.

Ce fichier contient toutes vos données confidentielles : il est listé dans
`.gitignore` et ne doit **jamais** être poussé sur GitHub.

## 5. Test en local

```bash
python run.py
```
Ouvrez `http://IP_DU_RASPBERRY:8000` depuis un navigateur du réseau local,
connectez-vous, puis testez démarrage/arrêt.

## 6. Comptes utilisateurs et invitations

Au premier lancement, l'application crée automatiquement un compte
administrateur dans une base SQLite (`instance/app.db`, générée et ignorée
par git) à partir de `ADMIN_USERNAME` / `ADMIN_PASSWORD_HASH` définis dans
`instance/config.py`. Modifier ces valeurs par la suite n'a plus d'effet :
la base de données est désormais la source de vérité.

Pour inviter d'autres personnes à créer leur propre compte :

1. Connectez-vous avec le compte administrateur.
2. Cliquez sur **Administration** en haut du tableau de bord.
3. Choisissez le rôle du nouveau compte puis cliquez sur **Générer un lien
   d'invitation** :
   - **Utilisateur** : peut démarrer le PC + le serveur et consulter le statut.
   - **Administrateur** : accès complet, y compris éteindre le serveur et
     générer de nouvelles invitations.
4. Copiez le lien affiché (`https://.../register/<token>`) et envoyez-le à
   la personne concernée. Le lien est valable 48h (`INVITE_LIFETIME_HOURS`)
   et devient invalide dès qu'il a été utilisé une fois.
5. La personne ouvre le lien, choisit un nom d'utilisateur et un mot de
   passe (8 caractères minimum), puis peut se connecter normalement.

Pour réinitialiser entièrement les comptes (par exemple en cas de test),
arrêtez le service et supprimez `instance/app.db` : il sera recréé avec un
nouveau compte admin basé sur `instance/config.py` au prochain démarrage.

## 7. Déploiement en production (systemd + waitress)

```bash
sudo cp deploy/mc-controller.service /etc/systemd/system/
sudo nano /etc/systemd/system/mc-controller.service   # adaptez les chemins/utilisateur
sudo systemctl daemon-reload
sudo systemctl enable --now mc-controller
sudo systemctl status mc-controller
```

## 8. Exposition sur Internet (accès distant demandé)

Ne forwardez **jamais** directement le port de l'application vers Internet.
Utilisez un reverse proxy en HTTPS sur le Raspberry (ex. Caddy, qui gère
automatiquement les certificats Let's Encrypt) :

```bash
sudo apt install caddy   # ou suivez la doc officielle Caddy pour Raspberry Pi OS
sudo cp deploy/Caddyfile.example /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile   # remplacez par votre nom de domaine
sudo systemctl restart caddy
```

Sur votre box/routeur, ne forwardez que les ports **80** (validation ACME)
et **443** (HTTPS) vers le Raspberry — jamais le port 8000 de l'app Flask.

Une fois le HTTPS actif, activez le cookie de session sécurisé dans
`instance/config.py` :
```python
SESSION_COOKIE_SECURE = True
```

Il vous faudra aussi un nom de domaine (ou un service de DNS dynamique,
type DuckDNS/No-IP, si votre IP publique n'est pas fixe) pointant vers
votre box, avec le port 443 redirigé vers le Raspberry.

## Fonctionnement des états

| État affiché         | Signification                                            |
|-----------------------|-----------------------------------------------------------|
| PC éteint             | Le PC hôte ne répond pas (port SSH injoignable)           |
| Serveur éteint        | PC allumé, mais le serveur Minecraft ne répond pas         |
| Démarrage en cours    | WOL envoyé et/ou script de lancement en cours d'exécution |
| Extinction en cours   | Arrêt du serveur puis du PC en cours                       |
| Serveur disponible    | Le serveur Minecraft répond (liste des joueurs affichée)   |

Le bouton **Démarrer** envoie le WOL (si nécessaire), attend que le PC
réponde, crée une session `tmux` nommée qui exécute
`sudo bash START_SCRIPT_PATH`, puis attend que le serveur Minecraft
réponde.

Le bouton **Éteindre** vérifie d'abord s'il y a des joueurs connectés :
si oui, une boîte de dialogue de confirmation s'affiche avant de
poursuivre. Une fois confirmé (ou s'il n'y a personne), l'app envoie la
commande `stop` dans la session `tmux`, attend l'arrêt effectif du
serveur, puis exécute `sudo shutdown -h now` sur le PC hôte. **Par
sécurité, si le serveur ne s'arrête pas proprement dans le délai imparti,
le PC n'est pas éteint** (pour éviter de perdre la sauvegarde du monde).

La surveillance d'inactivité tourne en tâche de fond (toutes les 60
secondes) : dès que le nombre de joueurs connectés tombe à 0 pendant
`AUTO_SHUTDOWN_MINUTES` (30 par défaut) minutes consécutives, la séquence
d'extinction est déclenchée automatiquement, exactement comme un clic sur
le bouton « Éteindre » sans confirmation nécessaire.

## Sécurité

- Session Flask signée par `SECRET_KEY`, cookie `HttpOnly` + `SameSite=Lax`
  (et `Secure` une fois en HTTPS).
- Comptes utilisateurs stockés en base SQLite, mots de passe hachés (PBKDF2
  via Werkzeug), jamais en clair. Le bouton **Éteindre** n'est visible et
  actif que pour les comptes administrateur (`/api/stop` renvoie 403 sinon).
- Invitations à usage unique (token aléatoire 256 bits, expiration 48h),
  consommées de façon atomique pour empêcher toute réutilisation, y compris
  en cas de double soumission simultanée.
- Verrouillage temporaire après plusieurs échecs de connexion consécutifs
  depuis la même IP (`LOGIN_MAX_ATTEMPTS` / `LOGIN_LOCKOUT_SECONDS`).
- Protection CSRF (Flask-WTF) sur toutes les requêtes qui modifient l'état
  (start/stop/logout).
- Sur le PC hôte, `sudo` sans mot de passe est restreint à deux commandes
  exactes (jamais `ALL`).
- Toutes les données sensibles (MAC, IP, identifiants, chemin du script,
  clé SSH, secrets) restent dans `instance/config.py`, exclu de git.

## Dépannage

- **"Fichier instance/config.py introuvable"** : suivez l'étape 4.
- **Le démarrage échoue à l'étape SSH** : vérifiez `SSH_KEY_PATH`,
  `SSH_USER`, et que la connexion `ssh -i ... user@host` fonctionne sans
  mot de passe ni interaction.
- **Le script se lance mais le serveur ne répond jamais** : le message
  d'erreur affiché contient les dernières lignes de la session `tmux`
  (équivalent de `tmux capture-pane`) pour diagnostiquer.
- **Le PC ne s'éteint pas** : vérifiez la règle sudoers pour `shutdown`
  et que le chemin de la commande correspond exactement (`which shutdown`).
