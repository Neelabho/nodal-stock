"""Static checks for the installable PWA and the iPhone layout rules. (Device/browser behaviour is verified manually; see STATUS.md.)"""
import json, re, shutil, struct, subprocess
from pathlib import Path
import pytest

FE = Path(__file__).resolve().parents[1] / "frontend"


def png_size(p):
    with open(p, "rb") as f:
        head = f.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", head[16:24])


def test_manifest_is_installable():
    m = json.loads((FE / "manifest.webmanifest").read_text())
    assert m["display"] == "standalone" and m["start_url"] == "/" and m["theme_color"] == "#852201" and m["short_name"]
    sizes = {i["sizes"]: i for i in m["icons"]}
    assert "192x192" in sizes and "512x512" in sizes and any(i["purpose"] == "maskable" for i in m["icons"])
    for i in m["icons"]:
        w, h = png_size(FE / i["src"].replace("/static/", ""))
        assert f"{w}x{h}" == i["sizes"]


def test_apple_touch_icon_and_ios_meta_tags():
    html = (FE / "index.html").read_text()
    assert "viewport-fit=cover" in html and 'rel="manifest"' in html and "apple-mobile-web-app-capable" in html
    assert png_size(FE / "icons" / "apple-touch-icon.png") == (180, 180)


def test_service_worker_never_caches_or_replays_writes_and_flags_stale_data():
    sw = (FE / "sw.js").read_text()
    assert 'req.method !== "GET"' in sw and "return;" in sw          # approvals bypass the worker entirely
    assert "x-nodal-cache" in sw                                      # offline copies are flagged so the UI can say they are stale
    assert "/api/admin" in sw                                         # operator endpoints are never cached
    assert "skipWaiting" in sw and "caches.delete" in sw              # old caches are cleaned on update


def test_tab_bar_has_at_most_five_items_and_touch_targets_are_44px():
    html = (FE / "index.html").read_text()
    bar = re.search(r'<nav class="tabbar.*?</nav>', html, re.S).group(0)
    assert len(re.findall(r"<button", bar)) <= 5                      # Apple HIG: few tabs
    css = (FE / "styles.css").read_text()
    assert "min-height:52px" in css and "min-height:48px" in css and "min-height:44px" in css
    assert "env(safe-area-inset-bottom)" in css and "env(safe-area-inset-top)" in css
    assert "-webkit-text-size-adjust:100%" in css


def test_design_direction_glass_over_blurred_solid_light_no_gradients_thin_lining_type():
    css = re.sub(r"/\*.*?\*/", "", (FE / "styles.css").read_text(), flags=re.S)       # ignore comments
    assert "gradient" not in css                                       # glow comes from blurred SOLID blobs, never gradient fills
    assert "backdrop-filter" in css and "filter:blur(90px)" in css and ".blob" in css
    assert not re.search(r"box-shadow:\s*(?!inset|none)-?\d+(?:px)?\s+-?\d+(?:px)?\s+[1-9]", css)    # no blurred drop shadows (inset rim light and 0-blur hairlines only)
    assert "Outfit" in css and "lining-nums" in css and "font-weight:200" in css
    assert "prefers-reduced-motion" in css                             # animations respect the setting
    html = (FE / "index.html").read_text()
    assert "Outfit" in html and "Rufina" not in html and 'id="splash"' in html


def test_red_is_reserved_for_critical():
    css = re.sub(r"/\*.*?\*/", "", (FE / "styles.css").read_text(), flags=re.S)
    uses = re.findall(r"var\(--critical(?:-soft)?\)", css)
    assert len(uses) <= 8                                              # a handful of critical-only uses (pill, hover on reject, toast-free)
    assert ".pill.Critical" in css


def test_approvals_require_a_network_and_explain_no_dispatch():
    js = (FE / "app.js").read_text()
    assert "navigator.onLine" in js and "Approvals need a connection" in js
    assert "No stock is dispatched" in js and "needs_confirmation" not in js or "confirm(" in js


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
def test_frontend_javascript_parses():
    r = subprocess.run(["node", "-e", f"new Function(require('fs').readFileSync({json.dumps(str(FE / 'app.js'))},'utf8'))"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
