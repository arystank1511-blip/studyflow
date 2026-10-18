"""Create the keys for morning reminders. Run once:  python tools/make_push_keys.py

Writes .env.push (ignored by git) with VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY and CRON_SECRET to copy
into Vercel → Settings → Environment Variables, and prints the SHA-256 of CRON_SECRET, which goes
into supabase/push_reminders.sql. Never commit or share the private key or the cron secret.
"""
import base64
import hashlib
import secrets
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


if __name__ == "__main__":
    target = Path(__file__).resolve().parent.parent / ".env.push"
    if target.exists():
        raise SystemExit(f"{target} already exists; delete it first if you really want new keys.")
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_numbers().private_value.to_bytes(32, "big")
    public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    cron_secret = secrets.token_urlsafe(32)
    target.write_text(f"VAPID_PUBLIC_KEY={b64url(public)}\nVAPID_PRIVATE_KEY={b64url(private)}\nCRON_SECRET={cron_secret}\n", encoding="utf-8")
    print(f"Wrote {target}")
    print("SHA-256 of CRON_SECRET for push_reminders.sql:", hashlib.sha256(cron_secret.encode()).hexdigest())
