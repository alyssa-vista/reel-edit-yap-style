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


def readable(video, work):
    """Some cameras (Sony .MP4 files, for one) write a header FFmpeg cannot open. On a Mac, Apple's own avconvert rewraps
    the file as it is (no re-encoding, same quality) into work/<name>/source.mov. Returns a path FFmpeg can read."""
    ok = lambda f: subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", f], capture_output=True).returncode == 0
    if ok(video):
        return video
    out = os.path.join(work, "source.mov")
    if os.path.isfile(out) and os.path.getmtime(out) >= os.path.getmtime(video) and ok(out):
        return out
    if sys.platform == "darwin" and shutil.which("avconvert"):
        print("this camera file needs rewrapping first (no quality lost)", flush=True)
        os.makedirs(work, exist_ok=True)
        r = subprocess.run(["avconvert", "--preset", "PresetPassthrough", "--source", video, "--output", out, "--replace"], capture_output=True)
        if r.returncode == 0 and ok(out):
            return out
    sys.exit("This video file cannot be opened. Export or save it again as MP4 or MOV from the camera or phone app, then try again.")


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
