# reel-edit-yap-style

Turn a talking video into a yap-style reel with Claude Code. You talk. Claude writes down every word, finds photos, GIFs and emojis that match, and puts them on the exact word, even behind your head.

> **Status: early.** It works on a Mac. Windows is not tested yet. See [CHANGELOG](CHANGELOG.md).

## What you need

- The Claude desktop app with a paid plan (the Code tab needs one)
- A Mac. Windows is planned and not tested yet
- No accounts and no keys for pictures

## Start

1. Open the Claude app and click **Code**.
2. Choose a new empty folder on your computer.
3. Paste this and press Enter:

```
Install https://github.com/alyssa-vista/reel-edit-yap-style into this folder and start the setup.
```

4. Click your answers in the survey. You never have to type.
5. Put your video in the `raw` folder and say `edit my video`.

## Change anything by saying it

`change the photo at 0:12` · `remove the emoji` · `use a funnier GIF` · `make the captions slower` · `open the survey again`

## How it works

| Step | What does it |
|---|---|
| Your style | `survey.py` opens a page on your own computer and saves `my-style.json` |
| Pictures | `find.py` uses one free tool per style: Wikimedia Commons, Openverse, the Cleveland Museum of Art, NASA, memegen.link and Iconify. `clips.py` finds and cuts moving clips from the Internet Archive |
| Words | `prep.py` writes down every word and its time with [faster-whisper](https://github.com/SYSTRAN/faster-whisper), on your computer |
| You, cut out | Apple Vision on a Mac, [rembg](https://github.com/danielgatis/rembg) on other computers |
| Video | `make.py` draws everything with [skia-python](https://github.com/kyamagu/skia-python), OpenCV and FFmpeg |

Claude follows the steps in [SKILL.md](.claude/skills/yap/SKILL.md): it chooses the pictures, shows you a list, and waits for your yes.

## Safety

- Your video never leaves your computer through this kit. Claude itself works under Anthropic's own terms.
- The survey page only answers to your own computer, uses a one-time address, and accepts only answers from its own list.
- No telemetry, no passwords, no keys.
- Found a problem? See [SECURITY](SECURITY.md).

## Pictures and rights

The finder keeps files marked public domain or CC0. Three styles are different: **memes**, **anime reactions** and **90s internet GIFs** belong to someone else or have an unknown owner, so the kit marks them "check rights". Rules differ by country, and you are responsible for what you post. Sources are listed in [ATTRIBUTIONS](ATTRIBUTIONS.md).

## Contributing

See [CONTRIBUTING](CONTRIBUTING.md). No licence has been chosen yet: all rights reserved. Not affiliated with or endorsed by Anthropic.
