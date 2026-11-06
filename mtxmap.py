#!/usr/bin/env python3
"""
mtxmap.py - M-Tx 소스 <-> PMX 마디 대응표 (GUI 비의존 핵심 모듈)

M-Tx 가 만든 .pmx 에는 다음 주석이 남는다.
    % Paragraph N line L bar B     (단락의 첫 줄 번호 L, 첫 마디 번호 B)
    %Bar K                          (단락 안의 다음 마디)
이 정보와 "음표 토큰 개수"를 이용해, 마디 번호 -> mtx 소스 구간을 구한다.

규칙(샘플 3종으로 확인)
  * mtx 단락 = 빈 줄로 구분된 블록. L 은 블록 첫 줄(주석 줄 포함).
  * mtx 의 각 음악 줄 = 한 보표. 보표 안에서 `//` 가 성부를 나눈다.
  * pmx 마디 안에서는 `/` 가 보표를, `//` 가 성부를 나눈다.
    pmx 는 한 마디 안에서 보표를 mtx 줄 순서의 *역순*으로 쓴다.
  * M-Tx 가 토큰을 끼워 넣거나 바꾸므로(\\mtxBM\\, Rd, 4/4 -> m4/4/4/4 ...)
    '음표 토큰'(a-g, r, z+음이름 으로 시작)만 센다.
  * 보표/성부별 누적 개수로 mtx 음표 토큰을 마디별로 잘라낸다.
  * 개수 또는 음이름 첫 글자가 안 맞으면 그 단락은 '줄 단위 근사'로 떨어뜨린다.
"""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

Seg = Tuple[int, int]  # (start, end) 문자 오프셋, end 는 배타적

# ---------------------------------------------------------------- 토큰화 유틸

_TOKEN_RE = re.compile(r"\S+")
# \\\mtxFoo{..}{..}\  /  \mtxBM\  /  \mtxCchar{16}{\bf{Adagio.}}\  (중괄호 3단계 중첩까지)
_MACRO_RE = re.compile(
    r"\\+mtx[A-Za-z]+"
    r"(?:\{[^{}]*(?:\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}[^{}]*)*\})*"
    r"\\?"
)
_BARTOK_RE = re.compile(r"^(?:\|\]?|\|\||:\|:?|\|:)$")   # | || |] :| |: :|:
_LYRIC_LINE_RE = re.compile(r"^\s*[A-Za-z]:")  # L: U: 등 한 글자 접두 줄
_PARA_RE = re.compile(r"^%\s*Paragraph\s+(\d+)\s+line\s+(\d+)\s+bar\s+(\d+)")
_BAR_RE = re.compile(r"^%\s*Bar\s+(\d+)")


_NON_BMP = re.compile("[\U00010000-\U0010ffff]")


def qt_safe(text: str) -> str:
    """Qt 문서 위치(UTF-16 단위)와 파이썬 문자열 인덱스를 일치시키기 위해
    BMP 밖 문자를 두 글자 자리표시자로 바꾼다. (음표 토큰에는 영향 없음)"""
    return _NON_BMP.sub("xx", text.replace("\r\n", "\n"))


def is_note_token(tok: str) -> bool:
    """음표/쉼표 토큰인가? (a-g, r, 또는 z+음이름)"""
    if not tok:
        return False
    c = tok[0]
    if c in "abcdefgr":
        return True
    if c == "z" and len(tok) > 1 and tok[1] in "abcdefg":
        return True
    return False


def note_key(tok: str) -> str:
    """M-Tx 가 철자를 바꿔도 유지되는 부분: 음이름(또는 r). z 는 z+음이름."""
    return tok[:2] if tok[0] == "z" else tok[0]


# ---------------------------------------------------------------- 데이터

@dataclass
class BarInfo:
    number: int
    paragraph_line: int                 # 단락 첫 줄(1-기준)
    segments: List[Seg] = field(default_factory=list)  # (보표,성부)별 소스 구간
    exact: bool = True                  # False 면 줄 단위 근사
    fallback_seg: Optional[Seg] = None  # 근사일 때 단락 전체 구간

    @property
    def start(self) -> int:
        if self.segments:
            return min(s for s, _ in self.segments)
        return self.fallback_seg[0] if self.fallback_seg else 0

    @property
    def end(self) -> int:
        if self.segments:
            return max(e for _, e in self.segments)
        return self.fallback_seg[1] if self.fallback_seg else 0

    def spans(self) -> List[Seg]:
        return self.segments if self.segments else (
            [self.fallback_seg] if self.fallback_seg else [])


@dataclass
class _MtxPara:
    first_line: int                      # 1-기준
    start: int
    end: int
    # staves[s][v] = [(tok, start, end), ...]
    staves: List[List[List[Tuple[str, int, int, int]]]]


class BarMap:
    def __init__(self) -> None:
        self.bars: Dict[int, BarInfo] = {}
        self.warnings: List[str] = []
        self._order: List[int] = []          # 마디 번호 (pmx 등장 순)
        self._starts: List[int] = []         # 정렬된 (start, bar) 검색용
        self._start_bars: List[int] = []

    # --------------------------------------------------------- 질의
    @property
    def order(self) -> List[int]:
        """pmx 에 나온 순서의 마디 번호들"""
        return list(self._order)

    def has(self, bar: int) -> bool:
        return bar in self.bars

    def spans_for_bar(self, bar: int) -> List[Seg]:
        b = self.bars.get(bar)
        return b.spans() if b else []

    def offset_for_bar(self, bar: int) -> Optional[int]:
        b = self.bars.get(bar)
        return b.start if b else None

    def bar_at_offset(self, off: int) -> Optional[int]:
        """소스 오프셋 -> 마디 번호. 구간 안이면 그 마디, 아니면 직전 마디."""
        best = None
        for n in self._order:
            for s, e in self.bars[n].spans():
                if s <= off < e:
                    return n
        i = bisect.bisect_right(self._starts, off) - 1
        if i >= 0:
            best = self._start_bars[i]
        return best

    # --------------------------------------------------------- 편집 추적
    def apply_edit(self, pos: int, removed: int, added: int) -> None:
        """컴파일 이후 편집(QTextDocument.contentsChange 와 같은 의미)을 반영해
        오프셋을 밀어 준다. 오프셋은 Qt(UTF-16) 단위여야 하므로 qt_safe() 로
        만든 텍스트로 대응표를 만든다."""
        def sh(seg: Optional[Seg]) -> Optional[Seg]:
            if seg is None:
                return None
            s, e = seg
            d = added - removed
            if pos >= e:
                return seg
            if pos + removed <= s:
                return (s + d, e + d)
            if pos <= s:                       # 앞쪽이 겹침
                s = pos
            return (s, max(s, e + d))
        for b in self.bars.values():
            b.segments = [sh(sg) for sg in b.segments]
            b.fallback_seg = sh(b.fallback_seg)
        self._finish()

    def _finish(self) -> None:
        pairs = sorted((self.bars[n].start, n) for n in self._order)
        self._starts = [p[0] for p in pairs]
        self._start_bars = [p[1] for p in pairs]


# ---------------------------------------------------------------- mtx 파싱

def _line_starts(text: str) -> List[int]:
    starts = [0]
    for m in re.finditer(r"\n", text):
        starts.append(m.end())
    return starts


def parse_mtx(text: str) -> Dict[int, _MtxPara]:
    """빈 줄로 단락을 나누고, 음악 줄 -> 보표/성부/음표 토큰 오프셋을 구한다."""
    lines = text.split("\n")
    starts = _line_starts(text)
    paras: Dict[int, _MtxPara] = {}
    i = 0
    n = len(lines)
    while i < n:
        if not lines[i].strip():
            i += 1
            continue
        j = i
        while j < n and lines[j].strip():
            j += 1
        # 단락 = lines[i:j]
        staves: List[List[List[Tuple[str, int, int, int]]]] = []
        for k in range(i, j):
            ln = lines[k]
            if ln.lstrip().startswith("%") or _LYRIC_LINE_RE.match(ln):
                continue
            # 헤더 단락(Key: value 형태)은 음악 줄이 아님 -> 빈 staves 로 남는다
            voices: List[List[Tuple[str, int, int, int]]] = [[]]
            marks = [0]            # 성부별: 지금까지 본 명시 마디선 개수
            for m in _TOKEN_RE.finditer(ln):
                tok = m.group(0)
                if tok.startswith("%"):
                    break  # 줄 안 주석
                if tok == "//":
                    voices.append([])
                    marks.append(0)
                    continue
                if _BARTOK_RE.match(tok):
                    marks[-1] += 1
                    continue
                if is_note_token(tok):
                    base = starts[k]
                    voices[-1].append((tok, base + m.start(), base + m.end(), marks[-1]))
            staves.append(voices)
        s0 = starts[i]
        e0 = starts[j] if j < n else len(text)
        paras[i + 1] = _MtxPara(i + 1, s0, e0, staves)
        i = j
    return paras


# ---------------------------------------------------------------- pmx 파싱

def parse_pmx(text: str):
    """-> [(paragraph_line, [(bar_no, [staff_voice_tokens...]) ...]) ...]
    staff_voice_tokens: List[staff][voice] = [note tokens]  (pmx 쓰는 순서 그대로)
    """
    out = []
    cur_para = None
    cur_bar = None
    buf: List[str] = []

    def flush():
        nonlocal cur_bar, buf
        if cur_para is not None and cur_bar is not None:
            cur_para[1].append((cur_bar, _split_pmx_bar(buf)))
        buf = []

    for raw in text.split("\n"):
        line = raw.rstrip("\r")
        m = _PARA_RE.match(line)
        if m:
            flush()
            cur_para = (int(m.group(2)), [])
            out.append(cur_para)
            cur_bar = int(m.group(3))
            continue
        m = _BAR_RE.match(line)
        if m and cur_para is not None:
            flush()
            cur_bar = int(m.group(1))
            continue
        if cur_para is None:
            continue
        if line.startswith("%"):
            if line.startswith("% Coded by"):
                flush()
                cur_bar = None
            continue
        buf.append(line)
    flush()
    return out


def _split_pmx_bar(lines: List[str]):
    """pmx 한 블록 -> (nb, 보표 -> 성부 -> 구간 리스트) 또는 None(비어 있음).
    한 블록에 `|` 로 나뉜 여러 마디가 들어갈 수 있다 (못갖춘마디 0 + 마디 1 등).
    단, M-Tx 가 어떤 보표에서 `|` 를 지워 버리는 경우도 있어(구간 수가 nb 보다 적음)
    그때는 mtx 쪽의 명시적 `|` 로 나눈다."""
    body = _MACRO_RE.sub(" ", "\n".join(lines))
    staves: List[List[List[List[str]]]] = []
    voices: List[List[List[str]]] = [[[]]]

    def has_notes(vs):
        return any(seg for v in vs for seg in v)
    for tok in body.split():
        if tok == "/":
            staves.append(voices)
            voices = [[[]]]
        elif tok == "//":
            voices.append([[]])
        elif tok == "|":
            voices[-1].append([])
        elif is_note_token(tok):
            voices[-1][-1].append(tok)
    if has_notes(voices):
        staves.append(voices)
    cleaned = [[[seg for seg in v if seg] for v in vs] for vs in staves]
    nb = max((len(v) for vs in cleaned for v in vs), default=0)
    if nb == 0:
        return None
    return nb, cleaned


# ---------------------------------------------------------------- 대응표 생성

def build_barmap(mtx_text: str, pmx_text: str, line_offset: int = 0) -> BarMap:
    """line_offset: 컴파일용 사본에 줄을 끼워 넣었을 때 pmx 의 L 에서 빼 줄 값."""
    bm = BarMap()
    mtx_text = mtx_text.replace("\r\n", "\n")
    paras = parse_mtx(mtx_text)
    pmx_paras = parse_pmx(pmx_text)

    for pi, (line_no, bars) in enumerate(pmx_paras):
        # pmx 는 `|` 없이 여러 마디를 한 블록에 담을 수 있다(예: 못갖춘마디 + 1마디).
        # 다음 라벨 번호와의 차이가 블록의 마디 수보다 크면 그 차이만큼으로 본다.
        nxt_first = None
        for _, ob in pmx_paras[pi + 1:]:
            if ob:
                nxt_first = ob[0][0]
                break
        fixed = []
        for i, (bno, blk) in enumerate(bars):
            if blk is not None:
                nxt = bars[i + 1][0] if i + 1 < len(bars) else nxt_first
                if nxt is not None and nxt - bno > blk[0]:
                    blk = (nxt - bno, blk[1])
            fixed.append((bno, blk))
        bars = fixed
        line_no -= line_offset if line_no > line_offset else 0
        mp = paras.get(line_no)
        if mp is None:
            bm.warnings.append(f"pmx 단락(line {line_no})에 해당하는 mtx 단락이 없음")
            continue
        segs_by_bar, why = _slice_paragraph(mp, bars)
        if segs_by_bar is None:
            bm.warnings.append(f"line {line_no}: 줄 단위 근사로 대체 ({why})")
            for bno, blk in bars:
                for j in range(blk[0] if blk else 1):
                    info = BarInfo(bno + j, line_no, [], False, (mp.start, mp.end))
                    bm.bars[bno + j] = info
                    bm._order.append(bno + j)
        else:
            for bno, segs in segs_by_bar:
                info = BarInfo(bno, line_no, segs, True)
                bm.bars[bno] = info
                bm._order.append(bno)
    bm._finish()
    return bm


def _slice_paragraph(mp: _MtxPara, blocks):
    n_staff = len(mp.staves)
    if n_staff == 0:
        return None, "음악 줄 없음"
    ptr = [[0] * len(v) for v in mp.staves]
    result = []
    for first_bar, blk in blocks:
        if blk is None:
            continue
        nb, pstaves = blk
        if len(pstaves) != n_staff:
            return None, f"마디 {first_bar}: pmx 보표 {len(pstaves)}개 / mtx 줄 {n_staff}개"
        bar_segs: List[List[Seg]] = [[] for _ in range(nb)]
        for idx, pvoices in enumerate(pstaves):
            s = n_staff - 1 - idx              # 역순 대응
            mvoices = mp.staves[s]
            if len(pvoices) != len(mvoices):
                return None, f"마디 {first_bar}: 성부 수 불일치({len(pvoices)}/{len(mvoices)})"
            for v, psegs in enumerate(pvoices):
                total = sum(len(x) for x in psegs)
                if total == 0:
                    continue
                a0 = ptr[s][v]
                chunk = mvoices[v][a0:a0 + total]
                if len(chunk) < total:
                    return None, f"마디 {first_bar}: mtx 음표 부족"
                ptoks = [t for seg in psegs for t in seg]
                for (mt, _, _, _), pt in zip(chunk, ptoks):
                    if note_key(mt) != note_key(pt):
                        return None, f"마디 {first_bar}: 음 불일치 {mt!r} vs {pt!r}"
                ptr[s][v] = a0 + total
                if len(psegs) == nb:                       # pmx 의 `|` 를 믿는다
                    cuts, pos = [], 0
                    for seg in psegs:
                        cuts.append(chunk[pos:pos + len(seg)])
                        pos += len(seg)
                else:                                       # mtx 의 명시적 `|` 로 나눈다
                    groups: List[list] = []
                    last = None
                    for note in chunk:
                        if note[3] != last:
                            groups.append([])
                            last = note[3]
                        groups[-1].append(note)
                    if len(groups) != nb:
                        return None, (f"마디 {first_bar}: pmx 가 `|` 를 지웠고 mtx 에도 "
                                      f"명시적 마디선이 부족함({len(groups)}/{nb})")
                    cuts = groups
                for j, notes in enumerate(cuts):
                    if notes:
                        bar_segs[j].append((notes[0][1], notes[-1][2]))
        for j in range(nb):
            result.append((first_bar + j, bar_segs[j]))
    for s in range(n_staff):
        for v in range(len(mp.staves[s])):
            if ptr[s][v] != len(mp.staves[s][v]):
                return None, "mtx 음표가 남음(개수 불일치)"
    return result, ""


# ---------------------------------------------------------------- CLI 자가 점검

if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        print("usage: mtxmap.py file.mtx file.pmx")
        raise SystemExit(2)
    mt = open(sys.argv[1], encoding="utf-8").read().replace("\r\n", "\n")
    px = open(sys.argv[2], encoding="utf-8").read()
    bmap = build_barmap(mt, px)
    for n in bmap._order:
        b = bmap.bars[n]
        tag = "OK " if b.exact else "~~ "
        txt = " | ".join(mt[s:e].replace("\n", "⏎")[:40] for s, e in b.spans())
        print(f"{tag}bar {n:>3}  L{b.paragraph_line:<3} {txt}")
    for w in bmap.warnings:
        print("WARN", w)
