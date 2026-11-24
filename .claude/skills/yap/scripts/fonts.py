"""Downloads the chosen Google Font once and keeps it on this computer."""

import os
import re
import urllib.parse

import find

CACHE = os.path.join(os.path.expanduser("~"), ".cache", "reel-edit-yap-style", "fonts")


def path(name):
    """'Inter Tight' or 'Playfair Display:ital' -> a .ttf file"""
    family, italic = name.replace(":ital", ""), name.endswith(":ital")
    out = os.path.join(CACHE, re.sub(r"\W+", "-", name) + ".ttf")
    if os.path.exists(out):
        return out
    os.makedirs(CACHE, exist_ok=True)
    for spec in (["ital,wght@1,500", "ital@1"] if italic else ["wght@600", ""]):
        url = (
            "https://fonts.googleapis.com/css2?family=" + urllib.parse.quote_plus(family) + (":" + spec if spec else "")
        )
        try:
            css = find.get(url, raw=True, tries=2, gap=0.3).decode()
        except find.Unreachable:
            continue
        link = re.search(r"url\((https://fonts\.gstatic\.com/[^)]+\.ttf)\)", css)
        if link:
            with open(out, "wb") as f:
                f.write(find.get(link[1], raw=True, gap=0.3))
            return out
    raise SystemExit(f"could not get the font {family}. Check the internet, or pick another font.")
