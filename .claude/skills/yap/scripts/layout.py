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
        self.pictures = self.scenes = 0
        self.turns, self.area = {}, None

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
        """the free area for words: on the pictures of this beat, else above the head, else under the chin"""
        if self.area:
            return self.area
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
        return draw.level(out) if len(out) > 1 else out

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

    # -------------------------------------------------- balanced places
    # one picture: the middle.  two: mirrored left and right.  three: a fan or a triangle.
    def slots(self, count, behind, plan):
        """returns (places, behind, words start, words end); a place is (x, y, w, h, tilt, front)"""
        head, face = self.face["top"], self.face
        top, low = TOP - 20, (head + 60 if behind else head - 30)
        if low - top < 300:  # the head is high: use the full screen or the space under the chin
            top, low = face["chin"] + 40, max(face["chin"] + 480, LOW)
        room = low - top
        lean = 1 if self.scenes % 2 else -1
        if count == 1:
            h = min(580, room)
            return (
                "card",
                [(540, low - h / 2, min(880, int(h * 1.6)), int(h), 1.2 * lean, 0)],
                behind,
                low - h * 0.46,
                head - 12,
            )
        side = min(400, face["x"] + 22, 1080 - face["x"] - face["w"] + 22)
        can_side = face["found"] and side >= 230
        y_side = min(face["chin"] - 70, 1480)
        sides = [
            (side / 2 - 22, y_side, side, int(side * 1.15), -6, 0),
            (1102 - side / 2, y_side, side, int(side * 1.15), 6, 0),
        ]
        if count == 2:
            names = [plan] if plan else (["sides", "pair"] if can_side else ["pair"])
            name = names[self.turns.get(2, 0) % len(names)]
            if name == "sides" and can_side:
                return "sides", sides, False, TOP, head - 20
            h = min(430, room - 20)
            w = min(455, int(h * 1.15))
            y = low - h / 2 - 40
            return (
                "pair",
                [(540 - w * 0.53, y, w, int(h), -4, 0), (540 + w * 0.53, y, w, int(h), 4, 0)],
                behind,
                low - h * 0.46,
                head - 12,
            )
        names = [plan] if plan else (["fan", "triangle"] if can_side else ["fan"])
        name = names[self.turns.get(3, 0) % len(names)]
        if name == "triangle" and can_side:
            h = min(430, room - 20)
            return (
                "triangle",
                sides + [(540, low - h / 2, int(h * 1.5), int(h), 1.0, 0)],
                behind,
                low - h * 0.46,
                head - 12,
            )
        h = min(400, room - 70)
        w, y = int(h * 1.32), low - h / 2 - 70
        return (
            "fan",
            [(540 - 235, y, w, int(h), -6, 0), (540 + 235, y, w, int(h), 6, 0), (540, y + 62, w, int(h), -1, 1)],
            behind,
            y + 20,
            head - 12,
        )

    def add_scene(self, pictures, t1, behind, plan):
        """all the pictures of one beat, placed so the screen never leans to one side"""
        count = min(3, len(pictures))
        pictures = pictures[:count]
        if count == 1 and (pictures[0]["look"] == "full" or plan == "full"):
            frames = [to_image(draw.dress(p, "full", 0, 0, "")) for p in self.picture_files(pictures[0]["file"])]
            self.items += [
                Sprite(frames, pictures[0]["t"], t1, 540, 960, 1080, enter="cut", wiggle=0, z=4, shadow=0),
                Shade(pictures[0]["t"], t1),
            ]
            self.scenes += 1
            return (760, 1180) if self.face["top"] > 700 else (TOP, self.face["top"] - 20)
        name, places, behind, words_top, words_low = self.slots(count, behind, plan)
        self.turns[count] = self.turns.get(count, 0) + 1
        self.scenes += 1
        times = [p["t"] for p in pictures]
        if name in ("sides", "triangle", "pair"):
            times = [times[0] + 0.1 * i for i in range(count)]
        fade = 0.0 if name == "sides" else 0.62
        sprites = []
        for i, (pic, (x, y, w, h, tilt, front)) in enumerate(zip(pictures, places)):
            path, moving = pic["file"], os.path.splitext(pic["file"])[1].lower() in (
                ".mp4",
                ".webm",
                ".gif",
                ".mov",
                ".webp",
            )
            look = pic["look"] if pic["look"] != "full" else "frame"
            if look == "sticker" and not moving:
                path = self.cutout(path)
            low_place = name == "triangle" and i < 2
            frames = [
                to_image(draw.dress(p, look, int(w), int(h), self.secondary, 0.0 if low_place else fade))
                for p in self.picture_files(path)
            ]
            hidden = behind and not low_place
            # in front of the person a middle picture pops in place, so it never flies over the face
            enter = ("up" if hidden else "pop") if x == 540 else ("left" if x < 540 else "right")
            sprites.append(
                Sprite(frames, times[i], t1, x, y, w, tilt, enter, 0.35, behind and not low_place, 2 + i * 0.1)
            )
        if name == "fan":  # early pictures wait in the middle, then make room
            middle, gap = (540, places[0][1], 0), 0.12
            for i, s in enumerate(sprites):
                later = [t for t in times[i + 1 :] if t - times[i] > gap]
                if i == 0 and later:
                    s.path = [(later[0], *places[0][:2], places[0][4])]
                    s.x, s.y, s.tilt = middle
                    s.enter = "up" if s.behind else "pop"
        self.items += sprites
        return words_top, words_low

    def add_emoji(self, chars, t0, t1, alone):
        """one emoji alone peeks over the head in the middle; next to pictures it comes as a mirrored pair"""
        chars = list(chars)[:2] if isinstance(chars, list) else [chars]
        images = [emoji(c) for c in chars]
        if not all(images):
            return
        head = self.face["top"]
        if len(images) == 1 and alone:
            peek = self.style["behind"] != "never"
            self.items.append(
                Sprite(images, t0, t1, 540, head - 100, 220, 0, "up" if peek else "pop", 1.6, peek, 6, 0.35)
            )
            return
        pair = images if len(images) == 2 else images * 2
        for image, x, tilt, flip in ((pair[0], 118, -10, True), (pair[1], 962, 10, False)):
            s = Sprite([image], t0, t1, x, head + 120, 165, tilt, "pop", 1.8, False, 6, 0.35)
            s.flip = flip and pair[0] is pair[1]
            self.items.append(s)

    # -------------------------------------------------- words
    def groups(self, a, b, strong, most):
        out, cur = [], []
        for i in range(a, b + 1):
            cur.append(i)
            ends = self.words[i]["word"].endswith((".", ",", "?", "!", ":"))
            # on a picture a strong word closes its group, so it can be big
            ends = ends or (self.area is not None and i in strong)
            if (
                len(cur) >= most
                or ends
                or i == b
                or (i + 1 <= b and self.words[i + 1]["start"] - self.words[i]["end"] > 0.35)
            ):
                out.append(cur)
                cur = []
        return out

    def say_center(self, a, b, strong, mode):
        top, bottom = self.zone()
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

    def say_calm(self, a, b, strong):
        top, bottom = self.zone()
        for g in self.groups(a, b, strong, 4 if self.area else 5):
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

    def say_fast(self, a, b, strong):
        top, bottom = self.zone()
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
    def beat_pictures(self, beat):
        a = beat["say"][0]
        listed = beat.get("pictures") or [
            {"file": beat.get("photo") or beat.get("clip"), "at": beat.get("at", a), "look": beat.get("look")}
        ]
        out = []
        for pic in listed:
            if not pic.get("file"):
                continue
            look = pic.get("look") or self.looks[self.pictures % len(self.looks)]
            self.pictures += 1
            out.append(dict(file=pic["file"], t=self.start(pic.get("at", a)), look=look))
        return sorted(out, key=lambda p: p["t"])

    def build(self):
        mode, wish = self.style["captions"], self.style["behind"]
        for beat in self.plan["beats"]:
            a, b = beat["say"]
            strong = set(beat.get("strong", []))
            pictures, self.area = self.beat_pictures(beat), None
            first = pictures[0]["t"] if pictures else None
            if pictures:
                behind = beat.get("behind", wish == "lots" or (wish == "some" and self.scenes % 2 == 0))
                area = self.add_scene(pictures, self.stop(b), bool(behind) and wish != "never", beat.get("layout"))
                # words said before the pictures arrive use the same place, so nothing jumps
                self.area = area
                self.sounds += [(p["t"], "swoosh") for p in pictures]
            if beat.get("emoji") and self.style["emoji"] != "none":
                at = self.start(beat.get("emojiAt", max(strong) if strong else b))
                self.add_emoji(beat["emoji"], at, self.stop(b), not pictures)
                self.sounds.append((at, "pop"))
                if not pictures and not isinstance(beat["emoji"], list):
                    top, low = self.zone()
                    self.area = (top, min(low, self.face["top"] - 225)) if self.face["top"] - 225 - top > 200 else None
            if mode == "all-fast":
                self.say_fast(a, b, strong)
            elif mode == "all-calm":
                self.say_calm(a, b, strong)
            else:
                self.say_center(a, b, strong, mode)
            self.area = None
            for i in sorted(strong)[:1]:
                if first is None or self.start(i) >= first - 1:
                    self.zooms.append((self.start(i), self.stop(b)))
        self.items.sort(key=lambda e: e.z)
        return self

    def lean(self, t):
        """how far the pictures and emojis pull to one side at t, in pixels (0 = balanced)"""
        on = [e for e in self.items if isinstance(e, Sprite) and e.on(t)]
        weights = [(e.w * e.w * e.images[0].height() / e.images[0].width(), e.place(t, 0)[0] - 540) for e in on]
        total = sum(w for w, _ in weights)
        return sum(w * x for w, x in weights) / total if total else 0.0

    def problems(self):
        """words touching words, words on the face, words outside the safe area"""
        texts, out, f = [e for e in self.items if isinstance(e, Text)], [], self.face
        t, told = 0.0, -9.0
        while t < self.end:
            pull = self.lean(t)
            # pictures of one scene may arrive 0.2 s apart
            if abs(pull) > 70 and abs(self.lean(t + 0.25)) > 70 and t - told > 1:
                out.append(f"{t:.2f}s the pictures lean {'right' if pull > 0 else 'left'} ({abs(pull):.0f} px)")
                told = t
            t += 0.1
        for e in self.items:
            if not isinstance(e, Sprite) or e.behind or e.w >= 1080 or not f["found"]:
                continue
            h, rise = e.w * e.images[0].height() / e.images[0].width(), 520 if e.enter == "up" else 0
            inside = e.x - e.w / 2 < f["x"] + f["w"] and f["x"] < e.x + e.w / 2
            if inside and e.y - h / 2 < f["chin"] and e.y + h / 2 + rise > f["y"]:
                out.append(f"{e.t0:.2f}s a picture covers the face")
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
