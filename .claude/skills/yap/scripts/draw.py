"""Everything that can appear on screen. Each element is a pure function of time, so any frame can be drawn on its own."""

import functools
import math

import cv2
import numpy as np
import skia

import fonts

W, H = 1080, 1920
SMOOTH = skia.SamplingOptions(skia.CubicResampler.Mitchell())
SHADOW = "#140c02"
TRACK = {"sans": -0.04, "accent": -0.02}
TYPE = {}


def set_fonts(main, accent):
    TYPE["sans"] = skia.Typeface.MakeFromFile(fonts.path(main))
    TYPE["accent"] = skia.Typeface.MakeFromFile(fonts.path(main if accent.startswith("Same") else accent))
    manager = skia.FontMgr()
    for name in ("Apple Color Emoji", "Segoe UI Emoji", "Noto Color Emoji"):
        face = manager.matchFamilyStyle(name, skia.FontStyle.Normal())
        if face and name.split()[1] in face.getFamilyName():
            TYPE["emoji"] = face
            return


def colour(code, alpha=1.0):
    r, g, b = (int(code[i : i + 2], 16) for i in (1, 3, 5))
    return skia.ColorSetARGB(int(max(0, min(1, alpha)) * 255), r, g, b)


def dark(code):
    r, g, b = (int(code[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b < 0.45


def part(t, start, length):
    return max(0.0, min(1.0, (t - start) / length)) if length else float(t >= start)


def ease(x):
    return 1 - (1 - x) ** 4


def spring(x, s=1.7):
    return 1 + (s + 1) * (x - 1) ** 3 + s * (x - 1) ** 2


@functools.lru_cache(None)
def font(kind, size):
    f = skia.Font(TYPE[kind], size)
    f.setSubpixel(True)
    f.setEdging(skia.Font.Edging.kAntiAlias)
    return f


def track(text, kind, size):
    # 'r' next to 'n' reads as 'm' when the letters sit too close
    return (-0.005 if "rn" in text else TRACK[kind]) * size


def width(text, kind, size):
    f = font(kind, size)
    return sum(f.getWidths(f.textToGlyphs(text))) + track(text, kind, size) * max(0, len(text) - 1)


@functools.lru_cache(4000)
def outline(text, kind, size):
    f, x, path = font(kind, size), 0.0, skia.Path()
    glyphs = f.textToGlyphs(text)
    for glyph, w in zip(glyphs, f.getWidths(glyphs)):
        shape = f.getPath(glyph)
        if shape:
            path.addPath(shape, x, 0)
        x += w + track(text, kind, size)
    return path


class Element:
    def __init__(self, t0, t1, behind=False, z=0):
        self.t0, self.t1, self.behind, self.z = t0, t1, behind, z

    def on(self, t):
        return self.t0 - 1e-6 <= t < self.t1 - 1e-6


class Text(Element):
    """one word or phrase; x is its centre, y its baseline"""

    def __init__(
        self, text, t0, t1, x, y, size, kind="sans", color="#FFFFFF", move="rise", tilt=0, box=None, behind=False, z=8
    ):
        super().__init__(t0, t1, behind, z)
        self.text, self.x, self.y, self.size, self.kind, self.color = text, x, y, size, kind, color
        self.move, self.tilt, self.box = move, tilt, box

    def bounds(self):
        b = outline(self.text, self.kind, self.size).getBounds()
        left = self.x - width(self.text, self.kind, self.size) / 2
        pad = self.size * 0.3 if self.box else 0
        return left + b.left() - pad, self.y + b.top() - pad, left + b.right() + pad, self.y + b.bottom() + pad

    def draw(self, c, t):
        x, y, scale, alpha, blur = self.x, self.y, 1.0, 1.0, 0.0
        p = part(t, self.t0, 0.22)
        if self.move == "pop":
            scale, alpha = 0.7 + 0.3 * spring(p), min(1, p * 4)
        elif self.move == "rise":
            y, alpha = y + (1 - ease(p)) * 30, ease(p)
        elif self.move in ("left", "right"):
            x += (-1 if self.move == "left" else 1) * (1 - ease(p)) * 520
            alpha, blur = min(1, p * 4), (1 - ease(p)) * 30
        c.save()
        c.translate(x, y)
        c.rotate(self.tilt)
        c.scale(scale, scale)
        w, s, shape = width(self.text, self.kind, self.size), self.size, outline(self.text, self.kind, self.size)
        if self.box:
            box = skia.Rect.MakeLTRB(-w / 2 - s * 0.26, -s * 0.94, w / 2 + s * 0.26, s * 0.34)
            c.drawRoundRect(box, s * 0.08, s * 0.08, skia.Paint(AntiAlias=True, Color=colour(self.box, alpha)))
        c.translate(-w / 2, 0)
        if not self.box:
            for spread, strength in ((0.20, 0.5), (0.06, 0.5)):
                glow = skia.MaskFilter.MakeBlur(skia.kNormal_BlurStyle, max(3, s * spread))
                c.drawPath(shape, skia.Paint(AntiAlias=True, Color=colour(SHADOW, strength * alpha), MaskFilter=glow))
        paint = skia.Paint(AntiAlias=True, Color=colour(self.color, alpha))
        if blur > 0.5:
            paint.setImageFilter(skia.ImageFilters.Blur(blur, 0.01))
        c.drawPath(shape, paint)
        c.restore()


def to_image(rgba):
    return skia.Image.fromarray(
        np.ascontiguousarray(rgba), colorType=skia.kRGBA_8888_ColorType, alphaType=skia.kUnpremul_AlphaType, copy=True
    )


class Sprite(Element):
    """a photo, a clip (list of pictures, 12 per second) or an emoji"""

    def __init__(self, images, t0, t1, x, y, w, tilt=0, enter="pop", wiggle=1.0, behind=False, z=3, shadow=0.45):
        super().__init__(t0, t1, behind, z)
        self.images, self.x, self.y, self.w, self.tilt = images, x, y, w, tilt
        self.enter, self.wiggle, self.shadow = enter, wiggle, shadow
        self.path = []  # [(time, x, y, tilt)]: places it glides to later
        self.flip = False

    def place(self, t, glide=0.32):
        """where it is at t; glide=0 gives the place it is heading to"""
        x, y, tilt = self.x, self.y, self.tilt
        for at, nx, ny, ntilt in self.path:
            if t >= at - 1e-6:
                k = ease(part(t, at, glide)) if glide else 1.0
                x, y, tilt = x + (nx - x) * k, y + (ny - y) * k, tilt + (ntilt - tilt) * k
        return x, y, tilt

    def draw(self, c, t):
        life = t - self.t0
        image = self.images[int(life * 12 + 1e-6) % len(self.images)]
        (x, y, tilt), scale, alpha, blur = self.place(t), 1.0, 1.0, 0.0
        p = part(t, self.t0, 0.28)
        if self.enter == "pop":
            scale, alpha = (0.25 + 0.75 * spring(p)) if p < 1 else 1.0, min(1, p * 5)
        elif self.enter in ("left", "right"):
            side = -1 if self.enter == "left" else 1
            x, tilt, blur = x + side * (1 - ease(p)) * 640, tilt + side * (1 - ease(p)) * 9, (1 - ease(p)) * 30
        elif self.enter == "up":
            y, blur = y + (1 - ease(p)) * 520, (1 - ease(p)) * 24
        beat = math.floor(t * 12) / 12
        tilt += self.wiggle * 1.6 * math.sin(beat * 5.2 + self.x)
        y += self.wiggle * 4 * math.sin(beat * 3.7 + self.y)
        k = self.w / image.width() * scale
        c.save()
        c.translate(x, y)
        c.rotate(tilt)
        c.scale(-k if self.flip else k, k)
        paint = skia.Paint(AntiAlias=True, Alphaf=alpha)
        effect = (
            skia.ImageFilters.DropShadow(0, 10 / k, 16 / k, 16 / k, colour(SHADOW, self.shadow * alpha))
            if self.shadow
            else None
        )
        if blur > 0.5:
            effect = skia.ImageFilters.Blur(blur / k, 0.01, input=effect)
        if effect:
            paint.setImageFilter(effect)
        c.drawImageRect(
            image,
            skia.Rect.MakeXYWH(-image.width() / 2, -image.height() / 2, image.width(), image.height()),
            SMOOTH,
            paint,
        )
        c.restore()


class Shade(Element):
    """a soft dark layer so words stay readable over a full-screen picture"""

    def __init__(self, t0, t1, amount=0.35, z=5):
        super().__init__(t0, t1, False, z)
        self.amount = amount

    def draw(self, c, t):
        c.drawRect(skia.Rect.MakeWH(W, H), skia.Paint(Color=colour("#000000", self.amount)))


@functools.lru_cache(None)
def emoji(char):
    if not TYPE.get("emoji"):
        return None
    surface = skia.Surface(420, 420)
    c = surface.getCanvas()
    c.clear(0)
    c.drawTextBlob(skia.TextBlob(char, skia.Font(TYPE["emoji"], 200)), 80, 290, skia.Paint(AntiAlias=True))
    a = surface.makeImageSnapshot().toarray(colorType=skia.kRGBA_8888_ColorType)
    ys, xs = np.where(a[..., 3] > 8)
    return to_image(a[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]) if len(ys) else None


# ---------------------------------------------------------------- photo looks
def crop(im, w, h):
    """fills w x h, keeping the middle and a little more of the top (faces)"""
    ih, iw = im.shape[:2]
    k = max(w / iw, h / ih)
    im = cv2.resize(
        im,
        (max(w, math.ceil(iw * k)), max(h, math.ceil(ih * k))),
        interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_CUBIC,
    )
    y, x = int((im.shape[0] - h) * 0.35), (im.shape[1] - w) // 2
    return im[y : y + h, x : x + w]


def with_edge(im, edges, code):
    r, g, b = (int(code[i : i + 2], 16) for i in (1, 3, 5))
    return cv2.copyMakeBorder(im, *edges, cv2.BORDER_CONSTANT, value=(r, g, b, 255))


def sticker(rgba, code):
    """a cut-out with a paper edge"""
    rgba = cv2.copyMakeBorder(rgba, 24, 24, 24, 24, cv2.BORDER_CONSTANT, value=(0, 0, 0, 0))
    solid = (rgba[..., 3] > 110).astype(np.uint8) * 255
    edge = cv2.GaussianBlur(cv2.dilate(solid, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (19, 19))), (0, 0), 1.5)
    r, g, b = (int(code[i : i + 2], 16) for i in (1, 3, 5))
    a = rgba[..., 3:] / 255
    out = np.array([r, g, b]) * (1 - a) + rgba[..., :3] * a
    return np.dstack([out.astype(np.uint8), np.maximum(edge, rgba[..., 3])])


def torn(im, seed):
    h, w = im.shape[:2]
    rnd, points = np.random.default_rng(seed), []
    for x in np.linspace(0, w, 34):
        points.append((x, rnd.uniform(4, 22)))
    for y in np.linspace(0, h, 26):
        points.append((w - rnd.uniform(4, 22), y))
    for x in np.linspace(w, 0, 34):
        points.append((x, h - rnd.uniform(4, 22)))
    for y in np.linspace(h, 0, 26):
        points.append((rnd.uniform(4, 22), y))
    mask = np.zeros((h, w), np.uint8)
    cv2.fillPoly(mask, [np.array(points, np.int32)], 255)
    paper = cv2.GaussianBlur(mask, (0, 0), 1.0)
    inside = cv2.erode(mask, np.ones((9, 9), np.uint8))[..., None] / 255
    rgb = np.full_like(im[..., :3], 250) * (1 - inside) + im[..., :3] * inside
    return np.dstack([rgb.astype(np.uint8), paper])


def level(pics):
    """old films are often grey and dark: stretch to a clean black and white, then lift the middle, the same for every frame"""
    light = np.stack([cv2.resize(p[..., :3], (64, 48), interpolation=cv2.INTER_AREA).mean(2) for p in pics]) / 255
    lo, hi = np.percentile(light, 2), max(np.percentile(light, 98.5), 0.3)
    middle = float(np.clip((light - lo) / (hi - lo), 0.01, 1).mean())
    gamma = float(np.clip(np.log(0.42) / np.log(max(middle, 0.02)), 0.5, 1.1))
    table = (np.clip((np.arange(256) / 255 - lo) / (hi - lo), 0, 1) ** gamma * 245 + 5).astype(np.uint8)
    out = []
    for p in pics:
        q = p.copy()
        q[..., :3] = table[p[..., :3]]
        out.append(q)
    return out


def fade_low(im, amount):
    """darkens the lower part so words can sit on the picture"""
    rows = np.linspace(0, 1, im.shape[0])
    k = 1 - amount * np.clip((rows - 0.45) / 0.4, 0, 1)
    out = im.copy()
    out[..., :3] = (im[..., :3] * k[:, None, None]).astype(np.uint8)
    return out


def dress(im, look, w, h, edge, fade=0.0):
    """puts one RGBA picture into the chosen look"""
    if fade and look != "sticker":
        im = fade_low(crop(im, w, h), fade)
    if look == "full":
        return crop(im, W, H)
    if look == "polaroid":
        return with_edge(crop(im, w, h), (18, 64, 18, 18), "#FFFFFF")
    if look == "torn":
        return torn(crop(im, w, h), w + h)
    if look == "sticker" and (im[..., 3] < 250).mean() > 0.05:
        return sticker(im, edge)
    return with_edge(crop(im, w, h), (14, 14, 14, 14), edge)
