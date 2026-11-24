"""find.py "<thing you can see | another thing | another>" --style <photo or gif style id> [--kind photo|gif] [--n 6] [--out folder]
Search for things a camera can see ("broken cup"), never feelings ("heartbreak"); give 2 or 3, separated by |. Finds free pictures and moving clips. No sign-up, no key. Keeps only files marked public domain / CC0 (meme pictures and 90s web GIFs are the
exception and are labelled "check rights"). Prints one JSON line per result and saves small previews in the out folder.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "reel-edit-yap-style/0.1"
FREE = re.compile("^(cc0|public domain|pd|no restrictions)", re.I)
RUDE = re.compile(
    "\\b(nudes?|naked|strippers?|striptease|burlesque|erotic\\w*|porn\\w*|lynch\\w*|klan|nazi\\w*|hitler|apartheid|execution|corpse|suicide)\\b",
    re.I,
)


class Unreachable(Exception):
    pass


CACHE, GAP, _last = (os.path.join(os.path.expanduser("~"), ".cache", "reel-edit-yap-style"), 3.0, {})


def get(u, raw=False, tries=3, gap=None):
    """curl, not urllib: the python.org Python on a Mac often has no certificates, curl (built into macOS and Windows) always does. Certificates stay checked."""
    if urllib.parse.urlsplit(u).scheme != "https":
        raise Unreachable("only https links")
    key = os.path.join(CACHE, hashlib.sha256(u.encode()).hexdigest() + ".json")
    host = urllib.parse.urlsplit(u).netloc
    if not raw and os.path.exists(key) and (time.time() - os.path.getmtime(key) < 7 * 86400):
        return json.load(open(key, encoding="utf-8"))
    why = ""
    for n in range(tries):
        time.sleep(max(0, (GAP if gap is None else gap) - (time.time() - _last.get(host, 0))))
        _last[host] = time.time()
        r = subprocess.run(
            [
                "curl",
                "-sS",
                "-L",
                "--proto",
                "=https",
                "--proto-redir",
                "=https",
                "--max-time",
                "60",
                "--max-filesize",
                "60000000",
                "-A",
                UA,
                "-w",
                "\n%{http_code}",
                "--",
                u,
            ],
            capture_output=True,
        )
        body, _, code = r.stdout.rpartition(b"\n")
        code = code.decode()
        if code == "200":
            if raw:
                return body
            try:
                d = json.loads(body)
                os.makedirs(CACHE, exist_ok=True)
                json.dump(d, open(key, "w", encoding="utf-8"))
                return d
            except ValueError:
                why = "answer was not data"
                break
        why = r.stderr.decode(errors="replace").strip()[:120] or f"code {code}"
        if code in ("401", "403", "404", "410"):
            break
        time.sleep(20 * (n + 1) if code == "429" else 2 + 4 * n)
    raise Unreachable(f"{urllib.parse.urlsplit(u).netloc}: {why}")


def commons(q, n, what):
    ft = {"photo": "filetype:bitmap", "video": "filetype:video", "gif": "filemime:image/gif"}[what]
    d = get(
        "https://commons.wikimedia.org/w/api.php?"
        + urllib.parse.urlencode(
            dict(
                action="query",
                format="json",
                generator="search",
                gsrnamespace=6,
                gsrlimit=max(20, n * 4),
                gsrsearch=f"{q} {ft}",
                prop="videoinfo" if what == "video" else "imageinfo",
                **(
                    {"viprop": "url|size|extmetadata", "viurlwidth": 400}
                    if what == "video"
                    else {"iiprop": "url|size|extmetadata", "iiurlwidth": 400}
                ),
            )
        )
    )
    out = []
    for p in sorted(d.get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 99)):
        i = (p.get("videoinfo") or p.get("imageinfo") or [None])[0]
        if not i:
            continue
        lic = (i.get("extmetadata", {}).get("LicenseShortName", {}) or {}).get("value", "")
        if FREE.match(lic):
            out.append(
                dict(
                    title=p["title"][5:],
                    licence=lic,
                    page=i.get("descriptionurl"),
                    url=i["url"],
                    thumb=i.get("thumburl") or i["url"],
                    w=i.get("width"),
                    h=i.get("height"),
                    seconds=i.get("duration"),
                )
            )
    return out


def cma(q, n, kind):
    d = get(
        "https://openaccess-api.clevelandart.org/api/artworks/?"
        + urllib.parse.urlencode(dict(q=q, type=kind, cc0=1, has_image=1, limit=n * 2))
    )
    return [
        dict(
            title=a["title"],
            licence="CC0",
            page=a["url"],
            url=a["images"]["print"]["url"],
            thumb=a["images"]["web"]["url"],
        )
        for a in d["data"]
        if a.get("images", {}).get("web") and a["images"].get("print")
    ]


def nasa(q, n, video):
    d = get(
        "https://images-api.nasa.gov/search?"
        + urllib.parse.urlencode(dict(q=q, media_type="video" if video else "image"))
    )
    out = []
    for i in d["collection"]["items"][: n * 2]:
        t = [l["href"] for l in i.get("links", []) if l.get("render") == "image"]
        if t:
            out.append(
                dict(
                    title=i["data"][0]["title"],
                    licence="NASA (free to use, do not imply NASA endorses you)",
                    page=i["href"],
                    url=i["href"],
                    thumb=t[0].replace(" ", "%20").replace("http://", "https://"),
                )
            )
    return out


def gifcities(q, n):
    d = get("https://gifcities.archive.org/api/v1/gifsearch?" + urllib.parse.urlencode(dict(q=q)))
    d = [x for x in d if 60 <= (x.get("width") or 0) and 60 <= (x.get("height") or 0)]
    return [
        dict(
            title=x["url_text"],
            licence="unknown: old web GIF, check rights",
            page=x["page"],
            url="https://web.archive.org/web/" + x["gif"].replace("/http", "if_/http", 1),
            thumb="https://web.archive.org/web/" + x["gif"].replace("/http", "if_/http", 1),
        )
        for x in d[: n * 2]
    ]


def memes(q, n):
    d = get("https://api.memegen.link/templates/")
    ws = [w for w in re.findall("\\w+", q.lower()) if len(w) > 2]

    def score(m):
        t = (
            m["name"]
            + " "
            + " ".join(m.get("keywords") or [])
            + " "
            + " ".join((m.get("example") or {}).get("text") or [])
        ).lower()
        return -sum((w in t for w in ws))

    return [
        dict(
            title=m["name"],
            licence="owner unknown: meme picture, check rights",
            page=m.get("source") or "https://memegen.link",
            url=m["blank"],
            thumb=m["blank"],
        )
        for m in sorted(d, key=score)
    ]


def openverse(q, n):
    d = get(
        "https://api.openverse.org/v1/images/?"
        + urllib.parse.urlencode(dict(q=q, license="cc0,pdm", page_size=min(20, n * 2), mature="false"))
    )
    return [
        dict(
            title=x["title"] or q,
            licence={"cc0": "CC0", "pdm": "Public domain"}[x["license"]],
            page=x["foreign_landing_url"],
            url=x["url"],
            thumb=x["thumbnail"],
            w=x.get("width"),
            h=x.get("height"),
        )
        for x in d["results"]
        if x["license"] in ("cc0", "pdm")
    ]


FEEL = dict(
    angry="angry",
    cry="cry",
    sad="cry",
    happy="happy",
    laugh="laugh",
    wave="wave",
    dance="dance",
    tired="sleep",
    sleep="sleep",
    bored="bored",
    shocked="shocked",
    confused="confused",
    think="think",
    run="run",
    shrug="shrug",
    smile="smile",
    wink="wink",
    blush="blush",
    facepalm="facepalm",
    nope="nope",
    thumbsup="thumbsup",
    pout="pout",
    yawn="yawn",
    stare="stare",
    clap="clap",
    hug="hug",
    love="hug",
)


def anime(q, n):
    k = next((v for w, v in FEEL.items() if w in q.lower()), "think")
    d = get(f"https://nekos.best/api/v2/{k}?amount={min(n, 10)}")
    return [
        dict(
            title=f"{k}: {x['anime_name']}",
            licence="owned by the anime studio: check rights",
            page="https://nekos.best",
            url=x["url"],
            thumb=x["url"],
        )
        for x in d["results"]
    ]


def icons(q, n):
    d = get("https://api.iconify.design/search?" + urllib.parse.urlencode(dict(query=q, limit=32)))
    ok = ("MIT", "Apache-2.0", "CC0-1.0", "ISC")
    out = []
    for x in d["icons"]:
        pre, name = x.split(":")
        lic = ((d.get("collections") or {}).get(pre, {}).get("license") or {}).get("spdx")
        if lic in ok and "logo" not in pre and ("brand" not in pre):
            u = f"https://api.iconify.design/{pre}/{name}.svg?height=600"
            out.append(dict(title=name, licence=lic, page="https://icon-sets.iconify.design/" + pre, url=u, thumb=u))
    return out


def emoji(q):
    """Google's moving emoji (Noto, CC BY 4.0: keep the credit line)"""
    h = "_".join((f"{ord(c):x}" for c in q.strip() if c != "️"))
    u = f"https://fonts.gstatic.com/s/e/notoemoji/latest/{h}/512.webp"
    return [
        dict(
            title=q,
            licence="CC BY 4.0, credit: Noto Emoji by Google",
            page="https://googlefonts.github.io/noto-emoji-animation/",
            url=u,
            thumb=u,
        )
    ]


def films(q, style, n):
    import clips

    return [
        dict(
            title=x["title"],
            licence=x["why"],
            page=x["page"],
            url="",
            thumb="https://archive.org/services/img/" + x["id"],
            film=x["id"],
            next=f"clips.py look {x['id']} sheet.jpg",
        )
        for x in clips.search(q, style, n)
    ]


def ladder(q, add, tail, n, what):
    """Wikimedia wants every word to match: start exact, then loosen"""
    out, w = ([], add.split())
    for k in range(len(w), -1, -1):
        out += [
            x
            for x in commons(" ".join([q] + w[:k] + [tail]).strip(), n, what)
            if x["url"] not in {y["url"] for y in out}
        ]
        if len(out) >= 3:
            return out
    return out or (commons(add, n, what) if add else [])


SECOND = dict(
    unsplash=lambda q, a, n: openverse(q, n),
    commons=lambda q, a, n: openverse(f"{q} {a}", n),
    openverse=lambda q, a, n: ladder(q, a, "", n, "photo"),
    cma=lambda q, a, n: ladder(q, a.lower() + " museum", "", n, "photo"),
)


def search(q, st, n):
    """the right free tool for each style; a second one when the first finds too little"""
    s, add, two = (st["src"], st["add"], [])
    first = dict(
        unsplash=lambda: ladder(q, add, "Unsplash", n, "photo"),
        commons=lambda: ladder(q, add, "", n, "photo"),
        commonsgif=lambda: ladder(q, add, "", n, "gif"),
        cma=lambda: cma(q, n, add),
        openverse=lambda: openverse(f"{q} {add}".strip(), n),
        nasa=lambda: nasa(q, n, False),
        nasavideo=lambda: nasa(q, n, True),
        gifcities=lambda: gifcities(q, n),
        memes=lambda: memes(q, n),
        anime=lambda: anime(q, n),
        icons=lambda: icons(q, n),
        emoji=lambda: emoji(q),
        films=lambda: films(q, st["id"], n),
    )[s]
    try:
        r = first()
    except Unreachable:
        if s not in SECOND:
            raise
        r = []
    if len(r) < 3 and s in SECOND:
        try:
            two = SECOND[s](q, add, n)
        except Unreachable:
            two = []
    return [
        x for x in r + [x for x in two if x["url"] not in {y["url"] for y in r}] if not RUDE.search(x["title"] or "")
    ]


if __name__ == "__main__":
    a = argparse.ArgumentParser()
    a.add_argument("q")
    a.add_argument("--style", required=True)
    a.add_argument("--kind", default="photo")
    a.add_argument("--n", type=int, default=6)
    a.add_argument("--out")
    a = a.parse_args()
    S = json.load(open(os.path.join(os.path.dirname(HERE), "styles.json")))
    st = next((x for x in S[a.kind] if x["id"] == a.style), None) or sys.exit("no such style: " + a.style)
    res, seen = ([], set())
    try:
        for q in [x.strip() for x in a.q.split("|") if x.strip()]:
            for r in search(q, st, a.n):
                if r["url"] not in seen:
                    seen.add(r["url"])
                    res.append(r)
            if len(res) >= a.n:
                break
        res = res[: a.n]
    except Unreachable as e:
        sys.exit(f"could not reach {e}. Try again in a minute.")
    for i, r in enumerate(res):
        if a.out and r["thumb"]:
            os.makedirs(a.out, exist_ok=True)
            f = os.path.join(
                a.out,
                f"{a.style}_{i + 1}" + (os.path.splitext(urllib.parse.urlsplit(r["thumb"]).path)[1] or ".jpg")[:5],
            )
            try:
                b = get(r["thumb"], raw=True, gap=0.5)
            except Unreachable:
                continue
            if not b[:15].lstrip().lower().startswith((b"<!doctype", b"<html")) and len(b) > 400:
                with open(f, "wb") as out:
                    out.write(b)
                r["preview"] = f
        print(json.dumps(r, ensure_ascii=False))
    if not res:
        print('{"none": "nothing free found, try a simpler word"}')
