#!/usr/bin/env python3
"""
mtxheader.py - M-Tx 헤더(첫 빈 줄 앞 블록)의 파싱/생성. GUI 비의존.
mtxstarter.py 의 parse_file_to_state / generate_preamble 로직을 분리한 것.

  state, body_off = parse_header(text)   # body_off: 본문이 시작되는 오프셋
  new_text = apply_state(text, state)    # 헤더를 state 로 다시 쓰고 %%I(MIDI) 토글
"""
from __future__ import annotations

import re
from typing import Dict, Tuple

METERS = ["C", "C/", "2/2", "2/4", "3/4", "4/4", "3/8", "6/8", "9/8", "12/8", "Custom"]
STYLES = ["", "Singer", "SATB", "SATB4", "Piano", "Singer Piano",
          "Organ", "SOLO", "DUET", "TRIO", "QUARTET", "Custom"]
SIZES = ["", "13", "16", "20", "24", "29", "16 20", "20 16"]

# %%I (PMX 의 MIDI 줄) 의 악기 코드 — 이름은 PMX 매뉴얼의 표
MIDI_INSTRUMENTS = [
    ("pi", "Acoustic Grand Piano"), ("rh", "Rhodes Piano"), ("ha", "Harpsichord"), ("ct", "Clavinet"),
    ("or", "Church Organ"), ("so", "Soprano Sax"), ("al", "Alto Sax"), ("te", "Tenor Sax"),
    ("bs", "Baritone Sax"), ("vl", "Violin"), ("va", "Viola"), ("vc", "Cello"),
    ("cb", "Contrabass"), ("ab", "Acoustic Bass"), ("tr", "Trumpet"), ("fr", "French Horn"),
    ("tb", "Trombone"), ("tu", "Tuba"), ("re", "Recorder"), ("fl", "Flute"), ("ob", "Oboe"),
    ("cl", "Clarinet"), ("ba", "Bassoon"), ("ma", "Marimba"), ("gu", "Acoustic Nylon Guitar"),
    ("vo", "Synth Voice"),
]
_MIDI_CODES = {c for c, _ in MIDI_INSTRUMENTS}


def parse_midi_line(line: str):
    """`%%Iipit60` / `%%It84ivofl` -> (악기 코드 목록, 템포(0=없음), 해석 못한 나머지).
    t+숫자 = 템포, i + 2글자 코드들 = 파트별 악기. 그 밖의 것(볼륨 등)은 rest 로 보존."""
    s = line[3:] if line.startswith("%%I") else line
    instr, tempo, rest, i = [], 0, "", 0
    while i < len(s):
        m = re.match(r"t(\d+)", s[i:])
        if m:
            tempo = int(m.group(1)); i += m.end(); continue
        if s[i] == "i" and not instr:
            j = i + 1
            while j + 1 < len(s) and s[j:j + 2] in _MIDI_CODES and not re.match(r"t\d", s[j:]):
                instr.append(s[j:j + 2]); j += 2
            if j > i + 1:
                i = j; continue
        rest += s[i]; i += 1
    return instr, tempo, rest


def build_midi_line(instr, tempo: int, rest: str = "") -> str:
    return "%%I" + (("i" + "".join(instr)) if instr else "") + (f"t{tempo}" if tempo else "") + rest


def split_instr_text(text: str):
    """입력창 글자 -> 코드 목록 (공백/쉼표 구분, 두 글자 소문자만)."""
    return [t for t in re.split(r"[\s,]+", text.strip().lower()) if re.fullmatch(r"[a-z]{2}", t)]


BODY_PLACEHOLDER = "% 아래에 악보 코드를 입력하세요."

# '한글' 체크 시 Extra 에 자동으로 채워지는 기본 블록 (사용자가 수정 가능).
# 엔진(LuaTeX / pdfTeX)에 따른 분기는 컴파일할 때 TeX 가 \ifluatex 로 정한다 — 앱이 줄을 바꿔 끼우지 않는다.
HANGUL_EXTRA = [
    r"%% \input iftex.sty",
    r"%% \ifluatex",
    r"%% \input luatexko.sty",
    r'%% \def\htitlefont{\hangulfont="Noto Sans CJK KR Medium" at 14pt}',
    r'%% \def\hcomposerfont{\hangulfont="Noto Serif CJK KR" at 10pt}',
    r'%% \hangulfont="Noto Sans CJK KR Light" at 10.5pt',
    r"%% \else\ifpdftex",
    r"%% \input kotexplain",
    r"%% \def\htitlefont{\hfont{nanumgtb}{at 14pt}}",
    r"%% \def\hcomposerfont{\hfont{nanumgtm}{at 12pt}}",
    r"%% \hfont{nanumgtm}{at 10pt}",
    r"%% \fi\fi",
]
TITLE_MACRO = "\\htitlefont"
COMPOSER_MACRO = "\\hcomposerfont"
LUATEX_MARK = "% LuaTeX: Active"       # LuaTeX 체크 상태를 파일에 남기는 표지(엔진 선택은 앱이 컴파일 때 쓴다)


def _is_font_input(line: str) -> bool:
    return "luatexko.sty" in line or "kotexplain" in line


def migrate_font_input(options):
    """옛 형식(맨 앞의 `\\input luatexko.sty` / `\\input kotexplain` 한 줄)을 새 형식으로 옮긴다.
    새 형식은 \\ifluatex ... \\else\\ifpdftex ... \\fi\\fi 안에 두 입력 줄이 모두 들어 있다.
    -> (옵션 목록, 옛 줄이 luatexko 였는지 | 옛 줄이 없으면 None)"""
    opts = list(options)
    old = [o for o in opts if _is_font_input(o)]
    if not old:
        return opts, None
    has_if = any("\\ifluatex" in o for o in opts)
    if has_if and any("luatexko.sty" in o for o in opts) and any("kotexplain" in o for o in opts):
        return opts, None                                   # 이미 새 형식
    was_lua = "luatexko.sty" in old[0]
    opts = [o for o in opts if not _is_font_input(o)]
    if has_if:
        out = []
        for o in opts:
            out.append(o)
            if o.replace(" ", "").startswith("%%\\ifluatex"):
                out.append(FONT_INPUT_LUA)
            elif o.replace(" ", "").startswith("%%\\else\\ifpdftex"):
                out.append(FONT_INPUT_PLAIN)
        return out, was_lua
    head = [] if any("iftex.sty" in o for o in opts) else [r"%% \input iftex.sty"]
    return head + [r"%% \ifluatex\input luatexko.sty\else\input kotexplain\fi"] + opts, was_lua


FONT_INPUT_LUA = r"%% \input luatexko.sty"
FONT_INPUT_PLAIN = r"%% \input kotexplain"


def add_hangul_extra(options, luatex: bool = True):
    """Extra 줄 목록에 한글 기본 블록을 넣는다(이미 \\htitlefont 정의가 있으면 옛 형식만 새 형식으로 옮긴다)."""
    if any(r"\def\htitlefont" in o for o in options):
        return migrate_font_input(list(options))[0]
    rest = [o for o in options if o not in HANGUL_EXTRA and not _is_font_input(o)]
    return list(HANGUL_EXTRA) + rest


_TEMPLATE_MARKS = ("htitlefont{", "hcomposerfont{", "\\hangulfont=", "\\hfont{", "iftex.sty",
                   "\\ifluatex", "\\else\\ifpdftex", "\\fi\\fi")


def remove_hangul_extra(options):
    """한글 해제 시: 기본 블록(수정된 줄 포함)을 걷어낸다."""
    return [o for o in options if o not in HANGUL_EXTRA and not _is_font_input(o)
            and not (o.startswith("%%") and any(m in o for m in _TEMPLATE_MARKS))]


def normalize_width(w: str) -> str:
    """Width 는 항상 mm 단위. 숫자만 쓰면 mm 를 붙인다 (190 -> 190mm, 190.5 -> 190.5mm)."""
    w = w.strip()
    if re.fullmatch(r"\d+(?:\.\d+)?", w):
        return w + "mm"
    return w


def default_state() -> Dict:
    return {
        'title': '', 'name': '', 'composer': '', 'poet': '',
        'meter_text': 'C', 'meter_num': 5, 'meter_den': 8,
        'key': 0, 'style': '', 'style_override': '', 'custom_style': '',
        'size': '', 'width': '', 'bars_line': 4, 'indent': 0,
        'space': '', 'systems': '',
        'luatex': False, 'hangul': False, 'midi': False,
        'midi_tempo': 0, 'midi_instr': '', 'midi_rest': '',
        'ragged': False, 'crop': False, 'meter_off': False, 'vocal_beam': False,
        'pmx': '', 'options': [], 'body': '',
    }


def split_header(text: str) -> Tuple[str, str]:
    """-> (header 텍스트(끝의 빈 줄 포함), body 텍스트). starter 와 같은 규칙:
    첫 빈 줄 또는 '%%' 단독 줄에서 헤더가 끝난다."""
    pos = 0
    for line in text.splitlines(keepends=True):
        s = line.strip()
        if not s or s == "%%":
            return text[:pos + len(line)], text[pos + len(line):]
        pos += len(line)
    return text, ""


def parse_header(text: str) -> Tuple[Dict, int]:
    state = default_state()
    header, body = split_header(text)
    body_off = len(header)
    parsed_custom_style_key = None
    expecting_custom_style_def = False   # (starter 에서는 미초기화 버그가 있었음)

    for line in header.splitlines():
        s = line.strip()
        if not s or s == "%%":
            continue
        if s == "% Crop: Active":
            state['crop'] = True
            continue
        if s == "% MeterSig: Off":
            state['meter_off'] = True
            continue
        if s == LUATEX_MARK:
            state['luatex'] = True
            continue
        if _is_font_input(s):
            state['hangul'] = True
            state['options'].append(s)      # Extra 에 그대로 보이게 둔다
            continue

        m = re.match(r"^([a-zA-Z0-9/]+)\s*:\s*(.*)$", s)
        if not m:
            state['options'].append(s)
            continue
        key, val = m.group(1).lower(), m.group(2).strip()

        if key == "disable" and re.search(r"(?i)\bunbeamVocal\b", val):
            # Disable: unbeamVocal  == ‘Vocal Beam’ 체크 (성악 음표를 기본값과 달리 보 연결)
            state['vocal_beam'] = True
            rest = re.sub(r"(?i)\bunbeamVocal\b[,\s]*", "", val).strip(" ,")
            if rest:                                   # 같은 줄의 다른 항목은 Extra 에 보존
                state['options'].append(f"Disable: {rest}")
            continue
        if key == "title":
            state['title'] = val
        elif key == "name":
            state['name'] = val
        elif key == "composer":
            state['composer'] = val
        elif key == "poet":
            state['poet'] = val
        elif key == "meter":
            if val in METERS:
                state['meter_text'] = val
            else:
                p = val.split('/')
                if len(p) == 2 and p[0].isdigit() and p[1].isdigit():
                    state['meter_text'] = "Custom"
                    state['meter_num'], state['meter_den'] = int(p[0]), int(p[1])
                else:
                    state['options'].append(s)
        elif key == "sharps":
            try: state['key'] = int(val)
            except ValueError: state['options'].append(s)
        elif key == "flats":
            try: state['key'] = -int(val)
            except ValueError: state['options'].append(s)
        elif key == "style":
            if val in STYLES:
                state['style'] = val
            else:
                state['style_override'] = val
                state['style'] = "Custom"
                parsed_custom_style_key = val.split()[0].lower() if val.split() else None
                expecting_custom_style_def = True
        elif key == "size":
            if val in SIZES: state['size'] = val
            else: state['options'].append(s)
        elif key == "width":
            state['width'] = val
        elif key == "bars/line":
            try: state['bars_line'] = int(val)
            except ValueError: state['options'].append(s)
        elif key == "indent":
            mm = re.search(r"([\d\.]+)", val)
            if mm:
                try: state['indent'] = int(float(mm.group(1)) * 100)
                except ValueError: pass
            else:
                state['options'].append(s)
        elif key == "space":
            state['space'] = val
        elif key == "systems":
            state['systems'] = val
        elif key == "pmx":
            state['pmx'] = val
        elif key == "tex":
            if val == r"\raggedbottom": state['ragged'] = True
            else: state['options'].append(s)
        else:
            if expecting_custom_style_def:
                state['custom_style'] = s
            elif parsed_custom_style_key and key == parsed_custom_style_key:
                state['custom_style'] = s
            elif not state['custom_style'] and ("voices" in val.lower() or "clefs" in val.lower()):
                state['custom_style'] = s
            else:
                state['options'].append(s)
        if key != "style":
            expecting_custom_style_def = False

    body_lines = body.splitlines()
    midi_lines = [l for l in body_lines if l.startswith("%%I")]
    state['midi'] = bool(midi_lines)
    if midi_lines:
        ins, tp, rest = parse_midi_line(midi_lines[0])
        state['midi_instr'], state['midi_tempo'], state['midi_rest'] = " ".join(ins), tp, rest
    state['body'] = body
    if state['hangul']:                    # 옛 형식 파일: 엔진 상태를 옛 줄에서 읽고 새 형식으로 옮긴다
        state['options'], was_lua = migrate_font_input(state['options'])
        if was_lua is not None:
            state['luatex'] = was_lua
    return state, body_off


def generate_header(st: Dict) -> str:
    """헤더 텍스트(끝에 빈 줄 한 개 포함)."""
    L = []
    title, composer, poet = st['title'].strip(), st['composer'].strip(), st['poet'].strip()
    if st.get('vocal_beam'):   L.append("Disable: unbeamVocal")      # 헤더 맨 앞
    if title:                  L.append(f"Title: {title}")
    if st['name'].strip():     L.append(f"Name: {st['name'].strip()}")
    if composer:               L.append(f"Composer: {composer}")
    if poet:                   L.append(f"Poet: {poet}")

    meter = (f"{st['meter_num']}/{st['meter_den']}"
             if st['meter_text'] == "Custom" else st['meter_text'])
    L.append(f"Meter: {meter}")

    key = st['key']
    if key > 0:   L.append(f"Sharps: {key}")
    elif key < 0: L.append(f"Flats: {abs(key)}")

    style = st['style'].strip()
    override = st['style_override'].strip()
    custom = st['custom_style'].strip()
    if style == "Custom":
        if override:
            L.append(f"Style: {override}")
        elif custom and ":" in custom:
            ex = custom.split(":")[0].strip()
            if ex: L.append(f"Style: {ex}")
        if custom:
            L.append(custom)
    elif style:
        L.append(f"Style: {style}")

    if st['size'].strip():  L.append(f"Size: {st['size'].strip()}")
    if st['width'].strip(): L.append(f"Width: {normalize_width(st['width'])}")

    systems = st['systems'].strip()
    has_systems = bool(systems) and systems != "0"
    if st['bars_line'] > 0 and not has_systems:
        L.append(f"Bars/Line: {st['bars_line']}")
    if st['indent'] > 0:
        L.append(f"Indent: {st['indent'] / 100.0}")
    if st['space'].strip(): L.append(f"Space: {st['space'].strip()}")
    if systems:             L.append(f"Systems: {systems}")
    if st['pmx'].strip():   L.append(f"PMX: {st['pmx'].strip()}")
    if st['ragged']:        L.append(r"TeX: \raggedbottom")
    if st['crop']:          L.append("% Crop: Active")
    if st.get('meter_off'): L.append("% MeterSig: Off")
    if st['luatex']:        L.append(LUATEX_MARK)
    opts = st['options']
    for o in opts:
        if o.strip():
            L.append(o.strip())
    return "\n".join(L) + "\n\n"


def apply_state(text: str, st: Dict) -> str:
    """text 의 헤더를 st 로 교체하고, midi 체크 여부에 맞춰 %%I 줄을 넣거나 뺀다."""
    _, body = split_header(text.replace("\r\n", "\n"))
    body = body.lstrip("\r\n")
    if not body.strip():
        body = BODY_PLACEHOLDER
    lines = body.split("\n")
    idx = next((k for k, l in enumerate(lines) if l.startswith("%%I")), None)
    if st['midi']:
        ins = split_instr_text(st.get('midi_instr', ''))
        want = (ins, int(st.get('midi_tempo') or 0), st.get('midi_rest', ''))
        if idx is None:
            lines.insert(0, build_midi_line(*want))
        elif parse_midi_line(lines[idx]) != want:          # 값이 바뀐 경우에만 그 줄을 다시 쓴다
            lines[idx] = build_midi_line(*want)
    elif idx is not None:
        lines = [l for l in lines if not l.startswith("%%I")]
    body = "\n".join(lines)
    if not body.endswith("\n"):
        body += "\n"
    return generate_header(st) + body


def key_signature_of(text: str) -> int:
    """헤더의 조표(양수=샵 수, 음수=플랫 수). 비주얼 헬퍼에 넘기는 용도."""
    st, _ = parse_header(text)
    return st['key']


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        t = open(p, encoding="utf-8").read().replace("\r\n", "\n")
        st, off = parse_header(t)
        out = apply_state(t, st)
        hdr_in, _ = split_header(t)
        hdr_out, _ = split_header(out)
        print(p, "HEADER ROUND-TRIP:", "OK" if hdr_in == hdr_out else "DIFF")
        if hdr_in != hdr_out:
            print("--- in\n" + hdr_in + "--- out\n" + hdr_out)
        print("   BODY SAME:", split_header(t)[1] == split_header(out)[1])
