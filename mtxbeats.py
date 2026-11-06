#!/usr/bin/env python3
"""
mtxbeats.py - M-Tx 소스의 ‘마디별 박자 합계’ 검사 (GUI 비의존)

컴파일 전에 각 마디의 음표·쉼표 길이를 합해 박자표와 맞는지 본다.
  * 보표(줄)·성부(`//`)별로 센다. 마디선(| || |] :| |: :|:)이 없어도 M-Tx 처럼 박자가 차면 마디가 끝난 것으로 본다. 줄 끝도 마디의 끝.
  * 길이: 0=온, 9=배온, 2=2분, 4=4분, 8=8분, 1=16분, 3=32분, 6=64분; d 점, dd 겹점. 길이를 안 쓰면 직전 길이.
  * 잇단음표 `c4x3 d e` : 묶음 전체가 첫 음의 길이이고, 첫 음 뒤의 (n-1)개 음표 토큰은 세지 않는다.
  * z 화음 구성음은 길이가 없으므로 세지 않는다. `rp` 는 한 마디 쉼표(마디 길이로 침).
  * 박자표: 헤더 `Meter:` 와 본문의 `n/d`, `C`, `C/` 토큰(그 마디부터). 같은 단락 안에서는 한 성부에만 써도 모든 보표에 적용.
  * 예외: 곡의 첫 마디(못갖춘마디)와 마지막 마디는 짧아도 된다. 넘치면 오류.
한계: PMX 의 free beat 처럼 일부러 박자를 안 맞춘 곳도 경고로 나온다(‘그래도 컴파일’).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction
from typing import Dict, List, Optional, Tuple

import mtxheader

_TOKEN_RE = re.compile(r"\S+")
_BARTOK_RE = re.compile(r"^(?:\|\]?|\|\||:\|:?|\|:)$")
_LYRIC_LINE_RE = re.compile(r"^\s*[A-Za-z]:")
_METER_TOK_RE = re.compile(r"^(\d+)/(\d+)$")
_NOTE_RE = re.compile(r"^[({]*([a-gr])([snf=+\-]*)([0-9])?(dd?)?")
_TUP_RE = re.compile(r"x(\d+)")
_GRACE_RE = re.compile(r"^G(\d)?")
_REST_OFFSET_RE = re.compile(r"(?<=^r)[+\-]\d+|(?<=^\(r)[+\-]\d+")

DUR = {"0": Fraction(1), "9": Fraction(2), "2": Fraction(1, 2), "4": Fraction(1, 4), "8": Fraction(1, 8),
       "1": Fraction(1, 16), "3": Fraction(1, 32), "6": Fraction(1, 64)}


def meter_len(tok: str) -> Optional[Fraction]:
    if tok in ("C", "C/"):
        return Fraction(1)
    m = _METER_TOK_RE.match(tok)
    if m and int(m.group(2)) > 0:
        return Fraction(int(m.group(1)), int(m.group(2)))
    return None


def meter_name(f: Fraction) -> str:
    return f"{f.numerator}/{f.denominator}"


@dataclass
class Issue:
    start: int                 # 마디 첫 토큰 오프셋
    end: int                   # 마디 마지막 토큰 끝 오프셋
    line: int                  # 1-기준 줄 번호
    bar: int                   # 그 줄 안의 마디 순번(1-기준)
    staff: int                 # 단락 안의 보표 순번(1-기준)
    voice: int                 # 성부 순번(1-기준)
    got: Fraction
    expected: Fraction
    kind: str                  # 'short' | 'long'

    def describe(self) -> str:
        what = "모자람" if self.kind == "short" else "넘침"
        v = f" 성부 {self.voice}" if self.voice > 1 else ""
        return (f"{self.line}번째 줄 {self.bar}번째 마디{v}: 박자 {what} "
                f"({_fmt(self.got)} / {meter_name(self.expected)})")


def _fmt(f: Fraction) -> str:
    return f"{f.numerator}/{f.denominator}" if f.denominator != 1 else str(f.numerator)


class _Ctx:
    """단락 하나를 검사하는 동안의 공유 상태"""

    def __init__(self, cur_meter: Fraction, changes: Dict[int, Fraction], report: bool):
        self.cur_meter = cur_meter          # 단락 시작 때 적용 중인 박자
        self.changes = changes              # 마디 순번 -> 새 박자 (어느 성부에서든)
        self.report = report
        self.issues: List[Issue] = []
        self.max_ord = 1

    def meter_at(self, ordinal: int) -> Fraction:
        m = self.cur_meter
        for k in sorted(self.changes):
            if k <= ordinal:
                m = self.changes[k]
        return m


def _scan_voice(ctx: _Ctx, line_no: int, staff: int, voice: int, toks: List[Tuple[str, int, int]],
                durs: Dict, first_ok: bool, last_ok: bool) -> None:
    """한 줄의 한 성부. 마디선이 없으면 박자가 찰 때 자동으로 마디가 끝난다(M-Tx 방식)."""
    acc = Fraction(0)
    ordn = 1
    start = end = -1
    notes = 0
    unsure = False
    skip = 0
    grace = 0                    # 꾸밈음 토큰 뒤에서 건너뛸 음표 토큰 수
    pending_complete = False

    span = [0, Fraction(0), -1]      # 마지막 명시 마디선 이후 자동으로 닫힌 마디 수, 그 합계, 첫 음 위치

    def finish(explicit: bool, at_end: bool = False):
        nonlocal acc, ordn, start, end, notes, unsure, pending_complete
        if notes and not unsure:
            exp = ctx.meter_at(ordn)
            if explicit and span[0] == 1 and acc < exp:
                # 마디선 앞에서 한 마디가 자동으로 닫히고 조금 남았다 = 사실은 그 마디가 넘친 것
                if ctx.report:
                    ctx.issues.append(Issue(span[2], end, line_no, ordn - 1, staff, voice, span[1] + acc, exp, "long"))
            elif acc < exp:
                ok = (first_ok and ordn == 1) or (last_ok and at_end)
                if not ok and ctx.report:
                    ctx.issues.append(Issue(start, end, line_no, ordn, staff, voice, acc, exp, "short"))
        if notes:
            if explicit or at_end:
                span[0], span[1], span[2] = 0, Fraction(0), -1
            elif acc >= exp and not unsure:
                if span[0] == 0:
                    span[2] = start
                span[0] += 1
                span[1] += acc
            ordn += 1
            ctx.max_ord = max(ctx.max_ord, ordn)
        elif explicit:
            span[0], span[1], span[2] = 0, Fraction(0), -1
        acc, start, end, notes, unsure, pending_complete = Fraction(0), -1, -1, 0, False, False

    for ti, (tok, a, b) in enumerate(toks):
        if _BARTOK_RE.match(tok):
            skip = 0
            rest_notes = any(_NOTE_RE.match(t) and t[0] != "z" for t, _, _ in toks[ti + 1:])
            finish(True, at_end=not rest_notes)
            continue
        if skip == 0:
            ml = meter_len(tok)
            if ml is not None and notes == 0:
                if not ctx.report:
                    ctx.changes[ordn] = ml
                continue
        gm = _GRACE_RE.match(tok)
        if gm:                                       # G2xx... : n 개의 꾸밈음(첫 음은 토큰 안, 나머지는 뒤 토큰)
            grace = int(gm.group(1) or 1) - 1
            continue
        tok0 = _REST_OFFSET_RE.sub("", tok)           # r-2, r+6 : 쉼표의 세로 위치(길이가 아님)
        nm = _NOTE_RE.match(tok0)
        if tok[0] == "z" or not nm:
            continue
        if grace > 0:
            grace -= 1
            continue
        if start < 0:
            start = a
        end = b
        if skip > 0:
            skip -= 1
        else:
            letter, d, dd = nm.group(1), nm.group(3), nm.group(4)
            if letter == "r" and tok.startswith("rp") and d is None:
                acc += ctx.meter_at(ordn)
                notes += 1
            elif letter == "r" and len(tok) > 1 and tok[1] == "m":
                unsure = True
                notes += 1
            else:
                if d is not None:
                    durs["cur"] = (d, dd)
                dc, dots = durs.get("cur", ("4", None))
                base = DUR.get(dc)
                if base is None:
                    unsure = True
                else:
                    if dots == "d":
                        base = base * Fraction(3, 2)
                    elif dots == "dd":
                        base = base * Fraction(7, 4)
                    acc += base
                notes += 1
                tm = _TUP_RE.search(tok)
                if tm and int(tm.group(1)) > 1:
                    skip = int(tm.group(1)) - 1
        if skip == 0 and notes and not unsure:
            exp = ctx.meter_at(ordn)
            if acc > exp:
                if ctx.report:
                    ctx.issues.append(Issue(start, end, line_no, ordn, staff, voice, acc, exp, "long"))
                acc, start, end, notes, unsure = Fraction(0), -1, -1, 0, False
                ordn += 1                                  # 넘친 마디는 닫고 다음 마디로(연쇄 경고를 줄인다)
            elif acc == exp:
                finish(False)
    finish(False, at_end=True)


def _split_voices(line: str, line_start: int) -> List[List[Tuple[str, int, int]]]:
    voices: List[List[Tuple[str, int, int]]] = [[]]
    for m in _TOKEN_RE.finditer(line):
        tok = m.group(0)
        if tok.startswith("%"):
            break
        if tok == "//":
            voices.append([])
            continue
        voices[-1].append((tok, line_start + m.start(), line_start + m.end()))
    return voices


def check(text: str, header_meter: Optional[Fraction] = None) -> List[Issue]:
    text = text.replace("\r\n", "\n")
    st, off = mtxheader.parse_header(text)
    if header_meter is None:
        mt = st["meter_text"]
        if mt == "Custom":
            header_meter = Fraction(st["meter_num"], st["meter_den"])
        else:
            header_meter = meter_len(mt) or Fraction(1)
    lines = text.split("\n")
    starts = [0]
    for ln in lines[:-1]:
        starts.append(starts[-1] + len(ln) + 1)
    first_line = text[:off].count("\n")
    paras: List[List[int]] = []
    i, n = first_line, len(lines)
    while i < n:
        if not lines[i].strip():
            i += 1
            continue
        j = i
        while j < n and lines[j].strip():
            j += 1
        paras.append(list(range(i, j)))
        i = j
    issues: List[Issue] = []
    cur_meter = header_meter
    durs: Dict[Tuple[int, int], Dict] = {}
    first_done = False
    for pi, para in enumerate(paras):
        music = [k for k in para if not lines[k].lstrip().startswith("%") and not _LYRIC_LINE_RE.match(lines[k])]
        if not music:
            continue
        changes: Dict[int, Fraction] = {}
        for report in (False, True):             # 1차: 박자표 위치 모으기, 2차: 검사
            ctx = _Ctx(cur_meter, changes, report)
            saved = {k: dict(v) for k, v in durs.items()}
            for si, k in enumerate(music):
                for vi, toks in enumerate(_split_voices(lines[k], starts[k])):
                    d = durs.setdefault((si, vi), {})
                    _scan_voice(ctx, k + 1, si + 1, vi + 1, toks, d, first_ok=not first_done,
                                last_ok=(pi == len(paras) - 1))
            if not report:
                durs = saved
        issues += ctx.issues
        if changes:
            cur_meter = changes[max(changes)]
        first_done = True
    issues.sort(key=lambda x: (x.line, x.bar, x.voice))
    return issues


if __name__ == "__main__":
    import sys
    t = open(sys.argv[1], encoding="utf-8").read()
    res = check(t)
    for it in res:
        print(it.describe())
    print(len(res), "개")
