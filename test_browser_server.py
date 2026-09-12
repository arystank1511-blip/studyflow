"""Optional isolated UI smoke-test server: python test_browser_server.py.

Uses an empty temporary SQLite database on 127.0.0.1:5001, never the personal workspace.
Excluded from Vercel deployments by .vercelignore (test_*.py).
"""
import tempfile
from pathlib import Path


if __name__ == "__main__":
    from app import app, init_db
    with tempfile.TemporaryDirectory(prefix="studyflow-ui-") as directory:
        app.config.update(CLOUD_MODE=False, DATABASE=str(Path(directory) / "smoke.db"),
                          SESSION_COOKIE_SECURE=False, TRUSTED_HOSTS=["127.0.0.1", "localhost"])
        init_db()
        app.run(host="127.0.0.1", port=5001, debug=False, use_reloader=False)
