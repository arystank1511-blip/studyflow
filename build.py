"""Copy public assets only; never bundle a local database or .env as static files."""
from pathlib import Path
import shutil

root = Path(__file__).resolve().parent
shutil.copytree(root / "static", root / "public" / "static", dirs_exist_ok=True)
