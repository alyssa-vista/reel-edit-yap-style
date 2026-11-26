"""trim.py: cut restarts, mistakes, ums and dead air out of a talking video, before anything is drawn on it.

  trim.py listen raw/video.mp4   ->  work/<name>/takes.txt  every word numbered, with pauses, possible restarts, ums and
                                                            sounds with no words marked
                                     work/<name>/cut.json   a first suggestion of what to keep (word numbers)
  trim.py cut work/<name>        ->  checks every cut, then writes work/<name>/trimmed.mov (1080x1920) and says what changed

Cuts are placed in the quiet between words, never on the transcript's word times, and every cut is measured.
The original video in raw is never changed. prep.py uses trimmed.mov by itself when it exists.
"""

import argparse
import json
import os
import re
import sys

import numpy as np

from tools import FFMPEG, load, probe, run, save

SR = 48000
HOP = 0.01  # one level reading every 10 ms
FILLERS = {"um", "umm", "uh", "uhh", "erm", "er", "hmm", "mm", "ah"}
MAYBE = {"like", "basically", "actually", "literally", "so", "okay"}
PACE = {  # from my-style.json "trim"
    "tight": dict(over=0.40, to=0.14),  # yap pace: no dead air
    "natural": dict(over=1.00, to=0.45),  # only long silences get shorter
}


def norm(w):
    return re.sub(r"[^a-z0-9']", "", w.lower())


# ---------------------------------------------------------------- sound


class Levels:
    """Loudness (dBFS) and the energy above 4 kHz, every 10 ms. A soft 's' or 'f' is not silence."""

    def __init__(self, x, sr=SR):
        hop, win = int(sr * HOP), int(sr * HOP) * 2
        n = len(x) // hop
        pad = np.pad(x.astype(np.float32), (hop // 2, win))
        hz = np.fft.rfftfreq(win, 1 / sr)
        han = np.hanning(win).astype(np.float32)
        e, h = np.empty(n, np.float32), np.empty(n, np.float32)
        for a in range(0, n, 2000):
            k = np.arange(a, min(n, a + 2000))
            fr = pad[k[:, None] * hop + np.arange(win)[None, :]]
            e[k] = 10 * np.log10(np.mean(fr[:, hop // 2 : hop // 2 + hop] ** 2, 1) + 1e-12)
            sp = np.abs(np.fft.rfft(fr * han, axis=1)) ** 2
            h[k] = 10 * np.log10(sp[:, hz > 4000].sum(1) / win + 1e-12)
        self.e, self.h, self.n = e, h, n
        self.speech = float(np.percentile(e, 80))
        self.limit = min(-35.0, self.speech - 16)  # a cut is clean below this
        self.quiet = (e < self.speech - 24) & (h < float(np.percentile(h, 80)) - 20)

    def at(self, t):
        k = int(round(t / HOP))
        return float(self.e[max(0, k - 1) : max(1, k + 2)].mean())

    def runs(self, a=0.0, b=None, least=0.06):
        """quiet stretches inside [a, b] (seconds)"""
        q, out = self.quiet, []
        i, j1 = max(0, int(a / HOP)), min(self.n, int((b if b is not None else self.n * HOP) / HOP))
        while i < j1:
            if q[i]:
                j = i
                while j < j1 and q[j]:
                    j += 1
                if (j - i) * HOP >= least:
                    out.append((i * HOP, j * HOP))
                i = j
            else:
                i += 1
        return out

    def dip(self, a, b):
        i, j = max(0, int(a / HOP)), min(self.n, int(b / HOP) + 1)
        return (i + int(np.argmin(self.e[i:j]))) * HOP if j > i else a


def onset(lv, a, b):
    """the last place in [a, b] where a word starts: the sound jumps up by 6 dB or more in 10 ms"""
    i, j = max(1, int(a / HOP)), min(lv.n - 1, int(b / HOP))
    for k in range(j, i, -1):
        if lv.e[k] - lv.e[k - 1] >= 6 and lv.e[k] > lv.speech - 14:
            return k * HOP
    return None


def head(lv, words, i):
    """where to start: just before the first sound of word i, never inside the word before it"""
    w, prev = words[i], words[i - 1] if i else None
    lo = max(w["start"] - 0.55, prev["end"] if prev else 0.0)
    q = [r for r in lv.runs(lo, w["start"] + 0.06) if r[0] < w["start"] + 0.04]
    if q:
        a, b = q[-1]
        return max(a, b - 0.06)
    t = onset(lv, max(lo, w["start"] - 0.3), w["start"] + 0.05)  # runs straight on from the word before
    return t - 0.01 if t is not None else lv.dip(w["start"] - 0.15, w["start"] + 0.02)


def tail(lv, words, i):
    """where to stop: after the last sound of word i (soft endings like s, f, n run on past the transcript),
    and before the next word starts"""
    w, nxt = words[i], words[i + 1] if i + 1 < len(words) else None
    hi = min(w["end"] + 0.55, nxt["start"] + 0.03 if nxt else 1e9)
    q = [r for r in lv.runs(w["end"] - 0.06, hi) if r[1] > w["end"] - 0.04]
    if q:
        a, b = q[0]
        return min(b, a + 0.07)
    t = onset(lv, w["end"] + 0.02, hi) if nxt else None  # runs straight on: stop where the next word begins
    return t - 0.01 if t is not None else lv.dip(w["end"] - 0.02, min(hi, w["end"] + 0.2))


# ---------------------------------------------------------------- listen


def islands(lv, gap=0.35, pad=0.12):
    """stretches of talking with no pause of `gap` inside. Each one is written down on its own, because the listener
    tidies a restart away when it hears both takes together."""
    edges = [0.0] + [t for r in lv.runs(0, None, gap) for t in r] + [lv.n * HOP]
    end = lv.n * HOP
    return [
        (max(0.0, a - pad), min(end, b + pad))
        for a, b in zip(edges[0::2], edges[1::2])
        if b - a >= 0.15 and (lv.e[int(a / HOP) : int(b / HOP) + 1] > lv.speech - 20).any()
    ]


def hear(path, model, lang):
    from faster_whisper import WhisperModel

    x, x16 = audio(path), audio(path, 16000)
    lv = Levels(x)
    whisper = WhisperModel(model, device="cpu", compute_type="int8")
    words = []
    for a, b in islands(lv):
        segs, _ = whisper.transcribe(
            x16[int(a * 16000) : int(b * 16000)],
            language=lang,
            word_timestamps=True,
            condition_on_previous_text=False,
        )
        words += [
            dict(i=0, word=w.word.strip(), start=round(a + w.start, 3), end=round(a + w.end, 3), sure=round(w.probability, 2))
            for s in segs
            for w in s.words
            if w.word.strip()
        ]
    for n, w in enumerate(words):
        w["i"] = n
    return x, lv, words


def audio(path, rate=SR):
    raw = run([FFMPEG, "-v", "error", "-i", path, "-map", "0:a:0", "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"])
    return np.frombuffer(raw.stdout, np.float32)


def restarts(words, gap=25.0):
    """the same words said again soon after: (first, second, how many words). Short repeats must be close together."""
    n, out, skip = [norm(w["word"]) for w in words], [], -1
    for i in range(len(n)):
        if i <= skip or not n[i]:
            continue
        for j in range(i + 1, len(n)):
            dt = words[j]["start"] - words[i]["start"]
            if dt > gap:
                break
            k = 0
            while j + k < len(n) and i + k < j and n[i + k] == n[j + k] and n[i + k]:
                k += 1
            if k >= 3 or (k >= 2 and dt < 8) or (k >= 1 and j == i + k and dt < 2.5):  # "I felt so... I felt so lost", "I- I"
                out.append((i, j, k))
                skip = j - 1
                break
    return out


def wordless(lv, words):
    """speech-loud sound where the transcript has no word: often a stumble it tidied away"""
    covered = np.zeros(lv.n, bool)
    for w in words:
        covered[max(0, int((w["start"] - 0.12) / HOP)) : int((w["end"] + 0.12) / HOP)] = True
    loud = (lv.e > lv.speech - 14) & ~covered
    out, i = [], 0
    while i < lv.n:
        if loud[i]:
            j = i
            while j < lv.n and (loud[j] or (j + 5 < lv.n and loud[j : j + 5].any())):
                j += 1
            if (j - i) * HOP >= 0.3:
                out.append((round(i * HOP, 2), round(j * HOP, 2)))
            i = j
        else:
            i += 1
    return out


def abandoned(words, i, j, k):
    """True when the first take was dropped half way: the second starts right after it (at most 3 words between,
    none of them ending a sentence). A line said twice on purpose ("you're not unsettled because... you're
    unsettled because...") finishes its sentence first, so it is only pointed out, never cut."""
    between = words[i + k : j]
    return len(between) <= 3 and not any(w["word"].endswith((".", "?", "!")) for w in words[i : j])


def suggest(words, again, pace, stumbles):
    drop = set()
    why = {}
    for i, j, k in again:
        if abandoned(words, i, j, k):  # keep the last take
            drop.update(range(i, j))
            why[f"{i}-{j - 1}"] = "restart: said again at word %d" % j
    for w in words:
        if norm(w["word"]) in FILLERS:
            drop.add(w["i"])
            why[str(w["i"])] = "um"
    keep, cur = [], None
    for w in words:
        if w["i"] in drop:
            cur = None
            continue
        if cur is None:
            cur = [w["i"], w["i"]]
            keep.append(cur)
        else:
            cur[1] = w["i"]
    return dict(keep=keep, pace=pace, pause_after=[], drop_sounds=[list(r) for r in stumbles], why=why)


def listen(video, model, lang, style):
    name = os.path.splitext(os.path.basename(video))[0]
    work = os.path.join("work", name)
    os.makedirs(work, exist_ok=True)
    info = probe(video)
    if not info["sound"]:
        sys.exit("This video has no sound, so there are no words to follow.")
    pace = (load(style).get("trim") if os.path.isfile(style) else None) or "tight"
    pace = pace if pace in PACE else "tight"
    print("listening to every word", flush=True)
    x, lv, words = hear(video, model, lang)
    if not words:
        sys.exit("No words were heard in this video.")
    save(os.path.join(work, "source_words.json"), words)
    again, stumbles = restarts(words), wordless(lv, words)
    again = [(i, j, k) for i, j, k in again if k >= 3 or abandoned(words, i, j, k)]  # two common words twice is not news
    cut = suggest(words, again, pace, stumbles)
    save(os.path.join(work, "cut.json"), cut)

    pauses = {}
    for a, b in lv.runs(words[0]["start"], words[-1]["end"], 0.6):
        after = max((w for w in words if w["start"] < a), key=lambda w: w["start"], default=None)
        if after:
            pauses[after["i"]] = b - a
    starts = {j: i for i, j, k in again}
    lines, line = [], []
    for w in words:
        tag = "*" if norm(w["word"]) in FILLERS else "?" if w["sure"] < 0.5 else ""
        if w["i"] in starts:
            line.append(f"<<AGAIN(from {starts[w['i']]})")
        line.append(f"{w['i']}:{w['word']}{tag}")
        if w["word"].endswith((".", "?", "!")) or w["i"] in pauses or w["i"] == len(words) - 1:
            first = next(x for x in line if not x.startswith("<<")).split(":")[0]
            lines.append(f"[{words[int(first)]['start']:6.2f}] " + " ".join(line))
            line = []
            if w["i"] in pauses:
                lines.append(f"         ~ pause {pauses[w['i']]:.1f} s")
    total = words[-1]["end"] - words[0]["start"]
    kept = sum(words[b]["end"] - words[a]["start"] for a, b in cut["keep"])
    dead = sum(max(0, d - PACE[pace]["to"]) for d in pauses.values())
    head_ = [
        f"{name}: {info['seconds']:.1f} s, {len(words)} words. Pace: {pace}.",
        "Every word has a number. * = um or uh. ? = the listener was not sure of that word. <<AGAIN = the same words were said before.",
        f"Suggested keep (cut.json): {cut['keep']}",
        f"That is about {kept - dead:.0f} s once pauses are shortened (from {total:.0f} s of talking).",
        "",
    ]
    if again:
        head_.append("Possible restarts (same words said again):")
        head_ += [
            f'  {i}-{i + k - 1} and {j}-{j + k - 1}: "{" ".join(w["word"] for w in words[i : i + k])}"'
            + (" (suggested: keep the second)" if f"{i}-{j - 1}" in cut["why"] else " (a full sentence first: on purpose? kept)")
            for i, j, k in again
        ]
    if stumbles:
        head_.append("Sound with no words: an um, a stumble or a laugh the listener skipped. All are in drop_sounds (remove one to keep it):")
        for a, b in stumbles:
            near = max((w["i"] for w in words if w["start"] < a), default=0)
            head_.append(f"  {a:.2f}-{b:.2f} s, after word {near}")
    maybes = [f"{w['i']}:{w['word']}" for w in words if norm(w["word"]) in MAYBE]
    if maybes:
        head_.append("Words that are sometimes filler (cut only if the sentence still works): " + ", ".join(maybes))
    with open(os.path.join(work, "takes.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(head_ + ["", *lines]) + "\n")
    print(json.dumps(dict(work=work, words=len(words), restarts=len(again), ums=sum(1 for v in cut["why"].values() if v == "um"),
                          wordless=len(stumbles), seconds=round(info["seconds"], 1))))


# ---------------------------------------------------------------- cut


def spans(lv, words, cut):
    """keep ranges (word numbers) -> source time spans, with long pauses inside them shortened"""
    p = PACE.get(cut.get("pace"), PACE["tight"]) if isinstance(cut.get("pace"), str) else cut["pace"]
    over, to = float(p["over"]), float(p["to"])
    keep_after = set(cut.get("pause_after", []))
    drops = [tuple(d) for d in cut.get("drop_sounds", [])]
    for d0, d1 in drops:  # a dropped sound counts as quiet, so it goes with the pause around it
        lv.quiet[int(d0 / HOP) : int(d1 / HOP) + 1] = True
    out, last = [], -1
    for a, b in cut["keep"]:
        assert 0 <= a <= b < len(words), f"keep {a}-{b}: there are words 0-{len(words) - 1}"
        assert a > last, f"keep {a}-{b} overlaps or goes back in time"
        last = b
        s, e = head(lv, words, a), tail(lv, words, b)
        pieces, t0 = [], s
        for q0, q1 in lv.runs(s + 0.03, e - 0.03, min(over, 0.2)):
            dropped = any(d0 < q1 and d1 > q0 for d0, d1 in drops)
            before = max((w["i"] for w in words[a : b + 1] if w["start"] < q0), default=a)
            if not dropped and (q1 - q0 < over or before in keep_after):
                continue
            pieces.append((t0, q0 + to / 2))
            t0 = q1 - to / 2
        pieces.append((t0, e))
        out += [list(map(lambda v: round(v, 3), x)) for x in pieces]
    merged = []
    for a, b in out:  # two ranges that touch in time play straight on
        if merged and a - merged[-1][1] < 0.02:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return merged


def check(lv, sp, words):
    """a cut inside a word is a fault. A cut in a breath, or where the speaker ran straight on, is only noted."""
    bad, soft = [], []
    for k, (a, b) in enumerate(sp):
        if b - a < 0.2:
            bad.append(f"piece {k} is only {b - a:.2f} s long: it will flicker")
        for side, t in (("start", a), ("end", b)):
            if lv.at(t) <= lv.limit:
                continue
            inside = next((w for w in words if w["start"] + 0.03 < t < w["end"] - 0.03), None)
            (bad if inside else soft).append(
                f"piece {k} {side} at {t:.2f} s"
                + (f' is inside "{inside["word"]}" (word {inside["i"]})' if inside else f" is not silent ({lv.at(t):.0f} dB)")
            )
    return bad, soft


def picture(info):
    steps = [f"fps={info['fps']:g}"]
    if info.get("hdr"):
        steps.append("zscale=t=linear:npl=203,format=gbrpf32le,tonemap=mobius,zscale=p=bt709:t=bt709:m=bt709:r=tv")
    return steps


def cut(work, force=False):
    info = probe(load(os.path.join(work, "source.json"))["video"])
    src = load(os.path.join(work, "source.json"))["video"]
    words, plan = load(os.path.join(work, "source_words.json")), load(os.path.join(work, "cut.json"))
    x = audio(src)
    lv = Levels(x)
    sp = spans(lv, words, plan)
    bad, soft = check(Levels(x), sp, words)  # measured on the real sound, not the one with dropped sounds marked quiet
    for b in bad:
        print("CHECK:", b)
    for b in soft:
        print("note:", b, "(a breath, or no pause there: fine)")
    if bad and not force:
        sys.exit("Fix the lines above in cut.json (or run again with --anyway), then cut again.")
    fps = min((24, 25, 30, 50, 60), key=lambda f: abs(f - info["fps"]))
    info["fps"] = fps
    fade = np.linspace(0, 1, int(0.008 * SR), dtype=np.float32)
    voice, sel, t, f_out = [], [], 0.0, 0
    for a, b in sp:
        y = x[int(round(a * SR)) : int(round(b * SR))].copy()
        y[: len(fade)] *= fade
        y[-len(fade) :] *= fade[::-1]
        voice.append(y)
        t += len(y) / SR
        n = int(round(t * fps)) - f_out  # picture frames for this piece, so sound and lips never drift
        f0 = int(round(a * fps))
        sel.append(f"between(n,{f0},{f0 + n - 1})")
        f_out += n
    wav = os.path.join(work, "trimmed.wav")
    pcm = np.concatenate(voice)
    with open(wav + ".f32", "wb") as f:
        f.write(pcm.astype("<f4").tobytes())
    vf = ",".join(
        picture(info)
        + [
            f"select='{'+'.join(sel)}'",
            f"setpts=N/({fps}*TB)",
            "scale=1080:1920:force_original_aspect_ratio=increase",
            "crop=1080:1920",
            "setsar=1",
            "format=yuv420p",
        ]
    )
    with open(os.path.join(work, "trim.filter"), "w") as f:
        f.write(vf)
    out = os.path.join(work, "trimmed.mov")
    run(
        [FFMPEG, "-v", "error", "-y", "-i", src, "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", wav + ".f32",
         "-filter_script:v", os.path.join(work, "trim.filter"), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264",
         "-preset", "fast", "-crf", "12", "-r", str(fps), "-g", str(fps // 2), "-color_primaries", "bt709", "-color_trc", "bt709",
         "-colorspace", "bt709", "-c:a", "pcm_s24le", "-frames:v", str(f_out), out]
    )
    os.remove(wav + ".f32")
    os.remove(os.path.join(work, "trim.filter"))
    kept = [w for a, b in plan["keep"] for w in words[a : b + 1]]
    with open(os.path.join(work, "expected.txt"), "w", encoding="utf-8") as f:
        f.write(" ".join(w["word"] for w in kept) + "\n")
    gone = [w for w in words if not any(a <= w["i"] <= b for a, b in plan["keep"])]
    before, after = info["seconds"], len(pcm) / SR
    save(os.path.join(work, "trim.json"), dict(spans=sp, seconds_before=round(before, 2), seconds_after=round(after, 2)))
    print(json.dumps(dict(trimmed=out, before=round(before, 1), after=round(after, 1), pieces=len(sp),
                          words_cut=" ".join(w["word"] for w in gone)[:400])))


if __name__ == "__main__":
    a = argparse.ArgumentParser()
    a.add_argument("what", choices=["listen", "cut"])
    a.add_argument("path", help="listen: the raw video. cut: the work folder")
    a.add_argument("--model", default="small.en")
    a.add_argument("--lang", default="en")
    a.add_argument("--style", default="my-style.json")
    a.add_argument("--anyway", action="store_true", help="cut even when a check failed")
    a = a.parse_args()
    if a.what == "listen":
        listen(a.path, a.model, a.lang, a.style)
        save(os.path.join("work", os.path.splitext(os.path.basename(a.path))[0], "source.json"), dict(video=os.path.abspath(a.path)))
    else:
        cut(a.path.rstrip("/"), a.anyway)
