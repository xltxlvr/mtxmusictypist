#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mtxmidi.py — 표준 MIDI 파일(.mid)을 M-Tx 악보(.mtx)로 옮기는 초안 변환기.

목표: ‘간단한 선율 위주’의 MIDI(악보 프로그램/LilyPond 가 내보낸 것, 또는 규칙적으로 입력한 것)를
      한 트랙 = 한 보표로 옮겨서 손질할 수 있는 M-Tx 초안을 만든다.  완벽한 채보 도구가 아니다.

하는 일
  * 박자(첫 time_signature)·조표(첫 key_signature, 없으면 음 분포로 추정)·템포·악기(program) 읽기
  * 음 시작/끝을 격자(기본 32분음표)로 양자화, 겹침 정리(앞 음을 자름), 너무 짧은 음(장식음 등) 버림
  * 한 트랙에서 동시에 울리는 음은 가장 높은 음만 남김(화음·다성은 아직 지원하지 않음)
  * 마디 경계에서 음을 나누고 타이로 잇기, 박자 구조에 맞게 음 길이 분해, 쉼표 채우기
  * 조표에 맞춘 음 이름(이명동음) 결정, M-Tx 의 ‘인쇄되는 대로’ 임시표, 상대 음높이(+ - =) 표기
  * 다성부 헤더(Style/Voices/Clefs/Name)와 %%I(MIDI 악기·템포) 생성

사용:  python3 mtxmidi.py 곡.mid [-o 곡.mtx] [-t 1,2,9] [--bars 4] [--grid 32] [--pickup 0.5]
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Dict, List, Optional, Tuple

import mido

LETTERS = "cdefgab"
NAT_PC = [0, 2, 4, 5, 7, 9, 11]
LOF_LETTERS = "FCGDAEB"            # 5도권 -1..5 에 해당하는 글자

# ---------------------------------------------------------------- 조표
_MAJOR = {"C": 0, "G": 1, "D": 2, "A": 3, "E": 4, "B": 5, "F#": 6, "C#": 7,
          "F": -1, "Bb": -2, "Eb": -3, "Ab": -4, "Db": -5, "Gb": -6, "Cb": -7}
_MINOR = {"A": 0, "E": 1, "B": 2, "F#": 3, "C#": 4, "G#": 5, "D#": 6, "A#": 7,
          "D": -1, "G": -2, "C": -3, "F": -4, "Bb": -5, "Eb": -6, "Ab": -7}


def key_to_fifths(name: str) -> Optional[int]:
    if name.endswith("m"):
        return _MINOR.get(name[:-1])
    return _MAJOR.get(name)


def lof_spelling(i: int) -> Tuple[int, int]:
    """5도권 위치 i -> (글자 번호 0=c.., 변화음 -2..2)"""
    letter = LOF_LETTERS[(i + 1) % 7].lower()
    return LETTERS.index(letter), (i + 1) // 7


def lof_pc(i: int) -> int:
    return (7 * i) % 12


def estimate_key(pitches: List[int]) -> int:
    """음 분포로 장조 기준 조표 개수를 추정(단조도 같은 조표로 잡힌다)."""
    best, best_score = 0, -1.0
    for f in range(-7, 8):
        scale = {lof_pc(i) for i in range(f - 1, f + 6)}
        score = sum(1 for p in pitches if p % 12 in scale) - 0.01 * abs(f)
        if score > best_score:
            best, best_score = f, score
    return best


# ---------------------------------------------------------------- 음 이름 결정
def spell(pc: int, fifths: int, hint: int = 0) -> Tuple[int, int]:
    """pc(0-11) -> (글자 번호, 변화음).  hint: +1 (반음 위로 간다) / -1 (반음 아래로 간다) / 0"""
    diat = [i for i in range(fifths - 1, fifths + 6) if lof_pc(i) == pc]
    if diat:
        return lof_spelling(diat[0])
    def exotic(i: int) -> bool:           # Cb, Fb, E#, B# 는 조표가 그쪽(±5 이상)일 때만 쓴다
        L, a = lof_spelling(i)
        if (LETTERS[L], a) in (("c", -1), ("f", -1)):
            return fifths > -5
        if (LETTERS[L], a) in (("e", 1), ("b", 1)):
            return fifths < 5
        return False
    cands = [i for i in range(fifths - 9, fifths + 13)
             if lof_pc(i) == pc and abs(lof_spelling(i)[1]) <= 1 and not exotic(i)]
    cands.sort()
    if not cands:
        cands = [i for i in range(-15, 22) if lof_pc(i) == pc]
        cands.sort(key=lambda i: abs(i - fifths - 2))
        return lof_spelling(cands[0])
    if hint > 0:
        return lof_spelling(cands[-1])
    if hint < 0:
        return lof_spelling(cands[0])
    centre = fifths + 2
    cands.sort(key=lambda i: (abs(i - centre), -i))
    return lof_spelling(cands[0])


# ---------------------------------------------------------------- 길이
@dataclass
class Meter:
    num: int = 4
    den: int = 4

    def bar_units(self, G: int) -> int:
        return self.num * G // self.den

    def beat_units(self, G: int) -> int:
        if self.den >= 8 and self.num % 3 == 0 and self.num > 3:
            return 3 * G // self.den
        return G // self.den


def duration_table(G: int) -> Dict[int, str]:
    """격자 단위 길이 -> M-Tx 길이 기호 (점음표 포함)"""
    base = {Fraction(2): "9", Fraction(1): "0", Fraction(1, 2): "2", Fraction(1, 4): "4",
            Fraction(1, 8): "8", Fraction(1, 16): "1", Fraction(1, 32): "3", Fraction(1, 64): "6"}
    out: Dict[int, str] = {}
    for fr, sym in base.items():
        u = fr * G
        if u.denominator == 1:
            out[int(u)] = sym
        if sym != "9":
            ud = u * Fraction(3, 2)
            if ud.denominator == 1:
                out[int(ud)] = sym + "d"
    return out


def split_span(p: int, d: int, meter: Meter, G: int, table: Dict[int, str]) -> List[int]:
    """마디 안의 위치 p(격자 단위)에서 길이 d 를 박자 구조에 맞는 단일 음표 길이들로 나눈다."""
    beat = meter.beat_units(G)
    bar = meter.bar_units(G)
    nbeats = bar // beat if beat else 1
    mid = bar // 2 if (nbeats >= 4 and nbeats % 2 == 0) else None
    lens = sorted(table, reverse=True)
    out: List[int] = []
    while d > 0:
        chosen = 0
        for L in lens:
            if L > d:
                continue
            end = p + L
            inside = [b for b in range(((p // beat) + 1) * beat, end, beat) if p < b < end]
            if not inside:
                chosen = L
                break
            if p % beat == 0:
                if mid is not None and p not in (0, mid) and p < mid < end:
                    continue
                chosen = L
                break
        if not chosen:                                  # 최후 수단: 가장 큰 기호로 쪼갠다
            chosen = max((L for L in lens if L <= d), default=1)
        out.append(chosen)
        p += chosen
        d -= chosen
    return out


# ---------------------------------------------------------------- MIDI 읽기
GM_NAMES = ["Piano", "Piano", "Piano", "Piano", "Rhodes", "Piano", "Harpsichord", "Clavinet"]

# GM program -> PMX %%I 악기 코드 (mtxheader.MIDI_INSTRUMENTS 의 26개 중 가장 가까운 것)
def pmx_code(program: int) -> str:
    if program <= 3: return "pi"
    if program <= 5: return "rh"
    if program == 6: return "ha"
    if program == 7: return "ct"
    if 8 <= program <= 15: return "ma"
    if 16 <= program <= 20: return "or"
    if program == 21 or program == 22 or program == 23: return "or"
    if 24 <= program <= 31: return "gu"
    if 32 <= program <= 39: return "ab"
    if program == 40: return "vl"
    if program == 41: return "va"
    if program == 42: return "vc"
    if program == 43: return "cb"
    if 44 <= program <= 47: return "vl"
    if 48 <= program <= 51: return "vl"
    if 52 <= program <= 55: return "vo"
    if program == 56: return "tr"
    if program == 57: return "tb"
    if program == 58: return "tu"
    if 59 <= program <= 60: return "fr" if program == 60 else "tr"
    if 61 <= program <= 63: return "fr"
    if program == 64: return "so"
    if program == 65: return "al"
    if program == 66: return "te"
    if program == 67: return "bs"
    if program == 68 or program == 69: return "ob"
    if program == 70: return "ba"
    if program == 71: return "cl"
    if 72 <= program <= 79: return "fl" if program in (72, 73, 75, 76) else "re"
    return "pi"


GM_FAMILY = ["Piano", "Chromperc", "Organ", "Guitar", "Bass", "Strings", "Ensemble", "Brass",
             "Reed", "Pipe", "Lead", "Pad", "FX", "Ethnic", "Perc", "SFX"]
GM_SHORT = {40: "Violin", 41: "Viola", 42: "Cello", 43: "Bass", 56: "Trumpet", 57: "Trombone", 58: "Tuba",
            60: "Horn", 64: "SaxS", 65: "SaxA", 66: "SaxT", 67: "SaxB", 68: "Oboe", 70: "Bassoon",
            71: "Clarinet", 73: "Flute", 74: "Recorder", 52: "Choir", 53: "Voice", 54: "Voice", 0: "Piano",
            6: "Harpsichord", 19: "Organ", 24: "Guitar"}


@dataclass
class Track:
    index: int
    name: str = ""
    program: int = 0
    channel: int = -1
    notes: List[Tuple[int, int, int]] = field(default_factory=list)      # (start, end, pitch) tick
    lyrics: List[Tuple[int, str]] = field(default_factory=list)
    drum: bool = False


@dataclass
class Song:
    tpq: int = 480
    meter: Meter = field(default_factory=Meter)
    meters: List[Tuple[int, int, int]] = field(default_factory=list)       # (tick, 분자, 분모)
    fifths: Optional[int] = None
    tempo_us: int = 500000
    title: str = ""
    tracks: List[Track] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def read_midi(path: str) -> Song:
    mid = mido.MidiFile(path)
    song = Song(tpq=mid.ticks_per_beat)
    got_meter = got_tempo = False
    for ti, tr in enumerate(mid.tracks):
        t = Track(index=ti)
        T = 0
        on: Dict[Tuple[int, int], List[int]] = {}
        for msg in tr:
            T += msg.time
            if msg.type == "track_name":
                t.name = msg.name.strip()
            elif msg.type == "instrument_name" and not t.name:
                t.name = msg.name.strip()
            elif msg.type == "time_signature":
                if not any(m[0] == T for m in song.meters):
                    song.meters.append((T, msg.numerator, msg.denominator))
            elif msg.type == "set_tempo" and not got_tempo:
                song.tempo_us = msg.tempo
                got_tempo = True
            elif msg.type == "key_signature" and song.fifths is None:
                song.fifths = key_to_fifths(msg.key)
            elif msg.type == "program_change" and t.channel < 0:
                t.program = msg.program
                t.channel = msg.channel
            elif msg.type == "lyrics":
                t.lyrics.append((T, msg.text))
            elif msg.type == "note_on" and msg.velocity > 0:
                if t.channel < 0:
                    t.channel = msg.channel
                on.setdefault((msg.channel, msg.note), []).append(T)
            elif msg.type in ("note_off", "note_on"):
                lst = on.get((msg.channel, msg.note))
                if lst:
                    s = lst.pop(0)
                    t.notes.append((s, T, msg.note))
        t.notes.sort()
        song.tracks.append(t)
    song.meters.sort()
    if not song.meters or song.meters[0][0] != 0:
        song.meters.insert(0, (0, 4, 4))
    # 같은 박자가 연달아 나오면 하나로
    dedup = []
    for m in song.meters:
        if not dedup or dedup[-1][1:] != m[1:]:
            dedup.append(m)
    song.meters = dedup
    song.meter = Meter(song.meters[0][1], song.meters[0][2])
    if song.tracks and not song.title:
        song.title = song.tracks[0].name
    return song


# ---------------------------------------------------------------- 드럼(채널 10) 배치
# GM 드럼 번호 -> 오선 위치(G 음자리표의 자연음 높이). 위치만 정할 뿐 소리를 내는 높이가 아니다.
DRUM_LOW = {35: 65, 36: 65, 41: 69, 43: 69, 45: 69, 38: 72, 40: 72, 37: 72, 39: 72,
            47: 76, 48: 76, 50: 76}
DRUM_HIGH = {42: 79, 44: 79, 46: 79, 51: 77, 59: 77, 53: 77, 56: 77,
             49: 81, 52: 81, 55: 81, 57: 81}


def split_drums(t: "Track", tpq: int, warnings: List[str]) -> List["Track"]:
    """드럼 트랙 -> 보표 2개(심벌·하이햇 / 북·스네어·톰). 소리 하나는 다음 타격(최대 4분음표)까지 이어 쓴다."""
    groups = [("Hi-hat" if t.name == "" or "hi" in t.name.lower() else t.name + "~hi", DRUM_HIGH),
              (t.name or "Drums", DRUM_LOW)]
    if t.name and "hi" not in t.name.lower():
        groups[0] = (t.name + "~up", DRUM_HIGH)
        groups[1] = (t.name + "~low", DRUM_LOW)
    out = []
    unknown = set()
    cap = max(e for s0, e, p0 in t.notes)
    for gname, table in groups:
        hits = sorted((s, table[p]) for s, e, p in t.notes if p in table)
        if not hits:
            continue
        notes = []
        for i, (s, pos) in enumerate(hits):
            nxt = hits[i + 1][0] if i + 1 < len(hits) else s + tpq
            while i + 1 < len(hits) and nxt == s:           # 같은 순간의 다른 타격은 다음 것으로
                i += 1
                nxt = hits[i + 1][0] if i + 1 < len(hits) else s + tpq
            notes.append((s, min(nxt, s + tpq, cap), pos))
        v = Track(index=t.index, name=gname, program=0, channel=9, notes=notes, drum=True)
        out.append(v)
    for s, e, p in t.notes:
        if p not in DRUM_LOW and p not in DRUM_HIGH:
            unknown.add(p)
    if unknown:
        warnings.append(f"트랙 {t.index}: 배치표에 없는 드럼 번호 {sorted(unknown)} 는 버렸습니다")
    return out


# ---------------------------------------------------------------- 양자화
def absorb_gaps(notes: List[Tuple[int, int, int]], tpq: int, ratio: float, stats: Dict[str, int]):
    """연주 습관으로 생긴 틈(떼는 시점이 조금 이른 것)은 쉼표가 아니라 레가토로 본다.
    음이 다음 음 시작까지의 간격(IOI)의 ratio 이상 울렸고 틈이 8분음표 미만이면 다음 음 시작까지 늘린다."""
    if ratio <= 0:
        return notes
    ns = sorted(notes)
    out = list(ns)
    for i, (s, e, p) in enumerate(ns):
        nxt = next((x for x in ns[i + 1:] if x[0] > s), None)
        if nxt is None:
            continue
        gap = nxt[0] - e
        ioi = nxt[0] - s
        if 0 < gap < tpq / 2 and ioi > 0 and (e - s) / ioi >= ratio:
            out[i] = (s, nxt[0], p)
            stats["legato_gaps"] = stats.get("legato_gaps", 0) + 1
    return out


def quantize(notes: List[Tuple[int, int, int]], g: float, stats: Dict[str, int],
             tpq: int = 0, legato: float = 0.0, min_rest: int = 0) -> List[Tuple[int, int, int]]:
    """tick -> 격자 단위, 한 번에 한 음(높은 음 우선), 겹치면 앞 음을 자른다."""
    if legato and tpq:
        notes = absorb_gaps(notes, tpq, legato, stats)
    q = []
    for s, e, p in notes:
        qs, qe = int(round(s / g)), int(round(e / g))
        if qe <= qs:
            stats["dropped_short"] = stats.get("dropped_short", 0) + 1
            continue
        q.append((qs, qe, p))
    q.sort(key=lambda x: (x[0], -x[2]))
    if legato and min_rest:
        q2 = list(q)
        for i, (s, e, p) in enumerate(q):
            nxt = next((x for x in q[i + 1:] if x[0] > s), None)
            if nxt is not None and 0 < nxt[0] - e < min_rest:      # 16분쉼표보다 짧은 틈은 잡음으로 본다
                q2[i] = (s, nxt[0], p)
                stats["legato_gaps"] = stats.get("legato_gaps", 0) + 1
        q = q2
    out: List[Tuple[int, int, int]] = []
    for s, e, p in q:
        if out and out[-1][0] == s:                    # 같은 시작 -> 높은 음만(이미 높은 것이 먼저)
            stats["dropped_chord"] = stats.get("dropped_chord", 0) + 1
            continue
        if out and out[-1][1] > s:                     # 겹침 -> 앞 음을 자른다
            ps, pe, pp = out[-1]
            out[-1] = (ps, s, pp)
            stats["overlaps"] = stats.get("overlaps", 0) + 1
        out.append((s, e, p))
    return out


# ---------------------------------------------------------------- 잇단음표(셋잇단·여섯잇단) 양자화
@dataclass
class TupletCfg:
    """박(4분음표) 창마다 2분할 격자(이진)와 3분할 격자(삼진) 중 맞는 쪽으로 시각을 붙인다."""
    unit_ticks: float            # 1 단위 = 몇 tick
    G: int                       # 온음표 하나 = 몇 단위 (96 등)
    bs: int                      # 이진 격자 간격(단위)
    w: int                       # 창(4분음표) = 몇 단위
    origin: int                  # 첫 온전한 마디 시작 tick (못갖춘마디 뒤)
    pu: int                      # origin 의 단위 값
    capable: object              # tick -> 이 창에서 잇단음표를 허용하는가
    result: Optional["TupletMap"] = None


class TupletMap:
    def __init__(self, cfg: TupletCfg, times: List[float]):
        self.cfg = cfg
        w = cfg.w
        self.t6, self.t3 = w // 6, w // 3
        by: Dict[int, List[float]] = {}
        for t in times:
            x = (t - cfg.origin) / cfg.unit_ticks
            wi = int(x // w)
            pos = x - w * wi
            if min(pos, w - pos) < 0.75:
                continue
            by.setdefault(wi, []).append(pos)
        self.cls: Dict[int, int] = {}
        for wi, ps in by.items():
            tick = cfg.origin + wi * w * cfg.unit_ticks
            if not cfg.capable(tick + 1):
                continue
            eb = sum(abs(p - round(p / cfg.bs) * cfg.bs) for p in ps)
            et = sum(abs(p - round(p / self.t6) * self.t6) for p in ps)
            if eb >= 0.9 and et <= 0.35 * eb:
                sn = [int(round(p / self.t6)) for p in ps]
                self.cls[wi] = w // 3 if all(k % 2 == 0 for k in sn) else w // 6

    def snap(self, t: float) -> int:
        cfg = self.cfg
        x = (t - cfg.origin) / cfg.unit_ticks
        wi = int(x // cfg.w)
        pos = x - cfg.w * wi
        step = self.cls.get(wi, cfg.bs)
        return cfg.pu + cfg.w * wi + int(round(pos / step)) * step

    def window_class(self, u: int) -> int:
        """단위 위치 u 가 속한 창의 종류: 0 이진, 3 셋잇단, 6 여섯잇단 (슬롯 폭으로 돌려준다)"""
        cfg = self.cfg
        wi = (u - cfg.pu) // cfg.w
        return self.cls.get(wi, 0)


class Pitches(tuple):
    """화음의 구성음(낮은 음부터). carry=True 이면 가장 낮은 음이 앞 화음에서 이어 울리는 지속음(타이로 잇는다)."""
    carry = False


def quantize_chords(notes: List[Tuple[int, int, int]], g: float, stats: Dict[str, int],
                    tpq: int = 0, legato: float = 0.0, min_rest: int = 0,
                    tup: Optional[TupletCfg] = None, carry: bool = True
                    ) -> List[Tuple[int, int, Tuple[int, ...]]]:
    """화음 허용 양자화: 같은 격자 시작의 음들을 화음 하나로 묶는다.
    * 화음의 끝 = 그 음들의 끝 중 가장 많은 값(같으면 더 늦은 것). 다음 화음의 시작보다 길면 거기서 자른다.
    * 이보다 길게 울리던 음은 잘리고(chord_cut), 이보다 짧던 음은 늘어난다(chord_stretch).
    -> [(시작, 끝, (낮은 음 ... 높은 음))]"""
    if legato and tpq:
        notes = absorb_gaps(notes, tpq, legato, stats)
    groups: Dict[int, List[Tuple[int, int]]] = {}
    if tup is not None:
        tup.result = TupletMap(tup, [t for s0, e0, _ in notes for t in (s0, e0)])
        snap = tup.result.snap
    else:
        snap = lambda t: int(round(t / g))
    for s, e, p in notes:
        qs, qe = snap(s), snap(e)
        if qe <= qs:
            stats["dropped_short"] = stats.get("dropped_short", 0) + 1
            continue
        groups.setdefault(qs, []).append((qe, p))
    ons = sorted(groups)
    out: List[Tuple[int, int, Tuple[int, ...]]] = []
    base_end = 0                 # 앞 화음의 가장 낮은 음이 원래 끝나는 시각
    for i, o in enumerate(ons):
        grp = groups[o]
        cnt: Dict[int, int] = {}
        for e0, _ in grp:
            cnt[e0] = cnt.get(e0, 0) + 1
        e = max(cnt.items(), key=lambda kv: (kv[1], kv[0]))[0]
        nxt = ons[i + 1] if i + 1 < len(ons) else None
        if nxt is not None and e > nxt:
            e = nxt
            stats["overlaps"] = stats.get("overlaps", 0) + 1
        elif legato and min_rest and nxt is not None and 0 < nxt - e < min_rest:
            e = nxt
            stats["legato_gaps"] = stats.get("legato_gaps", 0) + 1
        pitches = Pitches(sorted(set(p for _, p in grp)))
        own_base_end = max(e0 for e0, p in grp if p == pitches[0])
        if (carry and out and out[-1][1] == o and base_end > o and base_end >= e
                and pitches[0] > out[-1][2][0]):
            # 낮은 음(지속음)이 계속 울리는 중에 위 음들이 움직이는 경우: 지속음을 이 화음에 이어 타이로 잇는다
            pitches = Pitches((out[-1][2][0],) + tuple(pitches))
            pitches.carry = True
            stats["carried"] = stats.get("carried", 0) + 1
        else:
            base_end = own_base_end
        if len(set(e0 for e0, _ in grp)) > 1:
            stats["chord_unequal"] = stats.get("chord_unequal", 0) + 1
            stats["chord_cut"] = stats.get("chord_cut", 0) + sum(1 for e0, _ in grp if e0 > e)
        if len(grp) > len(pitches):
            stats["dup_pitch"] = stats.get("dup_pitch", 0) + (len(grp) - len(pitches))
        out.append((o, e, pitches))
    return out


# ---------------------------------------------------------------- 보표 한 줄 만들기
@dataclass
class Piece:
    kind: str                 # 'n' 음, 'r' 쉼표
    bar: int
    pos: int                  # 마디 안 위치
    dur: int
    pitch: int = 0
    extra: Tuple[int, ...] = ()      # 화음의 나머지 구성음(낮은 순)
    tie_prev: bool = False    # 앞 조각과 타이로 이어짐
    tie_next: bool = False
    fullbar: bool = False
    tup: int = 0              # 잇단음표 묶음의 음 수(0 = 아님)
    tup_first: bool = False   # 묶음의 첫 음(여기에 x3 를 쓴다)
    base_only: bool = False   # 타이가 화음의 가장 낮은 음에만 해당(지속음 이어 받기)


@dataclass
class Bar:
    start: int
    length: int
    meter: Meter
    change: bool = False       # 이 마디 맨 앞에 박자표를 써야 하는가


def bar_layout(total_units: int, segs: List[Tuple[int, Meter]], G: int, pickup_u: int = 0
               ) -> Tuple[List[Bar], Meter, List[str]]:
    """박자 구간 [(시작 단위, Meter)] -> 마디 목록.
    * 첫 구간이 한 마디짜리이고 다음 구간의 한 마디보다 짧으면 못갖춘마디로 본다(PMX 가 만든 MIDI 의 모습).
    * 마지막 구간이 한 마디짜리이고 직전 박자의 한 마디보다 짧으면 끝마디가 짧은 것으로 본다."""
    notes: List[str] = []
    segs = list(segs)
    bars: List[Bar] = []
    ends = [segs[k + 1][0] if k + 1 < len(segs) else None for k in range(len(segs))]
    pickup_first = False
    if len(segs) >= 2:
        l0 = ends[0] - segs[0][0]
        if l0 < segs[1][1].bar_units(G) and l0 <= segs[0][1].bar_units(G):
            pickup_first = True
    last_short = False
    if len(segs) >= 2 and not (len(segs) == 2 and pickup_first):
        lastm = segs[-1][1]
        if (total_units - segs[-1][0]) <= lastm.bar_units(G) and lastm.bar_units(G) < segs[-2][1].bar_units(G):
            last_short = True
    main = segs[1][1] if pickup_first else segs[0][1]
    cur = main
    first = True
    for k, (st, m) in enumerate(segs):
        end = ends[k] if ends[k] is not None else total_units
        if k == 0 and pickup_first:
            bars.append(Bar(0, end - st, main))
            notes.append("못갖춘마디로 처리: %d단위" % (end - st))
            continue
        if k == len(segs) - 1 and last_short:
            bars.append(Bar(st, max(end - st, total_units - st), cur))
            notes.append("끝마디가 짧음(박자 %d/%d)" % (m.num, m.den))
            continue
        L = m.bar_units(G)
        pos = st
        while pos < end:
            blen = min(L, end - pos)
            b = Bar(pos, blen, m)
            if m is not cur and (m.num, m.den) != (cur.num, cur.den):
                b.change = True
                cur = m
            bars.append(b)
            pos += blen
    # 못갖춘마디 지정(--pickup) : 첫 마디를 짧게
    if pickup_u and not pickup_first:
        L = main.bar_units(G)
        b0 = Bar(0, pickup_u, main)
        rest = [Bar(b.start + 0, b.length, b.meter, b.change) for b in bars]
        # 전체를 pickup_u 만큼 뒤로 민 격자로 다시 만든다
        bars = [b0]
        pos = pickup_u
        while pos < total_units:
            bars.append(Bar(pos, min(L, total_units - pos) if pos + L > total_units else L, main))
            pos += L
    if bars and bars[-1].start + bars[-1].length < total_units:
        L = bars[-1].meter.bar_units(G)
        pos = bars[-1].start + bars[-1].length
        while pos < total_units:
            bars.append(Bar(pos, L, bars[-1].meter))
            pos += L
    return bars, main, notes


def to_pieces(q: List[Tuple[int, int, Tuple[int, ...]]], bars: List[Bar], G: int,
              table: Dict[int, str], tmap: Optional[TupletMap] = None) -> List[List[Piece]]:
    """양자화된 음 -> 마디별 조각 목록(쉼표 채움, 마디 경계·박자 구조에 따른 분해, 잇단음표 묶음)"""
    import bisect
    total = bars[-1].start + bars[-1].length
    events: List[Tuple[str, int, int, Tuple[int, ...]]] = []
    cur = 0
    for s, e, p in q:
        if s > cur:
            events.append(("r", cur, s, (0,)))
        events.append(("n", s, e, p))
        cur = e
    if cur < total:
        events.append(("r", cur, total, (0,)))
    per_bar: List[List[Piece]] = [[] for _ in bars]
    # 영역: [시작, 끝, 마디, 슬롯폭(0=이진)]
    regions: List[List[int]] = []
    w = tmap.cfg.w if tmap else 0
    for bi, bar in enumerate(bars):
        L = bar.length
        ok = bool(tmap) and bar.meter.den <= 4 and w > 0 and L % w == 0 and (bar.start - tmap.cfg.pu) % w == 0
        if not ok:
            regions.append([bar.start, bar.start + L, bi, 0])
            continue
        k, nwin = 0, L // w
        while k < nwin:
            st = bar.start + k * w
            step = tmap.window_class(st)
            if step == 0:
                k2 = k + 1
                while k2 < nwin and tmap.window_class(bar.start + k2 * w) == 0:
                    k2 += 1
                regions.append([st, bar.start + k2 * w, bi, 0])
                k = k2
            else:
                regions.append([st, st + w, bi, step])
                k += 1
    # 잇단음표 창 안의 모든 경계가 슬롯에 맞는지 확인(안 맞으면 이진으로 되돌린다)
    bset = set()
    for kind, s0, e0, _ in events:
        bset.add(s0)
        bset.add(e0)
    for r in regions:
        if r[3]:
            if any(((u - r[0]) % r[3]) for u in range(r[0] + 1, r[1]) if u in bset):
                r[3] = 0
    rstarts = [r[0] for r in regions]
    prev_last: Optional[Piece] = None
    for kind, s, e, p in events:
        counts = [len(x) for x in per_bar]
        chunks = []
        a = s
        while a < e:
            ri = bisect.bisect_right(rstarts, a) - 1
            b = min(e, regions[ri][1])
            chunks.append((ri, a, b))
            a = b
        for ci, (ri, a, b) in enumerate(chunks):
            st, rend, bi, step = regions[ri]
            bar = bars[bi]
            last_chunk = ci == len(chunks) - 1
            if step:                                           # 잇단음표 창
                j0, j1 = (a - st) // step, (b - st) // step
                n = w // step
                for j in range(j0, j1):
                    tp = (ci > 0) or (j > j0)
                    tn = (not last_chunk) or (j < j1 - 1)
                    per_bar[bi].append(Piece(kind, bi, st - bar.start + j * step, w, p[0], tuple(p[1:]),
                                             tie_prev=(kind == "n" and tp), tie_next=(kind == "n" and tn),
                                             tup=n, tup_first=(j == 0)))
                continue
            pos, dur = a - bar.start, b - a
            if kind == "r" and pos == 0 and dur == bar.length == bar.meter.bar_units(G):
                per_bar[bi].append(Piece("r", bi, 0, dur, fullbar=True))
                continue
            parts = split_span(pos, dur, bar.meter, G, table)
            pp = pos
            for pi, L in enumerate(parts):
                tp = (ci > 0) or (pi > 0)
                tn = (not last_chunk) or (pi < len(parts) - 1)
                per_bar[bi].append(Piece(kind, bi, pp, L, p[0], tuple(p[1:]), tie_prev=(kind == "n" and tp),
                                         tie_next=(kind == "n" and tn)))
                pp += L
        made = [(bi2, x) for bi2 in range(len(per_bar)) for x in per_bar[bi2][counts[bi2]:]]
        if kind == "n" and made:
            if getattr(p, "carry", False) and prev_last is not None:
                made[0][1].tie_prev = True
                made[0][1].base_only = True
                prev_last.tie_next = True
            prev_last = made[-1][1]
        elif kind == "r":
            prev_last = None
    return per_bar


# ---------------------------------------------------------------- 본체 기록
ACC = {-2: "ff", -1: "f", 0: "n", 1: "s", 2: "ss"}


class StaffWriter:
    """한 보표(성부)의 상태를 들고 조각을 M-Tx 토큰으로 바꾼다."""

    def __init__(self, fifths: int, clef: str, table: Dict[int, str], hints: Dict[int, int]):
        self.fifths = fifths
        self.ref_oct = 4 if clef == "G" else 3
        self.table = table
        self.key_alt = {}
        for i in range(fifths - 1, fifths + 6):
            L, a = lof_spelling(i)
            self.key_alt[L] = a
        self.hints = hints            # 음 인덱스 -> 반음 방향 힌트
        self.courtesy = True
        self.reset_line()

    def reset_line(self):
        self.prev_dia: Optional[int] = None
        self.last_dur: Optional[str] = None
        self.ref_dia: Optional[int] = None       # 잇단음표 안에서 PMX 가 기준으로 삼는 직전 기음
        self.after_chord = False

    def start_bar(self):
        self.prev_bar_acc: Dict[Tuple[int, int], int] = getattr(self, "bar_acc", {})
        self.bar_acc: Dict[Tuple[int, int], int] = {}

    def spell_note(self, pitch: int, idx: int) -> Tuple[int, int, int]:
        L, a = spell(pitch % 12, self.fifths, self.hints.get(idx, 0))
        base = pitch - a
        octv = base // 12 - 1
        return L, a, octv

    def note_token(self, pc: Piece, idx: int, cont_acc: Optional[Tuple[int, int, int]] = None,
                   z: bool = False, tupn: int = 0, rel_only: bool = False,
                   raw_oct: bool = False) -> Tuple[str, Tuple[int, int, int]]:
        if pc.tie_prev and cont_acc is not None:
            L, a, octv = cont_acc
        else:
            L, a, octv = self.spell_note(pc.pitch, idx)
        key = (L, octv)
        cur = self.bar_acc.get(key, self.key_alt.get(L, 0))
        mark = ""
        if pc.tie_prev:
            self.bar_acc[key] = a                      # 이어지는 음은 임시표를 다시 그리지 않는다
        elif a != cur:
            mark = ACC[a]
            self.bar_acc[key] = a
        elif self.courtesy and key not in self.bar_acc and self.prev_bar_acc.get(key, a) != a:
            mark = ACC[a]                              # 바로 앞 마디에서 바뀌었던 음을 조표대로 되돌릴 때의 주의 임시표
            self.bar_acc[key] = a
        else:
            self.bar_acc.setdefault(key, a)
        dia = octv * 7 + L
        if raw_oct:                              # 잇단음표 안: M-Tx 가 해석하지 않고 PMX 로 그대로 넘긴다.
            # PMX 규칙(실험): 음 이름 뒤 한 자리 숫자 = 옥타브(길이는 묶음 첫 음을 잇는다),
            # 숫자가 없으면 직전 '기음'에서 가장 가까운 음(4도 이내). 길이 숫자와 옥타브 숫자를 같이 적으면
            # 다른 보표에 화음이 있을 때 PMX 의 MIDI 시간이 어긋나는 경우가 있어 길이는 적지 않는다.
            if z:
                tok = "z" + LETTERS[L] + mark + str(octv)
                self.after_chord = True
            else:
                need = self.after_chord or self.ref_dia is None or abs(dia - self.ref_dia) > 3
                tok = LETTERS[L] + (str(octv) if need else "") + mark
                self.ref_dia = dia
                self.after_chord = False
                self.last_dur = self.table[pc.dur]
            self.prev_dia = None
            return tok, (L, a, octv)
        if z:                                    # 화음의 구성음: z + 음 이름, 길이 없음
            tok = "z" + LETTERS[L] + mark
        else:
            dur = self.table[pc.dur]
            tok = LETTERS[L] + mark + dur        # 길이는 승계하지 않고 항상 적는다(점 승계가 불안정)
            self.last_dur = dur
        # 옥타브 표시
        suffix = ""
        if self.prev_dia is None:
            off = octv - self.ref_oct
            suffix = "=" + ("+" * off if off > 0 else "-" * (-off))
        else:
            diff = dia - self.prev_dia
            if abs(diff) > 3:
                delta = ((diff + 3) % 7) - 3
                k = (diff - delta) // 7
                if abs(k) >= 2 and (z or rel_only):        # 화음 구성음·잇단음표 안에는 `=` 를 쓸 수 없다 -> + / - 를 겹쳐 쓴다
                    suffix = ("+" if k > 0 else "-") * abs(k)
                elif abs(k) >= 2:
                    off = octv - self.ref_oct
                    suffix = "=" + ("+" * off if off > 0 else "-" * (-off))
                else:
                    suffix = "+" if k > 0 else "-"
        self.prev_dia = dia
        if tupn:
            self.ref_dia = dia
            self.after_chord = False
        if suffix:
            tok += suffix
        if tupn:
            tok += f"x{tupn}"
        return tok, (L, a, octv)

    def rest_token(self, pc: Piece) -> str:
        if pc.fullbar:
            self.last_dur = None
            return "rp"
        dur = self.table[pc.dur]
        tok = "r" + dur
        if pc.tup_first:
            tok += f"x{pc.tup}"
        self.last_dur = dur
        return tok


MAX_LINE = 240          # M-Tx 는 한 행이 250자 안팎을 넘으면 행을 잘못 읽는다(실험: 252자 통과, 270자 오류)


def line_starts(bars: List[Bar], per_line: int, widths: Optional[List[List[int]]] = None,
                limit: int = MAX_LINE) -> List[Tuple[int, int]]:
    """줄 나누기: (시작 마디, 마디 수). 변박 마디는 반드시 새 줄의 첫 마디.
    widths[보표][마디] 가 있으면 어느 보표든 한 줄이 limit 자를 넘지 않게 마디 수를 줄인다."""
    res: List[Tuple[int, int]] = []
    i = 0
    while i < len(bars):
        j = i + 1
        while j < len(bars) and j - i < per_line and not bars[j].change:
            if widths is not None and any(sum(w[k] + 3 for k in range(i, j + 1)) > limit for w in widths):
                break
            j += 1
        res.append((i, j - i))
        i = j
    return res


def build_staff_lines(per_bar: List[List[Piece]], writer: StaffWriter, seq_pitches: List[int],
                      bars_per_line: int, bars: List[Bar], top: bool = True,
                      plan: Optional[List[Tuple[int, int]]] = None) -> List[str]:
    """보표 하나의 줄들(문단 단위) 만들기"""
    lines: List[str] = []
    nb = len(per_bar)
    note_idx = 0
    cont: Optional[List[Tuple[int, int, int]]] = None
    open_tie = False
    for start, cnt in (plan or line_starts(bars, bars_per_line)):
        writer.reset_line()
        chunk = per_bar[start:start + cnt]
        out: List[str] = []
        for bi, pieces in enumerate(chunk):
            writer.start_bar()
            toks: List[str] = []
            for pc in pieces:
                if pc.kind == "r":
                    toks.append(writer.rest_token(pc))
                    continue
                pl = [pc.pitch] + list(pc.extra)
                if not pc.tie_prev or pc.base_only:
                    idx = note_idx
                    note_idx += 1
                else:
                    idx = note_idx - 1
                parts: List[str] = []
                sps: List[Tuple[int, int, int]] = []
                for k, pp in enumerate(pl):
                    tied = pc.tie_prev and (k == 0 or not pc.base_only)
                    sub = Piece("n", pc.bar, pc.pos, pc.dur, pp, (), tied, pc.tie_next)
                    ca = cont[k] if (tied and cont and k < len(cont)) else None
                    first_tok = bool(pc.tup and pc.tup_first and k == 0)
                    if first_tok:
                        writer.prev_dia = None                     # 묶음의 첫 음은 `=` 절대 옥타브로
                    tok, sp = writer.note_token(sub, idx if len(pl) == 1 else -1, ca, z=(k > 0),
                                                tupn=(pc.tup if first_tok else 0),
                                                raw_oct=bool(pc.tup and not first_tok))
                    if pc.tup:
                        writer.prev_dia = None                     # 묶음 뒤 첫 음도 절대 옥타브로
                    sps.append(sp)
                    if k == 0:                                   # 타이는 화음의 기음에만(PMX 가 화음 전체를 잇는다)
                        if pc.tie_next and not pc.tie_prev:
                            tok = "{ " + tok
                        if pc.tie_prev and not pc.tie_next:
                            tok = tok + " }"
                    parts.append(tok)
                if not pc.tie_prev or pc.base_only:
                    cont = sps
                toks.append(" ".join(parts))
            b = bars[start + bi]
            txt = " ".join(toks)
            if b.change:
                txt = f"{b.meter.num}/{b.meter.den} " + txt
            out.append(txt)
        lines.append(" | ".join(out) + " |")
    return lines


def pickup_origin(song: "Song", pickup_beats: float) -> int:
    """첫 온전한 마디가 시작하는 tick (못갖춘마디가 없으면 0)"""
    ms = song.meters
    if len(ms) >= 2:
        l0 = ms[1][0] - ms[0][0]
        bar1 = song.tpq * 4 * ms[1][1] // ms[1][2]
        bar0 = song.tpq * 4 * ms[0][1] // ms[0][2]
        if l0 < bar1 and l0 <= bar0:
            return ms[1][0]
    if pickup_beats:
        return int(round(pickup_beats * song.tpq))
    return 0


# ---------------------------------------------------------------- 전체 변환
GM_ORCH = {}


def clef_for(pitches: List[int]) -> str:
    if not pitches:
        return "G"
    s = sorted(pitches)
    med = s[len(s) // 2]
    return "G" if med >= 57 else "F"


def sanitize_name(s: str) -> str:
    s = s.strip().strip(":").strip()
    if not s or s.lower().startswith("uniquecontext"):
        return ""
    return s.replace(" ", "~")


def convert(path: str, tracks_sel: Optional[List[int]] = None, bars_per_line: int = 4, G: int = 32,
            pickup_beats: float = 0.0, title: Optional[str] = None, order: str = "file",
            lyrics: bool = False, legato: float = 0.6, drums: bool = False,
            key: Optional[int] = None, chords: bool = True, tuplets: bool = True,
            carry: bool = True) -> Tuple[str, Dict]:
    song = read_midi(path)
    info: Dict = {"warnings": list(song.warnings), "tracks": []}
    meter = song.meter
    grid_user = G
    use_tup = bool(tuplets and chords)
    if use_tup:
        G = grid_user * 3                 # 3 의 배수 격자: 1/32 음표 = 3 단위, 셋잇단 8분 = 8 단위, 여섯잇단 16분 = 4 단위
    if G % meter.den != 0:
        G = max(G, meter.den)
    table = duration_table(G)
    tick_unit = song.tpq * 4 / G                         # 1 격자 단위 = 몇 tick
    cand = []
    for t in song.tracks:
        if not t.notes:
            continue
        if tracks_sel and t.index not in tracks_sel:
            continue
        if t.channel == 9:
            if drums:
                cand.extend(split_drums(t, song.tpq, info["warnings"]))
            continue
        cand.append(t)
    if not cand:
        raise ValueError("변환할 음표 트랙이 없습니다")
    all_pitches = [n[2] for t in cand if not t.drum for n in t.notes]
    if key is not None:
        fifths = max(-7, min(7, int(key)))
        info["warnings"].append(f"조표를 강제로 지정했습니다 ({fifths:+d})")
    else:
        fifths = song.fifths if song.fifths is not None else estimate_key(all_pitches)
    if key is None and song.fifths is None:
        info["warnings"].append(f"조표가 없어 음 분포로 추정했습니다 (#{fifths:+d})")
    stats_all: Dict[str, int] = {}
    qtracks = []
    origin = pickup_origin(song, pickup_beats)
    meters_t = song.meters

    def capable(tick: float) -> bool:
        if tick < origin - 1:
            return False
        den = 4
        for tk, nu, de in meters_t:
            if tk <= tick:
                den = de
        return den <= 4

    tmaps = []
    for t in cand:
        st: Dict[str, int] = {}
        tmap = None
        if use_tup:
            cfg = TupletCfg(unit_ticks=tick_unit, G=G, bs=G // grid_user, w=G // 4, origin=origin,
                            pu=int(round(origin / tick_unit)), capable=capable)
            q = quantize_chords(t.notes, tick_unit, st, song.tpq, legato, max(1, G // 16), tup=cfg, carry=carry)
            tmap = cfg.result
            st["tuplet_beats"] = sum(1 for v in tmap.cls.values() if v)
        elif chords:
            q = quantize_chords(t.notes, tick_unit, st, song.tpq, legato, max(1, G // 16), carry=carry)
        else:
            q = [(a, b, (c,)) for a, b, c in quantize(t.notes, tick_unit, st, song.tpq, legato, max(1, G // 16))]
        tmaps.append((id(t), tmap))
        qtracks.append((t, q, st))
        for k, v in st.items():
            stats_all[k] = stats_all.get(k, 0) + v
    if order == "pitch":
        qtracks.sort(key=lambda x: -sum(n[2] for n in x[1]) / max(1, len(x[1])))
    total_u = max(q[-1][1] for _, q, _ in qtracks if q)
    pickup_u = int(round(pickup_beats * G / 4)) if pickup_beats else 0
    tu = song.tpq * 4 / G
    segs = []
    for tk, nu, de in song.meters:
        segs.append((int(round(tk / tu)), Meter(nu, de)))
    segs[0] = (0, segs[0][1])
    bars, meter, lay_notes = bar_layout(total_u, segs, G, pickup_u)
    info["warnings"].extend(lay_notes)
    info["bars"] = len(bars)
    info["G"] = G
    # 마지막 음이 마디 끝보다 조금(반 박 이하) 일찍 끝나면 마디 끝까지 늘린다(연주 습관)
    half_beat = meter.beat_units(G) // 2
    for n, (t, q, st) in enumerate(qtracks):
        if q:
            s0, e0, p0 = q[-1]
            bend = next((b.start + b.length for b in bars if b.start < e0 <= b.start + b.length), e0)
            if 0 < bend - e0 <= half_beat:
                q[-1] = (s0, bend, p0)
    voices = []
    items = []
    clefs = []
    names = []
    codes = []
    for n, (t, q, st) in enumerate(qtracks, 1):
        pitches = [ps[-1] for _, _, ps in q]            # 선율(맨 위 음) — 반음 철자 힌트용
        clef = "G" if t.drum else clef_for([x for _, _, ps in q for x in ps])
        hints = {}
        for i, p in enumerate(pitches):
            nxt = pitches[i + 1] if i + 1 < len(pitches) else None
            prv = pitches[i - 1] if i else None
            if nxt is not None and nxt - p == 1: hints[i] = 1
            elif nxt is not None and nxt - p == -1: hints[i] = -1
            elif prv is not None and p - prv == 1: hints[i] = 1
            elif prv is not None and p - prv == -1: hints[i] = -1
        per_bar = to_pieces(q, bars, G, table, dict(tmaps).get(id(t)))
        items.append((per_bar, hints, clef, pitches))
        voices.append(f"V{n}")
        clefs.append(clef)
        nm = sanitize_name(t.name) or GM_SHORT.get(t.program) or f"Part{n}"
        names.append(nm)
        codes.append(pmx_code(t.program))
        info["tracks"].append({"track": t.index, "name": nm, "notes": len(q), "clef": clef,
                               "program": t.program, **st})
    def render(plan):
        return [build_staff_lines(pb, StaffWriter(fifths, clef, table, hints), pitches, bars_per_line, bars,
                                  top=(i == 0), plan=plan)
                for i, (pb, hints, clef, pitches) in enumerate(items)]
    # 한 행이 너무 길면 M-Tx 가 읽지 못하므로, 마디별 길이를 재서 줄당 마디 수를 줄인다
    single = render([(k, 1) for k in range(len(bars))])
    widths = [[len(ln) - 2 for ln in sl] for sl in single]
    limit = MAX_LINE
    while True:
        plan = line_starts(bars, bars_per_line, widths, limit)
        staff_lines = render(plan)
        longest = max(len(ln) for sl in staff_lines for ln in sl)
        if longest <= MAX_LINE + 10 or limit <= 60:
            break
        limit -= 30
    if longest > MAX_LINE + 10:
        info["warnings"].append(f"한 행이 {longest}자로 깁니다(마디 하나가 너무 빽빽함) — M-Tx 가 읽지 못할 수 있습니다")
    # 헤더
    H = []
    ttl = title if title is not None else (song.title or os.path.splitext(os.path.basename(path))[0])
    if ttl:
        H.append(f"Title: {ttl}")
    H.append(f"Meter: {meter.num}/{meter.den}")
    if fifths > 0:
        H.append(f"Sharps: {fifths}")
    elif fifths < 0:
        H.append(f"Flats: {-fifths}")
    H.append("Style: Score")
    H.append(f"Score: Voices {' '.join(voices)}; Clefs {' '.join(clefs)}")
    H.append("Name: " + " ".join(names))
    H.append(f"Bars/Line: {bars_per_line}")
    bpm = int(round(60_000_000 / song.tempo_us)) if song.tempo_us else 0
    midi_line = "%%I" + "i" + "".join(reversed(codes)) + (f"t{bpm}" if bpm else "")
    body = [midi_line]
    nlines = len(staff_lines[0])
    paras = []
    for li in range(nlines):
        para = [staff_lines[v][li] for v in range(len(voices))]
        if li == nlines - 1:
            para = [p + "]" if p.endswith(" |") else p for p in para]          # 마지막 줄은 끝세로줄 |]
        paras.append("\n".join(para))
    text = "\n".join(H) + "\n\n" + midi_line + "\n" + "\n\n".join(paras) + "\n"
    info["quantized"] = [q for _, q, _ in qtracks]
    info["stats"] = stats_all
    info["fifths"] = fifths
    info["meter"] = f"{meter.num}/{meter.den}"
    info["tempo"] = bpm
    info["quant_unit_ticks"] = tick_unit
    return text, info


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="MIDI -> M-Tx 초안 변환")
    ap.add_argument("midi")
    ap.add_argument("-o", "--out")
    ap.add_argument("-t", "--tracks", help="변환할 트랙 번호(쉼표). 기본: 음이 있는 모든 트랙")
    ap.add_argument("--bars", type=int, default=4, help="한 문단(줄)에 넣을 마디 수 (기본 4)")
    ap.add_argument("--grid", type=int, default=32, choices=(8, 16, 32, 64), help="양자화 단위: 1/N 음표 (기본 32)")
    ap.add_argument("--pickup", type=float, default=0.0, help="못갖춘마디 길이(4분음표 단위, 예: 0.5)")
    ap.add_argument("--legato", type=float, default=0.6,
                    help="음이 다음 음까지 간격의 이 비율 이상 울렸으면 레가토로 보고 쉼표를 만들지 않는다 (0=끔, 기본 0.6)")
    ap.add_argument("--drums", action="store_true",
                    help="드럼(채널 10) 트랙도 변환: 악기별 오선 위치에 그린다(소리 높이는 아님; 심벌 보표 + 북 보표로 나눔)")
    ap.add_argument("--key", type=int, default=None, metavar="N",
                    help="조표 강제 지정: #의 수는 양수, b의 수는 음수 (예: --key=-3 은 b 세 개). 생략하면 MIDI 의 조표/추정")
    ap.add_argument("--no-carry", action="store_true", help="아래 지속음을 다음 화음으로 타이로 잇지 않고 자른다")
    ap.add_argument("--no-tuplets", action="store_true", help="잇단음표를 만들지 않는다(이진 격자로만 양자화)")
    ap.add_argument("--no-chords", action="store_true", help="화음을 만들지 않고 한 번에 가장 높은 음만 남긴다(종전 방식)")
    ap.add_argument("--title")
    ap.add_argument("--order", choices=("file", "pitch"), default="file", help="보표 순서: 파일 순서 / 평균 높이 순")
    a = ap.parse_args(argv)
    sel = [int(x) for x in a.tracks.split(",")] if a.tracks else None
    try:
        text, info = convert(a.midi, sel, a.bars, a.grid, a.pickup, a.title, a.order, legato=a.legato, drums=a.drums, key=a.key, chords=not a.no_chords, tuplets=not a.no_tuplets, carry=not a.no_carry)
    except ValueError as e:
        print("오류:", e)
        return 1
    out = a.out or os.path.splitext(a.midi)[0] + ".mtx"
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"{out}: {info['bars']}마디, {len(info['tracks'])}개 보표, {info['meter']}, 조표 {info['fifths']:+d}, 템포 {info['tempo']}")
    for t in info["tracks"]:
        extra = {k: v for k, v in t.items() if k.startswith(("dropped", "overlaps", "legato"))}
        print(f"  트랙 {t['track']:>2} {t['name']:<10} 음 {t['notes']:>4}  {t['clef']}  {extra if extra else ''}")
    for w in info["warnings"]:
        print("경고:", w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
