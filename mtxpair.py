"""슬러 ( ), 타이 { }, 이은 기 [ ] 의 짝 찾기.

M-Tx 에서 이 괄호들은 같은 줄이 아니라 마디·문단을 넘어 다른 줄에서 닫힐 수 있다. 짝은 다음 규칙으로 정한다.
  - 한 문단의 음악 줄(L:/U:/C:/주석/헤더 줄 제외)을 위에서부터 세어 ‘보표 번호’로 삼는다. 문단은 빈 줄로 나뉜다.
  - 한 보표 안에서 `//` 로 나뉜 앞/뒤가 ‘성부’이다. (보표, 성부)마다 괄호를 따로 센다.
  - 괄호 뒤에 붙은 글자는 ID 이거나 추가 명령(u l +n -n)이다. ID(숫자 또는 대문자)가 같은 것끼리 짝이 된다.
    ID 가 없으면 같은 종류의 가장 가까운 열린 괄호와 짝 (겹치면 안쪽부터).
  - 위로 짝이 안 맞은 것은 같은 보표의 다른 성부에서 한 번 더 찾는다 (한 마디만 `//` 일 때 대비).
"""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

_TEXTLINE = re.compile(r"^\s*[A-Za-z][A-Za-z0-9/]*:")
_TOK = re.compile(r"\S+")
_BR = re.compile(r"^([(){}\[\]])(.*)$", re.S)
_KIND = {"(": "(", ")": "(", "{": "{", "}": "{", "[": "[", "]": "["}
_OPEN = "({["


def parse_id(suffix: str) -> str:
    """괄호 뒤 글자에서 ID(숫자·대문자 첫 글자)만 뽑는다. u/l(방향), +n/-n(높이)은 건너뛴다."""
    i, ident = 0, ""
    while i < len(suffix):
        c = suffix[i]
        if c in "+-":
            m = re.match(r"[+-]\d*\.?\d*", suffix[i:])
            i += max(1, len(m.group(0)))
            continue
        if (c.isdigit() or "A" <= c <= "Z") and not ident:
            ident = c
        i += 1
    return ident


@dataclass
class Bracket:
    start: int
    end: int
    char: str
    kind: str
    ident: str
    staff: int
    voice: int
    line: int            # 0-기준 줄 번호
    partner: int = -1    # Bracket 목록 안의 인덱스

    @property
    def is_open(self) -> bool:
        return self.char in _OPEN


class PairInfo:
    def __init__(self, brackets: List[Bracket]):
        self.brackets = brackets
        self._starts = [b.start for b in brackets]

    def at(self, pos: int) -> Optional[int]:
        """커서 위치 pos 가 닿아 있는(앞·뒤·안) 괄호의 인덱스."""
        i = bisect.bisect_right(self._starts, pos) - 1
        for j in (i, i - 1):
            if 0 <= j < len(self.brackets):
                b = self.brackets[j]
                if b.start <= pos <= b.end:
                    return j
        return None

    def unmatched(self) -> List[int]:
        return [i for i, b in enumerate(self.brackets) if b.partner < 0]


def find_pairs(text: str) -> PairInfo:
    brackets: List[Bracket] = []
    staff = -1
    in_par = False
    off = 0
    for ln, line in enumerate(text.split("\n")):
        base = off
        off += len(line) + 1
        if not line.strip():
            in_par, staff = False, -1
            continue
        if line.lstrip().startswith("%") or _TEXTLINE.match(line):
            continue
        staff += 1
        voice = 0
        for m in _TOK.finditer(line):
            tok = m.group(0)
            if tok.startswith("//"):
                voice += 1
                continue
            bm = _BR.match(tok)
            if not bm:
                continue
            ch = bm.group(1)
            brackets.append(Bracket(base + m.start(), base + m.start() + 1 + len(bm.group(2)), ch,
                                    _KIND[ch], parse_id(bm.group(2)), staff, voice, ln))

    def run(keyf, items):
        stacks: Dict[tuple, List[int]] = {}
        for i in items:
            b = brackets[i]
            k = keyf(b)
            if b.is_open:
                stacks.setdefault(k, []).append(i)
            elif stacks.get(k):
                j = stacks[k].pop()
                brackets[i].partner, brackets[j].partner = j, i

    order = list(range(len(brackets)))
    run(lambda b: (b.staff, b.voice, b.kind, b.ident), order)
    # 1차에서 못 맞춘 것: 같은 보표의 다른 성부에서
    run(lambda b: (b.staff, b.kind, b.ident), [i for i in order if brackets[i].partner < 0])
    return PairInfo(brackets)
