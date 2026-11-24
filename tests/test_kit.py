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


if __name__ == "__main__":
    unittest.main()
