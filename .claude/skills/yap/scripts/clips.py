"""Moving clips from the internet, live, free, no sign-up (Internet Archive).
clips.py find "cat | kitten" --style animals          films to look in (public domain mark, or older than 95 years)
clips.py look <film id> out.jpg [--from 0 --to 300]   a sheet of 24 frames with their times
clips.py cut  <film id> <start second> <seconds> out.mp4"""

import argparse, datetime, json, os, re, subprocess, sys, tempfile, time, urllib.parse
import find
from tools import FFMPEG, run

OLD = datetime.date.today().year - 96
WHERE = dict(
    silentcomedy=f"year:[1900 TO {OLD}]",
    oldcartoon=f"year:[1905 TO {OLD}] AND (title:(cartoon OR felix OR koko OR alice) OR subject:(animation OR cartoon))",
    colormovie="licenseurl:*publicdomain* AND (technicolor OR color OR colour) AND year:[1935 TO 1975]",
    reaction=f"year:[1910 TO {OLD}]",
    schoolfilm="collection:prelinger AND licenseurl:*publicdomain*",
    movieshots="licenseurl:*publicdomain* AND (technicolor OR color OR colour) AND year:[1935 TO 1975]",
    silent=f"year:[1900 TO {OLD}]",
    cartoon=f"year:[1905 TO {OLD}] AND (title:(cartoon OR felix OR koko OR alice) OR subject:(animation OR cartoon))",
)
ANY = "collection:(prelinger OR newsandpublicaffairs OR home_movies) AND licenseurl:*publicdomain*"
SKIP = re.compile(find.RUDE.pattern + "|birth.of.a.nation|triumph.of.the.will|beware|war.?crime|\\bstag\\b", re.I)


def ident(x):
    x = x.rstrip("/").split("/")[-1]
    if not re.fullmatch("[A-Za-z0-9._-]{1,120}", x):
        sys.exit("not a film id")
    return x


def search(q, style, n=8):
    words = " OR ".join(("(" + re.sub("[^\\w ]", " ", w).strip() + ")" for w in q.split("|") if w.strip()))
    out = []
    for where in [WHERE.get(style, ANY)] + (
        [] if style in WHERE else ["licenseurl:*publicdomain* AND year:[1900 TO 1975]"]
    ):
        d = find.get(
            "https://archive.org/advancedsearch.php?"
            + urllib.parse.urlencode(
                {
                    "q": f"(title:({words}) OR subject:({words}) OR description:({words})) AND mediatype:movies AND {where}",
                    "fl[]": ["identifier", "title", "year", "licenseurl", "description"],
                    "sort[]": "downloads desc",
                    "rows": 30,
                    "output": "json",
                },
                doseq=True,
            )
        )
        for x in d["response"]["docs"]:
            t = str(x.get("description") or "")
            lic = x.get("licenseurl") or ""
            if SKIP.search(x["identifier"] + " " + str(x.get("title"))) or any(
                (o["id"] == x["identifier"] for o in out)
            ):
                continue
            if "publicdomain" not in lic and (not (str(x.get("year", "")).isdigit() and int(x["year"]) <= OLD)):
                continue
            out.append(
                dict(
                    id=x["identifier"],
                    title=x.get("title"),
                    year=x.get("year"),
                    why=(
                        "public domain mark"
                        if "publicdomain" in lic
                        else f"published {x['year']}, over 95 years ago (US)"
                    ),
                    about=re.sub("<[^>]+>|\\s+", " ", t)[:160],
                    page="https://archive.org/details/" + x["identifier"],
                )
            )
        if len(out) >= n:
            break
    return out[:n]


def video(i):
    d = find.get(f"https://archive.org/metadata/{i}")
    fs = [f for f in d.get("files", []) if f["name"].lower().endswith(".mp4") and float(f.get("size") or 0) > 100000.0]
    if not fs:
        sys.exit("this film has no mp4, pick another one")
    fs.sort(key=lambda f: abs(int(f.get("height") or 480) - 720))
    f = fs[0]

    def sec(v):
        p = [float(x) for x in str(v).split(":")]
        return sum((x * 60**k for k, x in enumerate(reversed(p))))

    return (f"https://archive.org/download/{i}/" + urllib.parse.quote(f["name"]), sec(f.get("length") or 0))


def ff(u, t, args, timeout=90):
    r = subprocess.run(
        [
            FFMPEG,
            "-v",
            "error",
            "-y",
            "-user_agent",
            find.UA,
            "-protocol_whitelist",
            "https,tls,tcp,crypto",
            "-ss",
            f"{t:.2f}",
            "-i",
            u,
        ]
        + args,
        capture_output=True,
        timeout=timeout,
    )
    if r.returncode:
        raise find.Unreachable(r.stderr.decode(errors="replace")[-200:])


def look(i, out, t0, t1, n=24):
    """the film site already keeps one small picture about every 30 s: quick and gentle. --from/--to then zooms in"""
    import cv2, numpy as np

    d = find.get(f"https://archive.org/metadata/{i}")
    th = []
    for f in d.get("files", []):
        m = re.search("\\.thumbs/.*_(\\d{6})\\.jpg$", f["name"])
        if m:
            th.append((int(m[1]), f["name"]))
    th = sorted((x for x in th if x[0] >= t0 and (not t1 or x[0] <= t1)))
    th = th[:: max(1, len(th) // n)][:n]
    tiles = []
    for t, name in th:
        try:
            im = cv2.imdecode(
                np.frombuffer(
                    find.get(f"https://archive.org/download/{i}/" + urllib.parse.quote(name), raw=True, gap=0.4),
                    np.uint8,
                ),
                1,
            )
        except find.Unreachable:
            continue
        if im is not None:
            im = cv2.resize(im, (220, 165))
            cv2.putText(im, str(t), (5, 18), 0, 0.55, (0, 255, 255), 2)
            tiles.append(im)
    if not tiles:
        sys.exit("no pictures of this film to look at, pick another one")
    while len(tiles) % 6:
        tiles.append(np.zeros_like(tiles[0]))
    cv2.imwrite(out, np.vstack([np.hstack(tiles[r : r + 6]) for r in range(0, len(tiles), 6)]))
    print(out, len(th), "moments")


def cut(i, t, s, out):
    s = min(max(s, 0.5), 8)
    u, L = video(i)
    ff(
        u,
        t,
        [
            "-t",
            f"{s:.2f}",
            "-an",
            "-vf",
            "scale=-2:min(720\\,ih),setsar=1",
            "-c:v",
            "libx264",
            "-crf",
            "16",
            "-pix_fmt",
            "yuv420p",
            out,
        ],
        180,
    )
    print(out, os.path.getsize(out) // 1000, "kB")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    s = p.add_subparsers(dest="c", required=True)
    a = s.add_parser("find")
    a.add_argument("q")
    a.add_argument("--style", default="any")
    a.add_argument("--n", type=int, default=8)
    a = s.add_parser("look")
    a.add_argument("id")
    a.add_argument("out")
    a.add_argument("--from", type=float, default=0, dest="t0")
    a.add_argument("--to", type=float, default=0, dest="t1")
    a = s.add_parser("cut")
    a.add_argument("id")
    a.add_argument("start", type=float)
    a.add_argument("seconds", type=float)
    a.add_argument("out")
    a = p.parse_args()
    try:
        if a.c == "find":
            for x in search(a.q, a.style, a.n):
                print(json.dumps(x, ensure_ascii=False))
        elif a.c == "look":
            look(ident(a.id), a.out, a.t0, a.t1)
        else:
            cut(ident(a.id), a.start, a.seconds, a.out)
    except find.Unreachable as e:
        sys.exit(f"could not reach the film site: {e}. Try another film.")
