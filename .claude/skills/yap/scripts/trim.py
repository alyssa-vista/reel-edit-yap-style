"""trim.py: cut restarts, mistakes, ums and dead air out of a talking video, before anything is drawn on it.

  trim.py listen raw/video.mp4   ->  work/<name>/takes.txt  every word numbered, with pauses, possible restarts, ums and
                                                            sounds with no words marked
                                     work/<name>/cut.json   a first suggestion of what to keep (word numbers)
  trim.py cut work/<name>        ->  checks every cut, then writes work/<name>/trimmed.mov (1080x1920) and says what changed

Cuts are placed in the quiet between words, never on the transcript's word times, and every cut is measured.
The original video in raw is never changed. prep.py uses trimmed.mov by itself when it exists.
"""

import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from tools import FFMPEG, load, probe, readable, run, save

SR = 48000
HOP = 0.01  # one level reading every 10 ms
FILLERS = {"um", "umm", "uh", "uhh", "erm", "er", "hmm", "mm", "ah"}
MAYBE = {"like", "basically", "actually", "literally", "so", "okay"}
PACE = {  # from my-style.json "trim"
    "tight": dict(over=0.40, to=0.14),  # yap pace: no dead air
    "fast": dict(over=0.40, to=0.14, speed=1.15),  # tight, and the talking a little faster (pitch kept)
    "natural": dict(over=1.00, to=0.45),  # only long silences get shorter
}
NOT = {"not", "never", "no", "nothing", "nobody", "dont", "didnt", "doesnt", "isnt", "wasnt", "arent", "werent", "wont", "cant",
       "couldnt", "shouldnt", "wouldnt", "havent", "hasnt"}  # a line said again with one of these added or removed is on purpose
# the listener sometimes writes these in quiet moments when nobody said them
INVENTED = {"thanks for watching", "thank you for watching", "thank you", "thanks", "please subscribe", "subscribe",
            "like and subscribe", "see you next time", "see you in the next video", "bye"}


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
    cap = w["end"] + 0.28  # a breath after the word is not part of it (quiet camera sound can hide the pause)
    if q:
        a, b = q[0]
        return min(b, a + 0.07, cap)
    t = onset(lv, w["end"] + 0.02, hi) if nxt else None  # runs straight on: stop where the next word begins
    return min(t - 0.01 if t is not None else lv.dip(w["end"] - 0.02, min(hi, w["end"] + 0.2)), cap)


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


def hear(path, model, lang, names="Claude"):
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
            hotwords=names or None,
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


def lines_of(words):
    """the video split into lines: at a sentence end, or a pause of 0.45 s or more. (first word, last word)"""
    out, a = [], 0
    for w in words:
        n = w["i"] + 1
        if n == len(words) or w["word"].endswith((".", "?", "!")) or words[n]["start"] - w["end"] >= 0.45:
            out.append((a, w["i"]))
            a = n
    return out


def toks(words, a, b):
    """the words of a line, compared loosely ("someone's" = "someone")"""
    return [re.sub(r"'s$", "", t) for t in (norm(w["word"]) for w in words[a : b + 1]) if t]


def retakes(words, lines, gap=40.0):
    """an earlier take of a line that is said again soon after: {line number: the later line}. The last take is kept.
    A line repeated on purpose ("you're NOT unsettled... you're unsettled because...") changes its words, so it stays."""
    out = {}
    for x, (a, b) in enumerate(lines):
        ti = toks(words, a, b)
        if len(ti) < 2:
            continue
        for y in range(x + 1, len(lines)):
            c, d = lines[y]
            if words[c]["start"] - words[a]["start"] > gap:
                break
            tj = toks(words, c, d)
            if len(tj) < 2:
                continue
            if {t.replace("'", "") for t in ti} & NOT != {t.replace("'", "") for t in tj} & NOT:
                continue  # "it was NEVER supposed to make sense yet" -> "it was supposed to make sense later": on purpose
            r = difflib.SequenceMatcher(None, ti, tj[: len(ti) + 2], autojunk=False).ratio()
            complete = words[b]["word"].endswith((".", "?", "!")) and len(ti) >= 0.8 * len(tj)
            if complete:  # a finished line: a retake starts with (nearly) the same four words; a repeat on purpose changes them
                hit = (sum(p == q for p, q in zip(ti[:4], tj[:4])) >= 3 and r >= 0.5) or r >= 0.85
                quick = words[c]["start"] - words[b]["end"] < 10  # a short line said again right away, a word or two changed
                hit = hit or (quick and len(ti) <= 8 and ti[0] == tj[0] and ti[-1] == tj[-1] and r >= 0.6)
            else:  # a line broken off: the same start is enough
                hit = (ti[:2] == tj[:2] and r >= 0.5) or r >= 0.72
            if hit:
                out[x] = y
                break
    return out


def stumble(words, lines, x):
    """a short line the listener could not make out (a word broken off half way), with more talking right after"""
    a, b = lines[x]
    ws = words[a : b + 1]
    more = x + 1 < len(lines) and words[lines[x + 1][0]]["start"] - ws[-1]["end"] < 20
    unsure = sum(w.get("sure", 1.0) for w in ws) / len(ws) < 0.6 or min(w.get("sure", 1.0) for w in ws) < 0.35
    return len(ws) <= 4 and unsure and more


def invented(words, lines, x):
    """'Thanks for watching!' and friends, written down in a quiet moment"""
    a, b = lines[x]
    ws = words[a : b + 1]
    return " ".join(toks(words, a, b)).replace("'", "") in INVENTED and min(w.get("sure", 1.0) for w in ws) < 0.85


def abandoned(words, i, j, k):
    """True when the first take was dropped half way: the second starts right after it (at most 3 words between,
    none of them ending a sentence). A line said twice on purpose ("you're not unsettled because... you're
    unsettled because...") finishes its sentence first, so it is only pointed out, never cut."""
    between = words[i + k : j]
    return len(between) <= 3 and not any(w["word"].endswith((".", "?", "!")) for w in words[i : j])


def suggest(words, again, pace, stumbles):
    drop = set()
    why = {}
    lines = lines_of(words)
    later = retakes(words, lines)
    for x, (a, b) in enumerate(lines):
        reason = ("retake: said again at word %d" % lines[later[x]][0]) if x in later else "stumble" if stumble(words, lines, x) else \
            "probably not said: the listener invents this in quiet moments" if invented(words, lines, x) else None
        if reason:
            drop.update(range(a, b + 1))
            why[f"{a}-{b}"] = reason
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
    return dict(keep=keep, pace=pace, speed=PACE.get(pace, {}).get("speed", 1.0), pause_after=[], drop_sounds=[list(r) for r in stumbles], why=why)


def listen(video, model, lang, style, names="Claude"):
    name = os.path.splitext(os.path.basename(video))[0]
    work = os.path.join("work", name)
    os.makedirs(work, exist_ok=True)
    raw_video = os.path.abspath(video)
    video = readable(video, work)
    save(os.path.join(work, "source.json"), dict(video=os.path.abspath(video), raw=raw_video))
    info = probe(video)
    if not info["sound"]:
        sys.exit("This video has no sound, so there are no words to follow.")
    pace = (load(style).get("trim") if os.path.isfile(style) else None) or "tight"
    pace = pace if pace in PACE else "tight"
    print("listening to every word", flush=True)
    x, lv, words = hear(video, model, lang, names)
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
        tag = "*" if norm(w["word"]) in FILLERS else "?" if w.get("sure", 1.0) < 0.5 else ""
        if w["i"] in starts:
            line.append(f"<<AGAIN(from {starts[w['i']]})")
        line.append(f"{w['i']}:{w['word']}{tag}")
        if w["word"].endswith((".", "?", "!")) or w["i"] in pauses or w["i"] == len(words) - 1:
            first = next(x for x in line if not x.startswith("<<")).split(":")[0]
            mark = next((f"   [CUT: {r}]" for k, r in cut["why"].items() if "-" in k and int(k.split("-")[0]) <= int(first) <= int(k.split("-")[1])), "")
            lines.append(f"[{words[int(first)]['start']:6.2f}] " + " ".join(line) + mark)
            line = []
            if w["i"] in pauses:
                lines.append(f"         ~ pause {pauses[w['i']]:.1f} s")
    total = words[-1]["end"] - words[0]["start"]
    est = sum(b - a for a, b in spans(Levels(x), words, cut)) / float(cut.get("speed", 1.0) or 1.0)  # measured, not guessed
    head_ = [
        f"{name}: {info['seconds']:.1f} s, {len(words)} words. Pace: {pace}.",
        "Every word has a number. * = um or uh. ? = the listener was not sure of that word. <<AGAIN = the same words were said before.",
        "[CUT: ...] = suggested to cut, and why. A retake keeps the LAST take of a line: if an earlier take was better, keep that one instead.",
        f"Suggested keep (cut.json): {cut['keep']}",
        f"That cut is {est:.0f} s long (from {total:.0f} s of talking), pauses shortened" + (f", at {cut['speed']}x speed." if cut.get("speed", 1) != 1 else "."),
        "",
    ]
    cut_lines = [(k, r) for k, r in cut["why"].items() if "-" in k and not r.startswith("restart")]
    if cut_lines:
        head_.append("Lines suggested to cut:")
        head_ += [f'  words {k}: {r}: "{" ".join(w["word"] for w in words[int(k.split("-")[0]) : int(k.split("-")[1]) + 1])}"' for k, r in cut_lines]
    gone = set().union(*[set(range(int(k.split("-")[0]), int(k.split("-")[1]) + 1)) for k in cut["why"] if "-" in k] or [set()])
    twice = [(i, j, k) for i, j, k in again if f"{i}-{j - 1}" not in cut["why"] and i not in gone and j not in gone]
    if twice:
        head_.append("Said twice inside one line (kept: on purpose?):")
        head_ += [f'  {i}-{i + k - 1} and {j}-{j + k - 1}: "{" ".join(w["word"] for w in words[i : i + k])}"' for i, j, k in twice]
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
    sj = load(os.path.join(work, "source.json"))
    src = sj["video"]
    if not os.path.isfile(src):  # the rewrapped copy was removed after an earlier cut: make it again
        src = readable(sj.get("raw", src), work)
    info = probe(src)
    words, plan = load(os.path.join(work, "source_words.json")), load(os.path.join(work, "cut.json"))
    x = audio(src)
    lv = Levels(x)
    sp = spans(lv, words, plan)
    bad, soft = check(Levels(x), sp, words)  # measured on the real sound, not the one with dropped sounds marked quiet
    kept_i = {i for a, b in plan["keep"] for i in range(a, b + 1)}
    inside = lambda w: any(a <= (w["start"] + w["end"]) / 2 <= b for a, b in sp)
    bad += [f'word {w["i"]} "{w["word"]}" is kept but falls outside every piece' for w in words if w["i"] in kept_i and not inside(w)]
    bad += [f'word {w["i"]} "{w["word"]}" is cut but its sound is still inside a piece' for w in words if w["i"] not in kept_i and inside(w)]
    for b in bad:
        print("CHECK:", b)
    for b in soft:
        print("note:", b, "(a breath, or no pause there: fine)")
    if bad and not force:
        sys.exit("Fix the lines above in cut.json (or run again with --anyway), then cut again.")
    fps = min((24, 25, 30, 50, 60), key=lambda f: abs(f - info["fps"]))
    info["fps"] = fps
    speed = float(plan.get("speed", 1.0) or 1.0)
    fade = np.linspace(0, 1, int(0.008 * SR), dtype=np.float32)
    voice, jobs, t, f_out, joins = [], [], 0.0, 0, []
    for a, b in sp:
        y = x[int(round(a * SR)) : int(round(b * SR))].copy()
        y[: len(fade)] *= fade
        y[-len(fade) :] *= fade[::-1]
        voice.append(y)
        if t:
            joins.append(round(t / speed, 3))
        t += len(y) / SR
        n = int(round(t / speed * fps)) - f_out  # output frames for this piece (after any speed-up), so sound and lips never drift
        jobs.append((int(round(a * fps)), n))
        f_out += n
    tmp = os.path.join(work, "pieces")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    vf = ",".join(picture(info) + ([f"setpts=PTS/{speed}", f"fps={fps}"] if speed != 1 else [])
                  + ["scale=1080:1920:force_original_aspect_ratio=increase", "crop=1080:1920", "setsar=1", "format=yuv420p",
                     "tpad=stop_mode=clone:stop=6"])
    workers = max(1, min(4, (os.cpu_count() or 2) // 2))
    enc = ["-c:v", "libx264", "-preset", "fast", "-crf", "12", "-r", str(fps), "-g", str(fps // 2), "-color_primaries", "bt709",
           "-color_trc", "bt709", "-colorspace", "bt709", "-threads", str(max(2, (os.cpu_count() or 4) // workers))]

    def piece(k):  # jump straight to the piece: only the kept parts are decoded
        f0, n = jobs[k]
        run([FFMPEG, "-v", "error", "-y", "-ss", f"{f0 / fps:.5f}", "-i", src, "-an", "-vf", vf, "-frames:v", str(n), *enc,
             os.path.join(tmp, f"{k:04d}.mp4")])

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(piece, range(len(jobs))))
    with open(os.path.join(tmp, "list.txt"), "w") as f:
        f.write("".join(f"file '{k:04d}.mp4'\n" for k in range(len(jobs))))
    pcm = np.concatenate(voice)
    with open(os.path.join(tmp, "voice.f32"), "wb") as f:
        f.write(pcm.astype("<f4").tobytes())
    out = os.path.join(work, "trimmed.mov")
    run([FFMPEG, "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", os.path.join(tmp, "list.txt"), "-f", "f32le", "-ar", str(SR),
         "-ac", "1", "-i", os.path.join(tmp, "voice.f32"), "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
         *(["-af", f"atempo={speed}"] if speed != 1 else []), "-c:a", "pcm_s24le", "-frames:v", str(f_out), out])  # faster talking keeps its pitch
    shutil.rmtree(tmp, ignore_errors=True)
    kept = [w for a, b in plan["keep"] for w in words[a : b + 1]]
    with open(os.path.join(work, "expected.txt"), "w", encoding="utf-8") as f:
        f.write(" ".join(w["word"] for w in kept) + "\n")
    gone = [w for w in words if not any(a <= w["i"] <= b for a, b in plan["keep"])]
    before, after = info["seconds"], len(pcm) / SR / speed
    save(os.path.join(work, "trim.json"), dict(spans=sp, joins=joins, speed=speed, seconds_before=round(before, 2), seconds_after=round(after, 2)))
    if os.path.basename(src) == "source.mov" and os.path.dirname(os.path.abspath(src)) == os.path.abspath(work):
        os.remove(src)  # the rewrapped camera file is as big as the original: free the space (made again if needed)
        print("removed the rewrapped copy to save space")
    print(json.dumps(dict(trimmed=out, before=round(before, 1), after=round(after, 1), speed=speed, pieces=len(sp),
                          words_cut=" ".join(w["word"] for w in gone)[:400])))


if __name__ == "__main__":
    a = argparse.ArgumentParser()
    a.add_argument("what", choices=["listen", "cut"])
    a.add_argument("path", help="listen: the raw video. cut: the work folder")
    a.add_argument("--model", default="small.en")
    a.add_argument("--lang", default="en")
    a.add_argument("--style", default="my-style.json")
    a.add_argument("--names", default="Claude", help="names the listener should expect, comma separated")
    a.add_argument("--anyway", action="store_true", help="cut even when a check failed")
    a = a.parse_args()
    if a.what == "listen":
        listen(a.path, a.model, a.lang, a.style, a.names)
    else:
        cut(a.path.rstrip("/"), a.anyway)
