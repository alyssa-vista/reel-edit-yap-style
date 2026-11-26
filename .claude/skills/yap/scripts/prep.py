"""prep.py raw/video.mp4

Gets one talking video ready. Everything stays on this computer.
Writes work/<name>/: plate.mp4 (1080x1920), voice.wav, words.json, lines.txt, person.mp4 (who is in front), face.json
When trim.py has made work/<name>/trimmed.mov, that is used instead of the raw video, and the words are checked against
what the trim kept (a missing or doubled word near a cut shows up here).
"""

import argparse
import json
import os
import shutil
import subprocess
import sys

import cv2
import numpy as np

from tools import FFMPEG, HERE, ROOT, load, probe, readable, run, save

W, H = 1080, 1920


def make_plate(video, work, info):
    fps = info["fps"]
    steps = []
    if info.get("hdr"):
        has = b"zscale" in run([FFMPEG, "-hide_banner", "-filters"]).stdout
        if has:
            steps.append("zscale=t=linear:npl=203,format=gbrpf32le,tonemap=mobius,zscale=p=bt709:t=bt709:m=bt709:r=tv")
        else:
            print(
                "NOTE: this is an HDR video. Colours may look pale. On iPhone: Settings, Camera, Record Video, HDR Video off."
            )
    steps += [
        f"fps={fps:g}",
        f"scale={W}:{H}:force_original_aspect_ratio=increase",
        f"crop={W}:{H}",
        "setsar=1",
        "format=yuv420p",
    ]
    run(
        [
            FFMPEG,
            "-v",
            "error",
            "-y",
            "-i",
            video,
            "-map",
            "0:v:0",
            "-vf",
            ",".join(steps),
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "12",
            "-g",
            "15",
            "-an",
            os.path.join(work, "plate.mp4"),
        ]
    )
    run(
        [
            FFMPEG,
            "-v",
            "error",
            "-y",
            "-i",
            video,
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            "48000",
            os.path.join(work, "voice.wav"),
        ]
    )


def listen(work, model, lang, names="Claude"):
    from faster_whisper import WhisperModel

    whisper = WhisperModel(model, device="cpu", compute_type="int8")
    # plain samples, read by us: faster-whisper's own file reader breaks with some versions of its audio library
    raw = run(
        [FFMPEG, "-v", "error", "-i", os.path.join(work, "voice.wav"), "-ac", "1", "-ar", "16000", "-f", "f32le", "-"]
    )
    audio = np.frombuffer(raw.stdout, np.float32)
    segments, _ = whisper.transcribe(audio, language=lang, word_timestamps=True, condition_on_previous_text=False, hotwords=names or None)
    words = [
        dict(i=0, word=w.word.strip(), start=round(w.start, 3), end=round(w.end, 3), sure=round(w.probability, 2))
        for s in segments
        for w in s.words
    ]
    lines, line = [], []
    for n, w in enumerate(words):
        w["i"] = n
        line.append(w)
        if w["word"].endswith((".", "?", "!")) or n == len(words) - 1:
            lines.append(f"[{line[0]['start']:.2f}] " + " ".join(f"{x['i']}:{x['word']}" for x in line))
            line = []
    save(os.path.join(work, "words.json"), words)
    with open(os.path.join(work, "lines.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return words


def read_back(work, words):
    """compare what is heard in the trimmed video with the words the trim meant to keep"""
    import difflib
    import re

    p = os.path.join(work, "expected.txt")
    if not os.path.isfile(p):
        return []
    n = lambda w: re.sub(r"[^a-z0-9']", "", w.lower())
    want = [n(w) for w in open(p, encoding="utf-8").read().split()]
    got = [n(w["word"]) for w in words]
    tj = os.path.join(work, "trim.json")
    joins = load(tj).get("joins") if os.path.isfile(tj) else None
    out = []
    for op, a0, a1, b0, b1 in difflib.SequenceMatcher(None, want, got, autojunk=False).get_opcodes():
        if op != "equal":
            at = words[min(b0, len(words) - 1)]["start"] if words else 0
            if joins is not None and not any(abs(at - j) < 0.8 for j in joins):
                continue  # the listener heard a word a little differently, away from any cut: not a cut problem
            out.append(f"near {int(at // 60)}:{at % 60:04.1f}: \"{' '.join(want[a0:a1])}\" heard as \"{' '.join(got[b0:b1])}\"")
    return out


def person_tool(work):
    """Apple Vision on a Mac, rembg anywhere else"""
    if sys.platform == "darwin" and shutil.which("swiftc"):
        exe = os.path.join(os.path.dirname(os.path.abspath(work)), ".person")
        if not os.path.exists(exe):
            run(["swiftc", "-O", os.path.join(HERE, "person.swift"), "-o", exe])
        return exe
    return None


def frames(path, w, h, pix, n):
    p = subprocess.Popen(
        [FFMPEG, "-v", "error", "-i", path, "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", pix, "-"],
        stdout=subprocess.PIPE,
    )
    while True:
        b = p.stdout.read(w * h * n)
        if len(b) < w * h * n:
            break
        yield np.frombuffer(b, np.uint8).reshape(h, w, n)
    p.stdout.close()
    p.wait()


def find_person(work, fps):
    w, h = 540, 960
    plate, out = os.path.join(work, "plate.mp4"), os.path.join(work, "person.mp4")
    sink = subprocess.Popen(
        [
            FFMPEG,
            "-v",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "gray",
            "-s",
            f"{w}x{h}",
            "-r",
            f"{fps:g}",
            "-i",
            "-",
            "-c:v",
            "libx264",
            "-crf",
            "8",
            "-preset",
            "fast",
            "-pix_fmt",
            "yuv420p",
            out,
        ],
        stdin=subprocess.PIPE,
    )
    exe = person_tool(work)
    if exe:
        src = subprocess.Popen(
            [FFMPEG, "-v", "error", "-i", plate, "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "bgra", "-"],
            stdout=subprocess.PIPE,
        )
        mask = subprocess.Popen([exe, str(w), str(h)], stdin=src.stdout, stdout=subprocess.PIPE)
        shutil.copyfileobj(mask.stdout, sink.stdin)
        mask.wait()
    else:
        from rembg import new_session, remove

        session = new_session("u2net_human_seg")
        for f in frames(plate, w, h, "rgb24", 3):
            sink.stdin.write(remove(f, session=session, only_mask=True).tobytes())
    sink.stdin.close()
    sink.wait()


def find_face(work, fps):
    det = cv2.FaceDetectorYN.create(
        os.path.join(ROOT, "assets", "face_detection_yunet_2023mar.onnx"), "", (540, 960), 0.7
    )
    boxes, tops = [], []
    step = max(1, round(fps / 2))
    masks = frames(os.path.join(work, "person.mp4"), 540, 960, "gray", 1)
    for n, (f, m) in enumerate(zip(frames(os.path.join(work, "plate.mp4"), 540, 960, "bgr24", 3), masks)):
        if n % step:
            continue
        _, found = det.detect(np.ascontiguousarray(f))
        if found is None or not len(found):
            continue
        x, y, w, h = [float(v) * 2 for v in max(found, key=lambda r: r[2] * r[3])[:4]]
        boxes.append((x, y, w, h))
        band = m[:, int(max(0, x / 2)) : int(min(540, (x + w) / 2)), 0]
        rows = np.where((band > 128).mean(1) > 0.3)[0]
        tops.append(float(rows[0]) * 2 if len(rows) else y - h * 0.45)
    if not boxes:
        print("NOTE: no face found. Text will use the top of the frame.")
        return dict(x=390, y=800, w=300, h=400, top=640, chin=1200, found=False)
    b = np.array(boxes)
    lo, hi = np.percentile(b, 5, 0), np.percentile(b, 95, 0)
    x, y = lo[0], lo[1]
    right, chin = np.percentile(b[:, 0] + b[:, 2], 95), np.percentile(b[:, 1] + b[:, 3], 95)
    return dict(
        x=x,
        y=y,
        w=right - x,
        h=chin - y,
        top=float(np.percentile(tops, 8)),
        chin=float(chin),
        found=True,
        moves=float(hi[0] - lo[0]),
    )


if __name__ == "__main__":
    a = argparse.ArgumentParser()
    a.add_argument("video")
    a.add_argument("--model", default="small.en")
    a.add_argument("--lang", default="en")
    a.add_argument("--names", default="Claude", help="names the listener should expect, comma separated")
    a.add_argument("--untrimmed", action="store_true", help="use the raw video even when a trimmed one exists")
    a = a.parse_args()
    work = os.path.join("work", os.path.splitext(os.path.basename(a.video))[0])
    os.makedirs(work, exist_ok=True)
    trimmed = os.path.join(work, "trimmed.mov")
    source = trimmed if os.path.isfile(trimmed) and not a.untrimmed else readable(a.video, work)
    if source == trimmed:
        print("using the trimmed video", flush=True)
    info = probe(source)
    if not info["sound"]:
        sys.exit("This video has no sound, so there are no words to follow.")
    info["fps"] = min((24, 25, 30, 50, 60), key=lambda f: abs(f - info["fps"]))
    print("1 of 4  getting the video ready", flush=True)
    make_plate(source, work, info)
    print("2 of 4  listening", flush=True)
    words = listen(work, a.model, a.lang, a.names)
    print("3 of 4  finding you in the picture (the slow part)", flush=True)
    find_person(work, info["fps"])
    print("4 of 4  finding your face", flush=True)
    info["face"] = find_face(work, info["fps"])
    save(os.path.join(work, "info.json"), dict(info, video=os.path.abspath(a.video)))
    unsure = [f"{w['i']}:{w['word']}" for w in words if w["sure"] < 0.5]
    diff = read_back(work, words) if source == trimmed else []
    print(json.dumps(dict(work=work, words=len(words), seconds=round(info["seconds"], 1), not_sure_about=unsure,
                          heard_differently_near_cuts=diff[:12])))  # the cuts themselves were checked by time in trim.py
