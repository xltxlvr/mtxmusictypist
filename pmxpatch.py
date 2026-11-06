#!/usr/bin/env python3
"""
pmxpatch.py - 생성된 .pmx 에 사용자 패치를 적용/생성한다. GUI 비의존.

  * 읽는 형식: 일반 `diff` (253c253 ...) 와 `diff -u` (@@ -a,b +c,d @@) 둘 다
  * 적용 방식: 줄 번호가 아니라 *바뀔 줄의 내용*으로 위치를 찾는다(적힌 번호에 가장 가까운 일치).
    -> 소스를 고쳐 줄 번호가 밀리거나, 컴파일용 사본에 줄이 끼어도 그대로 적용된다.
  * 일부 항목이 안 맞으면 그 항목만 건너뛰고 (가능한 마디 번호와 함께) 보고한다.
  * 만들기: 원본 pmx 와 수정본의 차이를 `diff -u` 형태(문맥 0줄, 순수 삽입만 앞 1줄)로 저장.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

_BAR_HDR = re.compile(r"^%\s*(?:Paragraph\s+\d+\s+line\s+\d+\s+bar\s+(\d+)|Bar\s+(\d+))\s*$")


@dataclass
class Hunk:
    old_start: int                  # 1-기준 (순수 삽입이면 '이 줄 뒤')
    old: List[str]
    new: List[str]
    insert_only: bool = False       # 일반 diff 의 'a' (문맥 없음)


@dataclass
class PatchResult:
    text: str
    total: int = 0
    applied: int = 0
    messages: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.applied == self.total


# ------------------------------------------------------------------ 파싱
_NORMAL = re.compile(r"^(\d+)(?:,(\d+))?([acd])(\d+)(?:,(\d+))?$")
_UNI = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse_patch(text: str) -> List[Hunk]:
    lines = text.replace("\r\n", "\n").split("\n")
    hunks: List[Hunk] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        m = _NORMAL.match(ln)
        if m:
            start, kind = int(m.group(1)), m.group(3)
            i += 1
            old, new = [], []
            while i < len(lines) and lines[i].startswith("< "):
                old.append(lines[i][2:]); i += 1
            if i < len(lines) and lines[i] == "---":
                i += 1
            while i < len(lines) and lines[i].startswith("> "):
                new.append(lines[i][2:]); i += 1
            hunks.append(Hunk(start, old, new, insert_only=(kind == "a")))
            continue
        m = _UNI.match(ln)
        if m:
            start = int(m.group(1))
            cnt = 1 if m.group(2) is None else int(m.group(2))
            i += 1
            old, new = [], []
            while i < len(lines) and not lines[i].startswith("@@") and not _NORMAL.match(lines[i]):
                l = lines[i]
                if l.startswith("-"):
                    old.append(l[1:])
                elif l.startswith("+"):
                    new.append(l[1:])
                elif l.startswith(" "):
                    old.append(l[1:]); new.append(l[1:])
                elif l.startswith("\\"):
                    pass
                elif l == "":            # 패치 파일 끝의 빈 줄 (문맥 속 빈 줄은 " " 로 시작)
                    i += 1
                    break
                else:
                    break
                i += 1
            hunks.append(Hunk(start, old, new, insert_only=(cnt == 0)))
            continue
        i += 1
    return hunks


# ------------------------------------------------------------------ 적용
def _norm(s: str) -> str:
    return s.strip()


def _find(lines: List[str], block: List[str], expected: int) -> Optional[Tuple[int, bool]]:
    """block 이 일치하는 시작 인덱스 (expected 에 가장 가까운 것). -> (index, 모호한가)"""
    n = len(block)
    if n == 0 or n > len(lines):
        return None
    for key in (lambda s: s.rstrip(), _norm):          # 정확 -> 공백 무시
        kb = [key(b) for b in block]
        cand = [i for i in range(len(lines) - n + 1)
                if all(key(lines[i + k]) == kb[k] for k in range(n))]
        if cand:
            best = min(cand, key=lambda i: abs(i - expected))
            return best, len(cand) > 1
    return None


def bar_of_line(lines: List[str], idx: int) -> Optional[int]:
    """idx 줄이 속한 블록의 첫 마디 번호 (바로 앞의 % Paragraph / %Bar 주석)."""
    for k in range(min(idx, len(lines) - 1), -1, -1):
        m = _BAR_HDR.match(lines[k])
        if m:
            return int(m.group(1) or m.group(2))
    return None


def find_bar_line(pmx_text: str, bar: int) -> Optional[int]:
    """마디 bar 가 들어 있는 블록의 머리 주석 줄 번호(0-기준)."""
    last = None
    for k, ln in enumerate(pmx_text.split("\n")):
        m = _BAR_HDR.match(ln)
        if m:
            b = int(m.group(1) or m.group(2))
            if b <= bar:
                last = k
            else:
                break
    return last


def apply_patch(pmx_text: str, patch_text: str) -> PatchResult:
    hunks = parse_patch(patch_text)
    crlf = "\r\n" in pmx_text
    lines = pmx_text.replace("\r\n", "\n").split("\n")
    res = PatchResult(text=pmx_text, total=len(hunks))
    delta = 0
    for n, h in enumerate(hunks, 1):
        expected = h.old_start - 1 + delta
        if h.insert_only and not h.old:
            # 문맥 없는 순수 삽입: 줄 번호 위치에 넣는다 (신뢰도 낮음)
            at = max(0, min(len(lines), h.old_start + delta))
            lines[at:at] = h.new
            delta += len(h.new)
            res.applied += 1
            res.messages.append(f"항목 {n}: 문맥 없는 삽입을 줄 번호 위치({at + 1})에 넣음 (확인 권장)")
            continue
        if h.old == h.new:
            res.applied += 1
            continue
        found = _find(lines, h.old, expected)
        if not found:
            bar = bar_of_line(lines, max(0, min(expected, len(lines) - 1)))
            first = (h.old[0] if h.old else "").strip()
            res.messages.append(f"항목 {n} 건너뜀: 원본 줄을 찾지 못함"
                                + (f" (마디 {bar} 근처)" if bar is not None else "")
                                + f" — {first[:60]}")
            continue
        idx, ambiguous = found
        if ambiguous:
            res.messages.append(f"항목 {n}: 같은 줄이 여럿이라 {h.old_start}번 줄에 가장 가까운 곳에 적용")
        lines[idx:idx + len(h.old)] = h.new
        delta += len(h.new) - len(h.old)
        res.applied += 1
    out = "\n".join(lines)
    res.text = out.replace("\n", "\r\n") if crlf else out
    return res


# ------------------------------------------------------------------ 만들기
def make_patch(pristine: str, edited: str, a_name="pmx.orig", b_name="pmx") -> str:
    """pristine -> edited 차이를 unified diff 로. 같으면 빈 문자열."""
    a = pristine.replace("\r\n", "\n").split("\n")
    b = edited.replace("\r\n", "\n").split("\n")
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    out: List[str] = []
    delta = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        old, new = a[i1:i2], b[j1:j2]
        start = i1 + 1
        if not old:                       # 순수 삽입: 앞 한 줄을 문맥으로 붙인다
            if i1 == 0:
                old, new, start = [], new, 0        # 맨 앞 삽입: '0번 줄 뒤'
            else:
                old, new, start = [a[i1 - 1]], [a[i1 - 1]] + new, i1
        bar = bar_of_line(a, i1)
        out.append(f"@@ -{start},{len(old)} +{start + delta},{len(new)} @@" + (f" bar {bar}" if bar is not None else ""))
        if old == new[:len(old)] and len(old) == 1 and len(new) > 1 and tag == "insert":
            out.append(" " + old[0])
            out.extend("+" + x for x in new[1:])
        else:
            out.extend("-" + x for x in old)
            out.extend("+" + x for x in new)
        delta += len(new) - len(old)
    if not out:
        return ""
    return f"--- {a_name}\n+++ {b_name}\n" + "\n".join(out) + "\n"
