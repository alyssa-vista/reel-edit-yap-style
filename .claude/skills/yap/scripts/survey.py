"""survey.py [--out my-style.json] [--no-open]   The click-only setup. Opens a page on THIS computer only, waits for Save, writes my-style.json."""

import argparse
import json
import os
import re
import secrets
import socketserver
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SV = os.path.join(ROOT, "survey")
a = argparse.ArgumentParser()
a.add_argument("--out", default="my-style.json")
a.add_argument("--no-open", action="store_true")
a.add_argument("--port", type=int, default=0)
a.add_argument("--minutes", type=float, default=30)
a = a.parse_args()
QS = json.load(open(os.path.join(SV, "questions.json"), encoding="utf-8"))
ST = json.load(open(os.path.join(ROOT, "styles.json"), encoding="utf-8"))
for q in QS:
    if q.get("from"):
        q["options"] = [
            dict(id=s["id"], name=s["name"], say=s["say"], img=f"{q['from']}_{s['id']}", icon=s.get("icon"))
            for s in ST[q["from"]]
        ]
KIT = {}
TOKEN, HEX, DONE = (secrets.token_urlsafe(16), re.compile("^#[0-9a-fA-F]{6}$"), threading.Event())


def check(d):
    """only answers from the list (or a real colour code) get through"""
    out = {}
    for q in QS:
        v = d.get(q["id"])
        if q["type"] == "palette":
            if not (isinstance(v, list) and len(v) == 3 and all((isinstance(c, str) and HEX.match(c) for c in v))):
                raise ValueError(q["id"])
            out["colors"] = dict(primary=v[0], secondary=v[1], text=v[2])
            continue
        ok = {o if isinstance(o, str) else o["id"] for o in q["options"]} | ({"none"} if q.get("none") else set())
        v = v if isinstance(v, list) else [v]
        if (
            not v
            or len(v) > q["pick"]
            or any((not isinstance(x, str) or x not in ok for x in v))
            or ("none" in v and len(v) > 1)
        ):
            raise ValueError(q["id"])
        out[q["id"]] = v if q["pick"] > 1 else v[0]
    return out


class H(BaseHTTPRequestHandler):

    def log_message(s, *x):
        pass

    def send(s, code, body, kind="text/html; charset=utf-8"):
        s.send_response(code)
        s.send_header("Content-Type", kind)
        s.send_header("Content-Length", str(len(body)))
        s.send_header("Cache-Control", "no-store")
        s.send_header("X-Content-Type-Options", "nosniff")
        s.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self'; media-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
        )
        s.end_headers()
        s.wfile.write(body)

    def path_ok(s):
        if s.headers.get("Host") not in (f"127.0.0.1:{PORT}", f"localhost:{PORT}"):
            return None
        p = s.path.split("?")[0]
        return p[len(TOKEN) + 2 :] if p.startswith(f"/{TOKEN}/") else None

    def do_GET(s):
        p = s.path_ok()
        if p is None:
            return s.send(404, b"")
        if p == "":
            return s.send(200, open(os.path.join(SV, "survey.html"), "rb").read())
        if p == "q.json":
            return s.send(200, json.dumps(QS).encode(), "application/json")
        if p in ("survey.css", "survey.js"):
            return s.send(
                200, open(os.path.join(SV, p), "rb").read(), "text/css" if p.endswith("css") else "text/javascript"
            )
        m = re.fullmatch("img/([a-z0-9_]+)\\.(jpg|mp4)", p)
        f = m and os.path.join(SV, "img", f"{m[1]}.{m[2]}")
        return (
            s.send(200, open(f, "rb").read(), "image/jpeg" if m[2] == "jpg" else "video/mp4")
            if f and os.path.isfile(f)
            else s.send(404, b"")
        )

    def kit(s, n):
        if not 0 < n <= 15000000:
            return s.send(413, b"")
        b = s.rfile.read(n)
        ext = (
            "pdf"
            if b[:5] == b"%PDF-"
            else (
                "png"
                if b[:8] == b"\x89PNG\r\n\x1a\n"
                else "jpg" if b[:3] == b"\xff\xd8\xff" else "webp" if b[:4] == b"RIFF" and b[8:12] == b"WEBP" else None
            )
        )
        if not ext:
            return s.send(415, b"")
        d = os.path.join(os.path.dirname(os.path.abspath(a.out)), "brand")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "brand-kit." + ext), "wb") as f:
            f.write(b)
        KIT["file"] = "brand/brand-kit." + ext
        s.send(200, b"ok")

    def do_POST(s):
        if s.path_ok() not in ("save", "kit") or s.headers.get("Origin") not in (
            f"http://127.0.0.1:{PORT}",
            f"http://localhost:{PORT}",
        ):
            return s.send(403, b"")
        n = int(s.headers.get("Content-Length") or 0)
        if s.path_ok() == "kit":
            return s.kit(n)
        if not 0 < n < 20000:
            return s.send(413, b"")
        try:
            out = check(json.loads(s.rfile.read(n)))
        except (ValueError, AttributeError, TypeError) as e:
            return s.send(400, f"check this answer: {e}".encode())
        if KIT:
            out["brandKit"] = KIT["file"]
        tmp = a.out + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1)
        os.replace(tmp, a.out)
        s.send(200, b"saved")
        DONE.set()


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(s):
        socketserver.TCPServer.server_bind(s)
        s.server_name, s.server_port = ("127.0.0.1", s.server_address[1])


srv = Server(("127.0.0.1", a.port), H)
PORT = srv.server_address[1]
url = f"http://127.0.0.1:{PORT}/{TOKEN}/"
threading.Thread(target=srv.serve_forever, daemon=True).start()
print("SURVEY", url, flush=True)
if not a.no_open:
    webbrowser.open(url)
ok = DONE.wait(a.minutes * 60)
time.sleep(0.5)
srv.shutdown()
print("SAVED " + os.path.abspath(a.out) if ok else "TIMED OUT: nothing was saved")
sys.exit(0 if ok else 1)
