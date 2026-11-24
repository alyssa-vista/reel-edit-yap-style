"""Run once. Makes a private .venv in this folder and installs the free tools there. Nothing else on the computer changes."""

import os
import subprocess
import sys
import venv

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.abspath(os.path.join(HERE, *[os.pardir] * 4))

if sys.version_info < (3, 11):
    sys.exit("Python 3.11 or newer is needed. Get it from python.org/downloads, then run this again.")
for folder in ("raw", "final", "work"):
    os.makedirs(os.path.join(PROJECT, folder), exist_ok=True)
env = os.path.join(PROJECT, ".venv")
if not os.path.isdir(env):
    venv.create(env, with_pip=True)
python = os.path.join(env, "Scripts" if os.name == "nt" else "bin", "python")
need = ["-r", os.path.join(PROJECT, "requirements.txt")]
if sys.platform != "darwin":
    need.append("rembg[cpu]==2.0.84")  # finds the person when there is no Apple Vision
subprocess.run([python, "-m", "pip", "install", "--disable-pip-version-check", "-q", *need], check=True)
print("READY", python)
