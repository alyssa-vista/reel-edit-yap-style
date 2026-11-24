"""Run once, with any Python 3.8 or newer. Makes a private tools folder here and installs the free tools there.
If the computer's Python is too old or too new for the tools, it gets a private Python 3.13 inside this folder.
Nothing else on the computer changes."""

import os
import platform
import subprocess
import sys
import venv

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.abspath(os.path.join(HERE, *[os.pardir] * 4))
TOOLS = os.path.join(PROJECT, ".tools.nosync")  # iCloud skips names that end in .nosync
GOOD = ((3, 11), (3, 14))  # every tool in requirements.txt has a ready-made file for these
UV = "uv==0.13.9"


def fits(version):
    return GOOD[0] <= version <= GOOD[1]


def python_in(folder):
    return os.path.join(folder, "Scripts" if os.name == "nt" else "bin", "python")


def run(*cmd, **kw):
    subprocess.run(cmd, check=True, **kw)


def private_python(env):
    """A Python of our own, kept inside this folder."""
    helper = os.path.join(TOOLS, "helper")
    if not os.path.isfile(python_in(helper)):
        venv.create(helper, with_pip=True)
    run(python_in(helper), "-m", "pip", "install", "--disable-pip-version-check", "-q", UV)
    where = dict(os.environ, UV_PYTHON_INSTALL_DIR=os.path.join(TOOLS, "python"), UV_CACHE_DIR=os.path.join(TOOLS, "cache"),
                 UV_PYTHON_INSTALL_BIN="0", UV_PYTHON_INSTALL_REGISTRY="0")
    run(python_in(helper), "-m", "uv", "venv", "-q", "--seed", "--managed-python", "--python", "3.13", env, cwd=TOOLS, env=where)


def main():
    mac = platform.mac_ver()[0]
    if sys.platform == "darwin" and mac and int(mac.split(".")[0]) < 14:
        sys.exit("This kit needs macOS 14 or newer. Update the Mac in System Settings > General > Software Update, then run this again.")
    for folder in ("raw", "final", "work"):
        os.makedirs(os.path.join(PROJECT, folder), exist_ok=True)
    env = os.path.join(TOOLS, "venv")
    python = python_in(env)
    if not os.path.isfile(python):
        if fits(sys.version_info[:2]):
            venv.create(env, with_pip=True)
        else:
            private_python(env)
    need = ["-r", os.path.join(PROJECT, "requirements.txt")]
    if sys.platform != "darwin":
        need.append("rembg[cpu]==2.0.84")  # finds the person when there is no Apple Vision
    run(python, "-m", "pip", "install", "--disable-pip-version-check", "-q", "--only-binary=:all:", *need)
    print("READY", python)


if __name__ == "__main__":
    main()
