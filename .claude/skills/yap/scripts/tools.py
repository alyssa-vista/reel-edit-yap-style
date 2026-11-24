"""shared helpers: ffmpeg, style, work folder"""

import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _bin(n):
    try:
        import static_ffmpeg.run as r

        a, b = r.get_or_fetch_platform_executables_else_raise()
        return a if n == "ffmpeg" else b
    except Exception:
        pass
    return shutil.which(n) or sys.exit(f"{n} is missing. Run setup.py first.")


FFMPEG, FFPROBE = (_bin("ffmpeg"), _bin("ffprobe"))


def run(a, **k):
    r = subprocess.run([str(x) for x in a], capture_output=True, **k)
    if r.returncode:
        raise SystemExit(f"{os.path.basename(str(a[0]))} failed: " + r.stderr.decode(errors="replace")[-900:])
    return r


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save(p, d):
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1, ensure_ascii=False)


def probe(v):
    d = json.loads(run([FFPROBE, "-v", "error", "-show_streams", "-show_format", "-of", "json", v]).stdout)
    s = next((x for x in d["streams"] if x["codec_type"] == "video"))
    w, h = (s["width"], s["height"])
    rot = next((int(x.get("rotation", 0)) for x in s.get("side_data_list", []) if "rotation" in x), 0)
    if abs(rot) in (90, 270):
        w, h = (h, w)
    n, m = s["avg_frame_rate"].split("/")
    return dict(
        w=w,
        h=h,
        fps=float(n) / float(m),
        seconds=float(d["format"]["duration"]),
        sound=any((x["codec_type"] == "audio" for x in d["streams"])),
        hdr=s.get("color_transfer") in ("arib-std-b67", "smpte2084"),
    )
