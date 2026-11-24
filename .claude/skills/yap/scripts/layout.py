"""Turns a plan (what appears on which word) and a style (the survey answers) into elements with a place and a time."""

import os
import subprocess

import cv2
import numpy as np

import draw
from draw import Shade, Sprite, Text, emoji, to_image, width
from tools import FFMPEG, HERE, run

LEFT, RIGHT, TOP, LOW = 90, 990, 250, 1500


class Layout:
    def __init__(self, info, words, style, plan, work):
        self.info, self.words, self.style, self.plan, self.work = info, words, style, plan, work
        self.face, self.items, self.sounds, self.zooms = info["face"], [], [], []
        self.end = words[-1]["end"] + 0.6 if words else info["seconds"]
        colors = style["colors"]
        self.primary, self.secondary, self.text = colors["primary"], colors["secondary"], colors["text"]
        looks = style["photoLook"]
        self.looks = looks if isinstance(looks, list) else [looks]
        self.pictures = self.side = 0

    # -------------------------------------------------- helpers
    def word(self, i):
        return self.plan.get("fix", {}).get(str(i), self.words[i]["word"]).strip()

    def start(self, i):
        return self.words[i]["start"]

    def stop(self, i):
        """a word stays until the next one starts, so nothing flickers"""
        if i + 1 < len(self.words):
            return min(self.words[i + 1]["start"], self.words[i]["end"] + 0.7)
        return self.end

    def zone(self):
        """the free area for words: above the head if there is room, else under the chin"""
        above = self.face["top"] - 20 - TOP
        if above >= 230:
            return TOP, self.face["top"] - 20
        return self.face["chin"] + 50, max(self.face["chin"] + 330, LOW)

    def fit(self, text, kind, size, room):
        w = width(text, kind, size)
        return size if w <= room else int(size * room / w)

    # -------------------------------------------------- pictures
    def picture_files(self, path):
        if os.path.splitext(path)[1].lower() in (".mp4", ".webm", ".gif", ".mov", ".webp"):
            raw = run(
                [
                    FFMPEG,
                    "-v",
                    "error",
                    "-t",
                    "5",
                    "-i",
                    path,
                    "-vf",
                    "fps=12,scale=640:-2",
                    "-f",
                    "image2pipe",
                    "-c:v",
                    "png",
                    "-",
                ]
            ).stdout
            pics = [
                cv2.imdecode(np.frombuffer(b"\x89PNG" + b, np.uint8), cv2.IMREAD_UNCHANGED)
                for b in raw.split(b"\x89PNG")[1:]
            ]
        else:
            pics = [cv2.imread(path, cv2.IMREAD_UNCHANGED)]
        pics = [p for p in pics if p is not None]
        if not pics:
            raise SystemExit(f"could not open {path}. Pick another picture.")
        out = []
        for p in pics:
            if p.ndim == 2:
                p = cv2.cvtColor(p, cv2.COLOR_GRAY2BGR)
            out.append(cv2.cvtColor(p, cv2.COLOR_BGRA2RGBA if p.shape[2] == 4 else cv2.COLOR_BGR2RGBA))
        return out

    def cutout(self, path):
        """lifts the subject out of a photo: Apple Vision on a Mac, rembg elsewhere"""
        out = os.path.join(self.work, "cut_" + os.path.splitext(os.path.basename(path))[0] + ".png")
        if os.path.exists(out):
            return out
        exe = os.path.join(os.path.dirname(os.path.abspath(self.work)), ".cutout")
        try:
            if not os.path.exists(exe):
                run(["swiftc", "-O", os.path.join(HERE, "cutout.swift"), "-o", exe])
            done = subprocess.run([exe, path, out], capture_output=True)
            if done.returncode == 0:
                return out
        except (OSError, SystemExit):
            pass
        return path

    def add_picture(self, path, t0, t1, behind, look):
        self.pictures += 1
        self.side = 1 - self.side
        moving = os.path.splitext(path)[1].lower() in (".mp4", ".webm", ".gif", ".mov", ".webp")
        if look == "sticker" and not moving:
            path = self.cutout(path)
        pics = self.picture_files(path)
        if look == "full":
            self.items += [
                Sprite(
                    [to_image(draw.dress(p, "full", 0, 0, "")) for p in pics],
                    t0,
                    t1,
                    540,
                    960,
                    1080,
                    enter="cut",
                    wiggle=0,
                    z=4,
                    shadow=0,
                ),
                Shade(t0, t1),
            ]
            return "full"
        ih, iw = pics[0].shape[:2]
        shape = max(0.7, min(1.5, iw / ih))
        w = 600 if behind else 380
        h = int(w / shape)
        if h > (460 if behind else 430):
            h = 460 if behind else 430
            w = int(h * shape)
        images = [to_image(draw.dress(p, look, w, h, self.secondary)) for p in pics]
        tilt = 4 if self.side else -4
        if behind:
            # off to one side and raised, so the head covers a corner and not the subject
            x = 540 + (1 if self.side else -1) * min(230, 500 - w / 2)
            y = max(self.face["top"] - 40, TOP + 250 + h / 2)
            self.items.append(Sprite(images, t0, t1, x, y, w, tilt, "up", 0.6, True, 2))
            return y - h / 2 - 55
        x = RIGHT - 150 if self.side else LEFT + 150
        y = min(LOW - h / 2 + 60, self.face["chin"] + 60 + h / 2)
        # never over the face: if the corner is too narrow, sit lower
        if self.face["x"] < x + w / 2 and x - w / 2 < self.face["x"] + self.face["w"] and y - h / 2 < self.face["chin"]:
            y = self.face["chin"] + 30 + h / 2
        self.items.append(Sprite(images, t0, t1, x, y, w, tilt, "right" if self.side else "left", 1.0, False, 3))
        return None

    def add_emoji(self, char, t0, t1, behind):
        image = emoji(char)
        if image is None:
            return
        x = 150 if self.side else 930
        self.items.append(
            Sprite([image], t0, t1, x, self.face["top"] + 70, 175, 8 if self.side else -8, "pop", 2.2, behind, 6, 0.35)
        )

    # -------------------------------------------------- words
    def groups(self, a, b, strong, most):
        out, cur = [], []
        for i in range(a, b + 1):
            cur.append(i)
            ends = self.words[i]["word"].endswith((".", ",", "?", "!", ":"))
            if (
                len(cur) >= most
                or ends
                or i == b
                or (i + 1 <= b and self.words[i + 1]["start"] - self.words[i]["end"] > 0.35)
            ):
                out.append(cur)
                cur = []
        return out

    def say_center(self, a, b, strong, mode, ceiling):
        top, bottom = self.zone()
        bottom = min(bottom, ceiling) if ceiling else bottom
        for g in [[i] for i in range(a, b + 1)] if mode == "center-word" else self.groups(a, b, strong, 4):
            text = " ".join(self.word(i) for i in g)
            hot = any(i in strong for i in g)
            size = self.fit(text, "sans", 128 if mode == "center-word" else 84, RIGHT - LEFT)
            y = min(bottom - 10, (top + bottom) / 2 + size * 0.35)
            color = self.primary if hot else self.text
            box = None
            if mode == "center-box":
                box, color = (self.primary if hot else self.secondary), "#1B1813"
                if draw.dark(box):
                    color = "#FFFFFF"
            self.items.append(
                Text(
                    text,
                    self.start(g[0]),
                    self.stop(g[-1]),
                    540,
                    y,
                    size,
                    "sans",
                    color,
                    "pop" if mode == "center-word" else "rise",
                    box=box,
                )
            )

    def say_calm(self, a, b, strong, ceiling):
        top, bottom = self.zone()
        bottom = max(top + 150, min(bottom, ceiling)) if ceiling else bottom
        for g in self.groups(a, b, strong, 3 if ceiling else 5):
            rows, row = [], []
            for i in g:
                if i in strong:
                    rows += ([row] if row else []) + [[i]]
                    row = []
                else:
                    row.append(i)
                    if len(row) == 3:
                        rows.append(row)
                        row = []
            rows += [row] if row else []
            kinds = [("accent", 215) if r[0] in strong else ("sans", 80) for r in rows]
            sizes = [self.fit(" ".join(self.word(i) for i in r), k, s, RIGHT - LEFT) for r, (k, s) in zip(rows, kinds)]
            heights = [s * (0.98 if k == "accent" else 1.22) for s, (k, _) in zip(sizes, kinds)]
            squeeze = min(1.0, (bottom - top) / sum(heights))
            y = top + max(0, (bottom - top - sum(heights) * squeeze) / 2)
            for r, (kind, _), size, high in zip(rows, kinds, sizes, heights):
                size, high = size * squeeze, high * squeeze
                y += high
                gap = size * 0.24
                widths = [width(self.word(i), kind, size) for i in r]
                x = 540 - (sum(widths) + gap * (len(r) - 1)) / 2
                for i, w in zip(r, widths):
                    self.items.append(
                        Text(
                            self.word(i),
                            self.start(i),
                            self.stop(g[-1]),
                            x + w / 2,
                            y - size * (0.24 if kind == "accent" else 0.26),
                            size,
                            kind,
                            self.primary if i in strong else self.text,
                            "pop" if i in strong else "rise",
                        )
                    )
                    x += w + gap

    def say_fast(self, a, b, strong, ceiling):
        top, bottom = self.zone()
        bottom = max(top + 170, min(bottom, ceiling)) if ceiling else bottom
        spots = [(0.30, 0.22), (0.68, 0.55), (0.5, 0.9), (0.66, 0.2), (0.32, 0.6)]
        moves = ["pop", "right", "rise", "left", "pop"]
        for n, g in enumerate(self.groups(a, b, strong, 2)):
            text, hot = " ".join(self.word(i) for i in g), any(i in strong for i in g)
            kind = "accent" if hot else "sans"
            size = self.fit(text, kind, 230 if hot else 104, RIGHT - LEFT)
            fx, fy = (0.5, 0.6) if hot else spots[n % len(spots)]
            half = width(text, kind, size) / 2
            x = max(LEFT + half, min(RIGHT - half, LEFT + (RIGHT - LEFT) * fx))
            y = max(top + size * 0.75, min(bottom - size * 0.15, top + (bottom - top) * fy + size * 0.3))
            self.items.append(
                Text(
                    text,
                    self.start(g[0]),
                    self.stop(g[-1]),
                    x,
                    y,
                    size,
                    kind,
                    self.primary if hot else self.text,
                    "pop" if hot else moves[n % len(moves)],
                    0 if hot else (-4, 3, 0, 4, -3)[n % 5],
                )
            )

    # -------------------------------------------------- all beats
    def build(self):
        mode, behind_ok = self.style["captions"], self.style["behind"] != "never"
        for n, beat in enumerate(self.plan["beats"]):
            a, b = beat["say"]
            strong = set(beat.get("strong", []))
            ceiling = None
            path = beat.get("photo") or beat.get("clip")
            if path:
                at = beat.get("at", a)
                want = beat.get("behind", self.style["behind"] == "lots" or (behind_ok and self.pictures % 2 == 0))
                look = beat.get("look") or self.looks[self.pictures % len(self.looks)]
                got = self.add_picture(path, self.start(at), self.stop(b), bool(want and behind_ok), look)
                ceiling = got if isinstance(got, (int, float)) else None
                self.sounds.append((self.start(at), "swoosh"))
            if beat.get("emoji") and self.style["emoji"] != "none":
                at = beat.get("emojiAt", max(strong) if strong else b)
                self.add_emoji(beat["emoji"], self.start(at), self.stop(b), behind_ok)
                self.sounds.append((self.start(at), "pop"))
            if mode == "all-fast":
                self.say_fast(a, b, strong, ceiling)
            elif mode == "all-calm":
                self.say_calm(a, b, strong, ceiling)
            else:
                self.say_center(a, b, strong, mode, ceiling)
            for i in sorted(strong)[:1]:
                self.zooms.append((self.start(i), self.stop(b)))
        self.items.sort(key=lambda e: e.z)
        return self

    def problems(self):
        """words touching words, words on the face, words outside the safe area"""
        texts, out, f = [e for e in self.items if isinstance(e, Text)], [], self.face
        for i, one in enumerate(texts):
            x0, y0, x1, y1 = one.bounds()
            if x0 < 40 or x1 > 1040 or y0 < 200 or y1 > 1560:
                out.append(f"{one.t0:.2f}s '{one.text}' is too close to the edge")
            if f["found"] and x0 < f["x"] + f["w"] and f["x"] < x1 and y0 < f["chin"] and y1 > f["y"] + f["h"] * 0.1:
                out.append(f"{one.t0:.2f}s '{one.text}' is on the face")
            for two in texts[i + 1 :]:
                if min(one.t1, two.t1) - max(one.t0, two.t0) > 0.05:
                    a0, b0, a1, b1 = two.bounds()
                    if min(x1, a1) - max(x0, a0) > 3 and min(y1, b1) - max(y0, b0) > 3:
                        out.append(f"{one.t0:.2f}s '{one.text}' touches '{two.text}'")
        return out
