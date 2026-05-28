"""Gravitas Leads — Mailer Portal entrypoint.

WSGI app object `app` is exposed at module level for gunicorn:
    gunicorn --worker-tmp-dir /dev/shm run:app

`python run.py` keeps working for local dev — debug mode and bind host/port
all read from env vars (FLASK_ENV, HOST, PORT) so the same file works in dev,
on a Droplet, and on App Platform.
"""
import os
import sys

# Use bundled lib/ for dependencies on machines without a venv.
# On DO App Platform / gunicorn, deps come from requirements.txt; this block
# is a no-op there because lib/ isn't shipped to the container.
HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.join(HERE, 'lib')
if os.path.isdir(LIB) and LIB not in sys.path:
    sys.path.insert(0, LIB)

from app import create_app
from config import DevelopmentConfig, ProductionConfig

_env = os.getenv('FLASK_ENV', 'development').lower()
_cfg = ProductionConfig if _env == 'production' else DevelopmentConfig
app = create_app(_cfg)

if __name__ == '__main__':
    host = os.getenv('HOST', '0.0.0.0')
    port = int(os.getenv('PORT', app.config.get('PORT', 5070)))
    debug = _env != 'production'
    app.run(host=host, port=port, debug=debug)
