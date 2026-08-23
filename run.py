"""Point d'entrée pour le développement local : ``python run.py``.

Pour la production sur le Raspberry Pi, préférez ``wsgi.py`` servi par
waitress ou gunicorn derrière un reverse proxy (voir README.md).
"""

from app import create_app

app = create_app()

if __name__ == "__main__":
    # host="0.0.0.0" pour tester depuis un autre appareil du réseau local.
    # debug=False : ce serveur de développement ne doit jamais être exposé tel quel sur Internet.
    app.run(host="0.0.0.0", port=8000, debug=False)
