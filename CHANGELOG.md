# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions: [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed
- Camera files FFmpeg cannot open (some Sony .MP4 files) are rewrapped on a Mac with Apple's avconvert first, with no quality lost; the copy is removed after trimming
- Retakes: an earlier take of a whole line said again is cut and the last take kept (before, complete retakes were all kept); lines repeated on purpose (a word like "not" added, a different ending) stay
- Stumbles the listener could not make out, and "Thanks for watching"-type words it invents in quiet moments, are cut
- A piece never keeps more than about a quarter second after its last word (quiet camera sound let breaths through)
- Every cut is checked by time: a kept word outside the pieces, or a cut word still inside, stops the cut; the read-back after it only lists words heard differently near a cut
- "Claude" (and other names given with --names) is heard correctly

### Changed
- Trimming decodes only the kept parts of the video, and a speed-up is done in the same pass (a 5-minute 4K camera file: 2:13 -> 1:38)

### Added
- Survey: "Tight, and talk a bit faster" (1.15x, same voice); `speed` in cut.json

## [0.1.0] - 2026-11-25

First public version. Tested on a Mac; Windows not tested yet.

### Added
- Click-only setup survey (12 questions)
- Picture and GIF finder with no sign-ups
- Word timing, person cutout, five caption styles, five photo looks, emojis, zooms and sound effects
- Brand colors by hex code or brand kit file
- Setup brings its own Python when the computer's is too old or too new, and checks for macOS 14
- Cuts restarts, ums and dead air before editing (tight or natural pace), with a check of every cut
- MIT license
