"""make.py work/<name> check | stills 1.2 3.4 ... | video
Draws the plan on the video. Reads work/<name>/plan.json and my-style.json."""

import argparse
import math
import os
import subprocess
import sys

import cv2
import numpy as np
import skia

import draw
import sound
from layout import Layout
from tools import FFMPEG, load, run

W, H = 1080, 1920


def ready(work, style):
    info, words, plan = (load(os.path.join(work, n)) for n in ("info.json", "words.json", "plan.json"))
    s = load(style)
    draw.set_fonts(s["font"], s["accent"])
    cv2.setNumThreads(2)
    layout = Layout(info, words, s, plan, work).build()
    light = wall_light(work, info["face"])
    layout.wall = float(plan.get("wall", 1.0 if light < 0.55 else max(0.6, 1.0 - (light - 0.5) * 0.95)))
    return info, s, layout


def reader(path, w, h, pix, n, first, count, fps):
    seek = ["-ss", f"{max(0, (first - 0.5) / fps):.5f}"] if first else []
    p = subprocess.Popen(
        [
            FFMPEG,
            "-v",
            "error",
            *seek,
            "-i",
            path,
            "-frames:v",
            str(count),
            "-vf",
            f"scale={w}:{h}",
            "-f",
            "rawvideo",
            "-pix_fmt",
            pix,
            "-",
        ],
        stdout=subprocess.PIPE,
    )
    while True:
        b = p.stdout.read(w * h * n)
        if len(b) < w * h * n:
            break
        yield np.frombuffer(b, np.uint8).reshape(h, w, n)
    p.stdout.close()
    p.wait()


def zoom_at(zooms, t):
    for a, b in zooms:
        if a <= t < b:
            return 1 + 0.07 * draw.ease(draw.part(t, a, 0.25)) + 0.012 * (t - a)
    return 1.0


def paint(canvas, items, t, behind):
    for e in items:
        if e.behind == behind and e.on(t):
            e.draw(canvas, t)


def wall_light(work, face):
    """how bright the wall is where the words go (0 to 1)"""
    cap = cv2.VideoCapture(os.path.join(work, "plate.mp4"))
    ok, f = cap.read()
    cap.release()
    if not ok:
        return 0.0
    area = f[250 : max(330, int(face["top"]) - 20), 90:990]
    return float(area.mean()) / 255


def compose(frame, mask, layout, t):
    z, cx, cy = zoom_at(layout.zooms, t), 540.0, 1150.0
    move = np.float32([[z, 0, cx - cx * z], [0, z, cy - cy * z]])
    rgb = cv2.warpAffine(frame, move, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    page = np.ascontiguousarray(np.dstack([rgb, np.full((H, W), 255, np.uint8)]))
    if layout.wall < 1 or any(e.behind and e.on(t) for e in layout.items):
        # holes in the mask (a microphone, jewellery) are filled so pictures never show through the person
        m = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)))
        k = W / m.shape[1]
        m = cv2.warpAffine(
            m,
            np.float32([[k * z, 0, cx - cx * z], [0, k * z, cy - cy * z]]),
            (W, H),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        )
        m = cv2.GaussianBlur(m, (0, 0), 1.4).astype(np.float32)[..., None] / 255
        if layout.wall < 1:
            # a bright wall is turned down so light words can be read; the person is not touched
            page[..., :3] = (np.power(page[..., :3] / 255.0, 1.18) * 255 * layout.wall).astype(np.uint8)
        surface = skia.Surface(page)
        paint(surface.getCanvas(), layout.items, t, True)
        surface.flushAndSubmit()
        page[..., :3] = (page[..., :3] * (1 - m) + rgb * m).astype(np.uint8)
    surface = skia.Surface(page)
    paint(surface.getCanvas(), layout.items, t, False)
    surface.flushAndSubmit()
    return page[..., :3]


def frames(work, info, layout, first, count):
    fps = info["fps"]
    plate = reader(os.path.join(work, "plate.mp4"), W, H, "rgb24", 3, first, count, fps)
    masks = reader(os.path.join(work, "person.mp4"), 540, 960, "gray", 1, first, count, fps)
    for n, (f, m) in enumerate(zip(plate, masks)):
        yield compose(f, m[..., 0], layout, (first + n) / fps + 1e-4)


def stills(work, info, layout, times):
    tiles = []
    for t in times:
        for f in frames(work, info, layout, int(round(t * info["fps"])), 1):
            small = cv2.resize(f, (324, 576), interpolation=cv2.INTER_AREA)[..., ::-1].copy()
            cv2.putText(small, f"{t:.2f}", (6, 20), 0, 0.6, (0, 255, 0), 2)
            tiles.append(small)
    while len(tiles) % 6 and len(tiles) > 6:
        tiles.append(np.zeros_like(tiles[0]))
    rows = [np.hstack(tiles[i : i + 6]) for i in range(0, len(tiles), 6)]
    out = os.path.join(work, "stills.jpg")
    cv2.imwrite(out, np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 90])
    print(out)


def piece(work, info, layout, first, count, out):
    p = subprocess.Popen(
        [
            FFMPEG,
            "-v",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{W}x{H}",
            "-r",
            str(info["fps"]),
            "-i",
            "-",
            "-vf",
            "scale=out_color_matrix=bt709:out_range=tv",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "14",
            "-pix_fmt",
            "yuv420p",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            out,
        ],
        stdin=subprocess.PIPE,
    )
    for f in frames(work, info, layout, first, count):
        p.stdin.write(f.tobytes())
    p.stdin.close()
    if p.wait():
        sys.exit("the video part failed")


def video(work, info, layout, style_file, jobs):
    total = int(min(info["seconds"], layout.end) * info["fps"])
    cuts = [total * i // jobs for i in range(jobs + 1)]
    parts = [os.path.join(work, f"part{i}.mp4") for i in range(jobs)]
    running = [
        subprocess.Popen(
            [
                sys.executable,
                os.path.abspath(__file__),
                work,
                "piece",
                str(cuts[i]),
                str(cuts[i + 1] - cuts[i]),
                parts[i],
                "--style",
                style_file,
            ]
        )
        for i in range(jobs)
    ]
    if any(p.wait() for p in running):
        sys.exit("the video did not finish")
    with open(os.path.join(work, "parts.txt"), "w", encoding="utf-8") as f:
        f.write("".join(f"file '{os.path.basename(p)}'\n" for p in parts))
    mix = sound.mix(
        os.path.join(work, "voice.wav"),
        layout.sounds,
        style_amount(load(style_file)),
        total / info["fps"],
        os.path.join(work, "mix.wav"),
    )
    os.makedirs("final", exist_ok=True)
    name, v = os.path.splitext(os.path.basename(info["video"]))[0], 1
    while os.path.exists(os.path.join("final", f"{name} v{v}.mp4")):
        v += 1
    out = os.path.join("final", f"{name} v{v}.mp4")
    run(
        [
            FFMPEG,
            "-v",
            "error",
            "-y",
            "-f",
            "concat",
            "-i",
            os.path.join(work, "parts.txt"),
            "-i",
            mix,
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "256k",
            "-movflags",
            "+faststart",
            "-shortest",
            out,
        ]
    )
    for p in parts:
        os.remove(p)
    print("DONE", out)


def style_amount(style):
    return dict(yes=1.0, soft=0.45, no=0.0)[style["sound"]]


if __name__ == "__main__":
    a = argparse.ArgumentParser()
    a.add_argument("work")
    a.add_argument("what", choices=["check", "stills", "video", "piece"])
    a.add_argument("rest", nargs="*")
    a.add_argument("--style", default="my-style.json")
    a.add_argument("--jobs", type=int, default=max(1, min(4, (os.cpu_count() or 2) // 2)))
    a = a.parse_args()
    info, style, layout = ready(a.work, a.style)
    if a.what == "check":
        found = layout.problems()
        print(
            "\n".join(found) or "no problems",
            f"\n{len(layout.items)} things on screen, {layout.pictures} pictures, {math.ceil(layout.end)} s",
        )
    elif a.what == "stills":
        stills(a.work, info, layout, [float(t) for t in a.rest])
    elif a.what == "piece":
        piece(a.work, info, layout, int(a.rest[0]), int(a.rest[1]), a.rest[2])
    else:
        video(a.work, info, layout, a.style, a.jobs)
