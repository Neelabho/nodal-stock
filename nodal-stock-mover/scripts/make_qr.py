"""Create a QR code PNG for the public URL:  python scripts/make_qr.py https://your-app.onrender.com  ->  docs/qr.png"""
import sys
from pathlib import Path
import qrcode

url = sys.argv[1] if len(sys.argv) > 1 else None
if not url or not url.startswith("https://"):
    sys.exit("usage: make_qr.py https://<your-public-url>   (HTTPS is required for the PWA and service worker)")
out = Path(__file__).resolve().parents[1] / "docs" / "qr.png"
qrcode.make(url, box_size=12, border=3).save(out)
print("wrote", out)
