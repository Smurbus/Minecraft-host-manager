"""Entry point for local development: ``python run.py``.

For production on the Raspberry Pi, prefer ``wsgi.py`` served by waitress or
gunicorn behind a reverse proxy (see README.md).
"""

from app import create_app

app = create_app()

if __name__ == "__main__":
    # host="0.0.0.0" to test from another device on the local network.
    # debug=False: this development server must never be exposed as-is on the Internet.
    app.run(host="0.0.0.0", port=8000, debug=False)
