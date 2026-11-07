# mtxMusicTypist

An integrated desktop editor for typesetting music with **M-Tx** (and PMX / MusiXTeX underneath).
Write M-Tx source on the left, press **F5**, and see the engraved score as a PDF on the right —
with two-way navigation between the PDF and the source, a note-input helper, a header editor,
MIDI playback, and a few tools for catching typical M-Tx mistakes before they reach the compiler.

mtxMusicTypist combines and extends two earlier tools by the same author, *mtxstarter* (header
settings and build) and *mtxvisualhelper* (note-input panel).

> The user interface is in Korean. Korean lyrics and titles are supported through LuaTeX + `luatexko`
> or pdfTeX + `kotexplain`.

## Features

**Editing**
- M-Tx source editor with syntax colouring (notes, chords, rests, slurs/ties, markers), line numbers,
  and a gutter showing the PMX bar number of each line.
- Find / replace (`Ctrl+F`, `Ctrl+H`, `F3` / `Shift+F3`): plain text or regular expressions
  (`\1`, `\g<name>`, `\n` in the replacement), case sensitivity, "selection only", all matches
  highlighted, *Replace all* undoable in one step.
- Slur / tie matching across lines, bars and paragraphs; the partner of a bracket under the cursor is
  highlighted, and `Ctrl+]` jumps to it.
- Soft wrap display, recent files, last-session restore, command-line file argument.
- **Bar-beat check** before compiling (see below).

**Building and viewing**
- `F5` compiles with `prepmx` / `pmxab` / `musixtex` (pdfTeX or LuaTeX) and shows the PDF in a dockable viewer
  (or in the system PDF viewer).
- PDF ⇄ source synchronisation: double-click a bar in the PDF to jump to the source, and the bar under the
  source cursor is marked in the PDF.
- Optional margin cropping (`pdfcrop`), build-file cleanup, and a PMX editor panel for hand patches
  that are re-applied on every build.
- Korean support: the *Hangul* option inserts a block that selects `luatexko` under LuaTeX and
  `kotexplain` under pdfTeX at compile time.

**Input helpers**
- *Note input* dock (from mtxvisualhelper): enter notes, rests and accidentals with the keyboard;
  works while a Korean IME is active.
- *Header* panel: title, composer, meter, key, style, instruments, MIDI tempo/instruments, page layout, etc.
  The panel reads and writes the M-Tx header and leaves untouched settings as they were.

**Playback**
- `F8` plays the generated MIDI from the bar under the cursor (`Shift+F8` from the beginning) using a
  SoundFont through pygame/SDL_mixer.

**MIDI import (experimental)**
- *File ▸ Import MIDI file…* converts a MIDI file to an M-Tx draft (one track = one staff).
  Key signature, accidental spelling, ties, pickup bars, meter changes, chords, triplets/sextuplets and
  sustained bass notes are handled; drum tracks can be drawn on a staff by instrument.
  MIDI contains no engraving information, so the result is a starting point, not a faithful conversion.
  The converter also works from the command line (see below).

## The bar-beat check

The most common M-Tx error is a bar whose notes do not add up to the meter. Before compiling, the app sums
the durations of every bar of every staff and voice and, if something does not match, lists the
offending bars; selecting an entry selects the bar in the editor. Choose **Compile anyway** for
intentional exceptions (e.g. PMX free beats) or **Go and fix**.

What is counted: inherited durations (including dots), triplet/`xN` groups (once, by the first note),
`rp` (a full bar), bars closed automatically when the meter is filled (explicit `|` is optional), and
inline meter changes (`3/4`, `C`). Chord notes (`z…`), grace notes (`G…`) and rest offsets (`r-2`)
carry no duration. The first (pickup) and the last bar may be short. Toggle it under **Build ▸ Check bar beats
before compiling**; run it alone with `Ctrl+Shift+B`.

## Requirements

- Python 3 (a recent version; PyQt6 requires 3.9 or newer)
- Python packages: `PyQt6` (with `QtPdf` and `QtMultimedia`), `mido` (MIDI import), `pygame` (playback)
- A TeX installation with **M-Tx** (`prepmx`), **PMX** (`pmxab`), **MusiXTeX** (`musixtex`), and optionally
  LuaTeX (with `luatexko` for Korean) and `pdfcrop`. TeX Live ships all of these. TeX Live programs must be
  on `PATH`; otherwise set the folder under *Settings ▸ TeX path…*.
- A General MIDI SoundFont (e.g. FluidR3_GM or GeneralUser GS) for playback — set it under *Settings*.

Tested on Linux; the app is written to run on Windows and macOS as well.

## Running

```
pip install PyQt6 mido pygame
python3 mtxmusictypist.py [file.mtx]
```

`--version` prints the version. Use *Help ▸ Diagnostics* to check which TeX programs and patched
`.tex` files the app found. The patched `musixtex_patched.tex` and `pmx_patched.tex` in this folder are
needed for LuaTeX builds and must stay next to the program.

Shortcuts worth knowing: `F5` build, `F6` toggle cursor follow, `F7` build with PMX patch, `F8` play,
`Ctrl+1…5` show/hide docks, `Ctrl+Enter` insert from the note-input dock, `Ctrl+L` start a lyrics line,
`Alt+X/S/F/N` accidentals. *Help ▸ Usage* lists them all.

## MIDI → M-Tx from the command line

`mtxmidi.py` does not need Qt, only `mido`:

```
python3 mtxmidi.py song.mid -o song.mtx [options]
```

| Option | Meaning |
|---|---|
| `-t 1,2` | convert only these tracks |
| `--bars N` | bars per paragraph (default 4) |
| `--grid {8,16,32,64}` | quantisation grid (default 32) |
| `--pickup BEATS` | length of the pickup bar in quarter notes |
| `--key N` | force the key signature (`--key=-3` = three flats) |
| `--drums` | draw drum tracks on a staff by instrument |
| `--legato X` | how much of a gap between notes is absorbed into legato |
| `--order {file,pitch}` | staff order |
| `--no-chords`, `--no-tuplets`, `--no-carry` | switch off chord, tuplet or sustained-note (tie) handling |
| `--title TEXT` | title in the header |

Limitations: lyrics and multi-voice (`//`) output are not generated; a sustained note is carried by a tie only
when it is the lowest note of the chord; very dense tuplet bars can make PMX's own MIDI output drift, although
the score itself is correct.

## Repository layout

| File | Purpose |
|---|---|
| `mtxmusictypist.py` | main window, build pipeline, menus |
| `mtxwidgets.py` | editor, find bar, header panel, PDF view, PMX panel |
| `mtxheader.py` | M-Tx header parsing and generation |
| `mtxmap.py`, `mtxpos.py`, `mtxpair.py` | source ⇄ PMX ⇄ PDF bar mapping, bracket pairing |
| `mtxbeats.py` | bar-beat checker |
| `mtxmidi.py` | MIDI → M-Tx converter |
| `mtxplayer.py` | MIDI playback |
| `mtxvisualhelper.py` | note-input panel (bundled from the stand-alone tool) |
| `pmxpatch.py` | storing and re-applying PMX patches |
| `musixtex_patched.tex`, `pmx_patched.tex` | patched TeX files for LuaTeX + Korean |
| `samples/` | example `.mtx`, `.pmx` and `.mid` files |
| `test_*.py` | regression tests (`python3 test_mapping.py`, …) |
| `CHANGELOG.md` | version history (in Korean) |
| `BUILD.md` | packaging with PyInstaller |

## Packaging

See [BUILD.md](BUILD.md). In short: `pip install pyinstaller PyQt6 mido pygame` and
`pyinstaller --noconfirm mtxmusictypist.spec`.

## Acknowledgements

Built on [M-Tx](https://ctan.org/pkg/m-tx), [PMX](https://ctan.org/pkg/pmx) and
[MusiXTeX](https://ctan.org/pkg/musixtex).
