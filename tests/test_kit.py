import http.client, json, os, re, subprocess, sys, tempfile, unittest
from urllib.parse import urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
K = os.path.join(ROOT, ".claude", "skills", "yap")


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def load(*p):
    return json.loads(read(os.path.join(K, *p)))


GOOD = dict(
    trim=["tight"],
    palette=["#F1C92F", "#FFF4C1", "#FFFFFF"],
    font=["Poppins"],
    accent=["Caveat"],
    photos=["cinematic", "ink"],
    photoLook=["frame"],
    gifs=["none"],
    emoji=["few"],
    captions=["all-fast"],
    behind=["some"],
    busy=["medium"],
    sound=["yes"],
)


class Lists(unittest.TestCase):

    def test_unique_ids(self):
        for k, v in load("styles.json").items():
            self.assertEqual(len({x["id"] for x in v}), len(v), k)

    def test_every_style_has_a_tool(self):
        for v in load("styles.json").values():
            for x in v:
                self.assertTrue(x.get("tool"), x["id"])

    def test_every_style_has_a_source(self):
        src = read(os.path.join(K, "scripts", "find.py"))
        for v in load("styles.json").values():
            for x in v:
                self.assertIn(f"{x['src']}=lambda", src, x["id"])

    def test_every_survey_card_has_a_picture_or_icon(self):
        for k, v in load("styles.json").items():
            for x in v:
                self.assertTrue(
                    x.get("icon") or os.path.isfile(os.path.join(K, "survey", "img", f"{k}_{x['id']}.jpg")),
                    f"{k} {x['id']}",
                )

    def test_no_private_text(self):
        bad = re.compile("/Users/[a-z]+|C:\\\\\\\\Users|@gmail\\.com|BEGIN [A-Z ]*PRIVATE KEY", re.I)
        for d, dirs, fs in os.walk(ROOT):
            dirs[:] = [x for x in dirs if x not in (".git", "raw", "final", "work", "__pycache__")]
            for f in fs:
                if (
                    f.endswith((".py", ".md", ".json", ".js", ".css", ".html", ".yml", ".txt", ".swift"))
                    and f != "test_kit.py"
                ):
                    self.assertIsNone(bad.search(read(os.path.join(d, f))), f)


class Setup(unittest.TestCase):
    def test_python_choice(self):
        sys.path.insert(0, os.path.join(K, "scripts"))
        import setup

        for v in ((3, 11), (3, 13), (3, 14)):
            self.assertTrue(setup.fits(v))
        for v in ((3, 9), (3, 10), (3, 15), (4, 0)):  # too old or too new: setup brings its own
            self.assertFalse(setup.fits(v))

    def test_pinned(self):
        for line in read(os.path.join(ROOT, "requirements.txt")).split():
            self.assertRegex(line, r"^[\w.-]+==[\w.]+$")


class Survey(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.tmp.name, "my-style.json")
        self.p = subprocess.Popen(
            [
                sys.executable,
                os.path.join(K, "scripts", "survey.py"),
                "--no-open",
                "--out",
                self.out,
                "--minutes",
                "0.5",
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        self.u = urlsplit(self.p.stdout.readline().split()[1])

    def tearDown(self):
        self.p.kill()
        self.p.wait()
        self.p.stdout.close()
        self.tmp.cleanup()

    def ask(self, method, path, body=None, **h):
        c = http.client.HTTPConnection("127.0.0.1", self.u.port, timeout=10)
        c.request(method, path, body, h)
        r = c.getresponse()
        d = r.read()
        c.close()
        return (r.status, d)

    def save(self, d, origin=None):
        return self.ask(
            "POST", self.u.path + "save", json.dumps(d), Origin=origin or f"http://127.0.0.1:{self.u.port}"
        )[0]

    def test_page_needs_the_token(self):
        self.assertEqual(self.ask("GET", "/")[0], 404)
        self.assertEqual(self.ask("GET", self.u.path)[0], 200)

    def test_other_host_is_refused(self):
        self.assertEqual(self.ask("GET", self.u.path, Host="evil.example")[0], 404)

    def test_other_site_cannot_save(self):
        self.assertEqual(self.save(GOOD, "https://evil.example"), 403)
        self.assertFalse(os.path.exists(self.out))

    def test_no_path_escape(self):
        for p in ("img/../../styles.json", "../styles.json", "img/%2e%2e/x.jpg", "questions.json"):
            self.assertEqual(self.ask("GET", self.u.path + p)[0], 404, p)

    def test_bad_answers(self):
        for k, v in (
            ("font", ['Evil"; rm']),
            ("photos", ["cinematic"] * 9),
            ("gifs", ["none", "dance"]),
            ("palette", ["red", "#fff", "x"]),
            ("emoji", []),
        ):
            self.assertEqual(self.save({**GOOD, k: v}), 400, k)
        self.assertEqual(self.save([1]), 400)
        self.assertFalse(os.path.exists(self.out))

    def test_brand_kit(self):
        o = dict(Origin=f"http://127.0.0.1:{self.u.port}")
        self.assertEqual(self.ask("POST", self.u.path + "kit", b"#!/bin/sh evil", **o)[0], 415)
        self.assertEqual(self.ask("POST", self.u.path + "kit", b"%PDF-1.7 x", Origin="https://evil.example")[0], 403)
        self.assertEqual(self.ask("POST", self.u.path + "kit", b"%PDF-1.7 x", **o)[0], 200)
        self.assertEqual(os.listdir(os.path.join(self.tmp.name, "brand")), ["brand-kit.pdf"])
        self.assertEqual(self.save(GOOD), 200)
        self.p.wait(10)
        self.assertEqual(json.loads(read(self.out))["brandKit"], "brand/brand-kit.pdf")

    def test_good_answers_are_saved_and_it_stops(self):
        self.assertEqual(self.save(GOOD), 200)
        self.assertEqual(self.p.wait(10), 0)
        d = json.loads(read(self.out))
        self.assertEqual(d["colors"]["primary"], "#F1C92F")
        self.assertEqual(d["photos"], ["cinematic", "ink"])
        self.assertEqual(d["font"], "Poppins")


try:
    import cv2
    import numpy as np
    import skia

    sys.path.insert(0, os.path.join(K, "scripts"))
    import draw
    import layout
except (ImportError, SystemExit):
    layout = None


@unittest.skipUnless(layout, "the drawing tools are not installed here")
class Balance(unittest.TestCase):
    """one picture sits in the middle, two mirror each other, three stay balanced"""

    def setUp(self):
        draw.TYPE.update(sans=skia.Typeface(), accent=skia.Typeface(), emoji=skia.Typeface())
        draw.emoji.cache_clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.pic = os.path.join(self.tmp.name, "p.png")
        cv2.imwrite(self.pic, np.full((300, 400, 3), 90, np.uint8))

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, count, plan=None, emoji=None, behind="some", look="frame", face=None):
        words = [dict(i=i, word=f"w{i}", start=i * 0.4, end=i * 0.4 + 0.3) for i in range(8)]
        face = face or dict(x=296, y=853, w=466, h=664, top=763, chin=1517, found=True)
        style = dict(
            colors=dict(primary="#F1C92F", secondary="#FFF4C1", text="#FFFFFF"),
            photoLook=[look],
            captions="all-calm",
            behind=behind,
            emoji="few",
        )
        beat = dict(
            say=[0, 7], strong=[4], pictures=[dict(file=self.pic, at=1 + 2 * k) for k in range(count)], layout=plan
        )
        if emoji:
            beat["emoji"] = emoji
        return layout.Layout(dict(face=face, seconds=4), words, style, dict(beats=[beat]), self.tmp.name).build()

    def check(self, made, name):
        for n in range(0, 40):
            if abs(made.lean(n / 10)) > 70 and abs(made.lean(n / 10 + 0.25)) > 70:
                self.fail(f"{name} leans {made.lean(n / 10):.0f} px at {n / 10}")

    def test_never_leans(self):
        small = dict(x=430, y=500, w=220, h=300, top=420, chin=800, found=True)
        side = dict(x=600, y=853, w=400, h=500, top=700, chin=1353, found=True)
        for face in (None, small, side):
            for behind in ("lots", "some", "never"):
                for look in ("frame", "polaroid", "torn", "full", "sticker"):
                    for count, plans in ((0, [None]), (1, [None]), (2, ["sides", "pair"]), (3, ["fan", "triangle"])):
                        for plan in plans:
                            for emoji in (None, "o", ["o", "x"]):
                                self.check(
                                    self.make(count, plan, emoji, behind, look, face),
                                    (count, plan, emoji, behind, look),
                                )

    def test_one_picture_is_in_the_middle(self):
        made = [e for e in self.make(1).items if isinstance(e, draw.Sprite)]
        self.assertEqual([e.x for e in made], [540])

    def test_two_pictures_mirror(self):
        for plan in ("sides", "pair"):
            a, b = [e for e in self.make(2, plan).items if isinstance(e, draw.Sprite)]
            self.assertAlmostEqual(a.x + b.x, 1080, delta=1)
            self.assertEqual((a.y, a.tilt), (b.y, -b.tilt))


try:
    import trim
except (ImportError, SystemExit, NameError):
    trim = None


def talk(parts, sr=48000, seed=3):
    """fake speech: ('w', seconds) is a loud word, ('s', seconds) a soft hiss like an s, ('-', seconds) room quiet"""
    r, out, words, t = np.random.default_rng(seed), [], [], 0.0
    for kind, d in parts:
        n = int(d * sr)
        x = r.standard_normal(n).astype(np.float32)
        if kind == "w":
            x = 0.08 * x + 0.15 * np.sin(np.arange(n) * 2 * np.pi * 180 / sr).astype(np.float32)
            words.append(dict(i=len(words), word=f"w{len(words)}", start=round(t + 0.02, 3), end=round(t + d - 0.04, 3)))
        elif kind == "s":
            x = 0.03 * np.diff(x, prepend=0).astype(np.float32)
        else:
            x = 0.0004 * x
        out.append(x)
        t += d
    return np.concatenate(out), words


@unittest.skipUnless(trim, "the video tools are not installed here")
class Trim(unittest.TestCase):
    """mistakes and dead air come out, cuts land in the quiet, lines said twice on purpose stay"""

    def words(self, text):
        return [dict(i=i, word=w, start=i * 0.5, end=i * 0.5 + 0.4) for i, w in enumerate(text.split())]

    def test_restart_keeps_the_last_take(self):
        w = self.words("I felt so I felt so lost today.")
        again = trim.restarts(w)
        self.assertEqual(again[0][:2], (0, 3))
        self.assertEqual(trim.suggest(w, again, "tight", [])["keep"], [[3, 7]])

    def test_stutter_goes(self):
        w = self.words("I I think so.")
        self.assertEqual(trim.suggest(w, trim.restarts(w), "tight", [])["keep"], [[1, 3]])

    def test_line_said_twice_on_purpose_stays(self):
        w = self.words("You're not unsettled because something is wrong. You're unsettled because something is ready.")
        again = trim.restarts(w)
        self.assertTrue(again)
        self.assertEqual(trim.suggest(w, again, "tight", [])["keep"], [[0, len(w) - 1]])

    def test_um_goes(self):
        w = self.words("So um I think")
        self.assertEqual(trim.suggest(w, [], "tight", [])["keep"], [[0, 0], [2, 3]])

    def test_dead_air_is_shortened_and_cuts_are_quiet(self):
        x, w = talk([("-", 0.5), ("w", 0.4), ("-", 0.12), ("w", 0.4), ("-", 1.6), ("w", 0.4), ("-", 0.5)])
        lv = trim.Levels(x)
        sp = trim.spans(lv, w, dict(keep=[[0, 2]], pace="tight"))
        kept = sum(b - a for a, b in sp)
        self.assertLess(kept, 1.8)  # 1.2 s of words + a short gap + edges: the 1.6 s pause is nearly gone
        self.assertGreater(kept, 1.4)
        self.assertEqual(trim.check(trim.Levels(x), sp, w)[0], [])
        natural = trim.spans(trim.Levels(x), w, dict(keep=[[0, 2]], pace="natural"))
        self.assertGreater(sum(b - a for a, b in natural), kept)

    def test_soft_ending_is_kept(self):
        x, w = talk([("-", 0.5), ("w", 0.4), ("s", 0.15), ("-", 0.6), ("w", 0.4), ("-", 0.5)])
        hiss_end = 0.5 + 0.4 + 0.15
        self.assertGreaterEqual(trim.tail(trim.Levels(x), w, 0), hiss_end - 0.01)

    def test_runs_straight_on(self):
        x, w = talk([("-", 0.5), ("w", 0.4), ("s", 0.1), ("w", 0.4), ("-", 0.5)])
        t = trim.tail(trim.Levels(x), w, 0)
        self.assertGreaterEqual(t, 0.5 + 0.4 + 0.08)  # keeps the hiss
        self.assertLessEqual(t, 0.5 + 0.4 + 0.1 + 0.01)  # and stops before the next word


if __name__ == "__main__":
    unittest.main()
