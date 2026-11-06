#!/usr/bin/env python3
"""
mtxplayer.py - 한 줄 MIDI 플레이어 + "특정 마디부터 재생".

원리: pygame 은 MIDI 를 중간부터 재생하지 못한다. 그래서 mido 로 원하는 마디의
시작 틱부터 잘라 낸 임시 .mid 를 만들고(템포/악기/컨트롤러 상태는 앞에 끼워 넣음)
그것을 pygame 으로 재생한다.

마디 경계는 MIDI 의 박자표(time_signature) 이벤트 + pmx 헤더의 못갖춘마디 길이로 계산하고,
박자표 이벤트가 계산된 마디 경계와 어긋나면 위치 기능을 끈다(처음부터 재생만 가능).
앞부분(상단)은 Qt 비의존이라 단독 테스트할 수 있다.
"""
from __future__ import annotations

import bisect
import os
import tempfile
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import mido


# ============================================================ Qt 비의존 부분
def parse_pickup_beats(pmx_text: str) -> float:
    """pmx 첫 데이터 줄(두 번째 '---' 다음 줄)의 xmtrnum0(못갖춘마디 박 수)."""
    seen = 0
    lines = pmx_text.split("\n")
    for i, l in enumerate(lines):
        if l.strip() == "---":
            seen += 1
            if seen == 2 and i + 1 < len(lines):
                for tok in lines[i + 1].split():
                    if "." in tok:
                        try:
                            return max(0.0, float(tok))
                        except ValueError:
                            return 0.0
                return 0.0
    return 0.0


@dataclass
class MidiInfo:
    path: str
    tpb: int = 480
    tempos: List[Tuple[int, int]] = field(default_factory=list)       # (tick, µs/beat)
    sigs: List[Tuple[int, int, int]] = field(default_factory=list)    # (tick, num, den)
    total_ticks: int = 0
    bar_ticks: List[int] = field(default_factory=list)
    bar_secs: List[float] = field(default_factory=list)
    bars_ok: bool = False
    message: str = ""

    # ---- 틱 <-> 초
    def tick_to_sec(self, tick: int) -> float:
        sec, last_t, tempo = 0.0, 0, 500000
        for t, tp in self.tempos:
            if t >= tick:
                break
            sec += (t - last_t) * tempo / self.tpb / 1e6
            last_t, tempo = t, tp
        return sec + (tick - last_t) * tempo / self.tpb / 1e6

    def sec_to_tick(self, sec: float) -> int:
        acc, last_t, tempo = 0.0, 0, 500000
        for t, tp in self.tempos:
            seg = (t - last_t) * tempo / self.tpb / 1e6
            if acc + seg > sec:
                break
            acc += seg
            last_t, tempo = t, tp
        return last_t + int((sec - acc) * 1e6 * self.tpb / tempo)

    @property
    def total_secs(self) -> float:
        return self.tick_to_sec(self.total_ticks)

    def bar_index_at_sec(self, sec: float) -> int:
        if not self.bars_ok:
            return -1
        return max(0, bisect.bisect_right(self.bar_secs, sec + 1e-6) - 1)


def analyze(path: str, nbars: int, pickup_beats: float = 0.0) -> MidiInfo:
    info = MidiInfo(path)
    mid = mido.MidiFile(path)
    info.tpb = mid.ticks_per_beat
    for tr in mid.tracks:
        ab = 0
        for msg in tr:
            ab += msg.time
            if msg.is_meta and msg.type == "set_tempo":
                info.tempos.append((ab, msg.tempo))
            elif msg.is_meta and msg.type == "time_signature":
                info.sigs.append((ab, msg.numerator, msg.denominator))
            if not (msg.is_meta and msg.type == "end_of_track"):
                info.total_ticks = max(info.total_ticks, ab)
    info.tempos = sorted(set(info.tempos))
    info.sigs = sorted(set(info.sigs))
    if not info.sigs:
        info.sigs = [(0, 4, 4)]
    if not info.tempos or info.tempos[0][0] != 0:
        info.tempos.insert(0, (0, 500000))

    def meter_at(t):
        cur = (4, 4)
        for tick, n, d in info.sigs:
            if tick <= t:
                cur = (n, d)
        return cur

    starts, pos = [], 0
    for i in range(max(0, nbars)):
        starts.append(pos)
        n, d = meter_at(pos)
        length = n * info.tpb * 4 // d
        if i == 0 and pickup_beats > 0:
            length = int(round(pickup_beats * info.tpb * 4 / d))
        pos += max(1, length)
    # 검증: (1) 박자 변경 틱이 마디 시작과 일치 (2) 계산한 총 길이가 MIDI 내용을 덮음
    edge = set(starts) | {pos}
    bad = [t for t, _, _ in info.sigs if t != 0 and t not in edge]
    if nbars <= 0:
        info.message = "마디 정보 없음"
    elif bad:
        info.message = f"박자 변경 틱 {bad[0]} 이 마디 경계와 어긋남 (못갖춘마디/다중쉼표?)"
    elif pos < info.total_ticks - info.tpb:
        info.message = "계산한 마디 길이가 MIDI 보다 짧음 (마디 수 불일치)"
    elif pos > info.total_ticks + 2 * info.tpb * 4:
        info.message = "계산한 마디 길이가 MIDI 보다 훨씬 김 (마디 수 불일치)"
    else:
        info.bars_ok = True
        info.bar_ticks = starts
        info.bar_secs = [info.tick_to_sec(t) for t in starts]
    return info


def trim_midi(src: str, from_tick: int, dst: str) -> None:
    """from_tick 이전 음표는 버리고, 그 시점의 템포/악기/컨트롤러 상태를 앞에 넣어 저장."""
    mid = mido.MidiFile(src)
    out = mido.MidiFile(type=mid.type, ticks_per_beat=mid.ticks_per_beat)
    for tr in mid.tracks:
        ab = 0
        state = {}          # 키 -> 메시지 (from_tick 이전의 마지막 상태)
        keep_meta = []      # 이름 같은 부가 메타(맨 앞으로)
        after = []          # (tick, msg)
        for msg in tr:
            ab += msg.time
            if msg.is_meta and msg.type == "end_of_track":
                continue
            if ab < from_tick:
                if msg.is_meta:
                    if msg.type in ("set_tempo", "time_signature", "key_signature"):
                        state[(msg.type,)] = msg
                    else:
                        keep_meta.append(msg)
                elif msg.type == "program_change":
                    state[("pc", msg.channel)] = msg
                elif msg.type == "control_change":
                    state[("cc", msg.channel, msg.control)] = msg
                elif msg.type == "pitchwheel":
                    state[("pw", msg.channel)] = msg
                # note_on / note_off 등은 버린다
            else:
                after.append((ab, msg))
        events = [(0, m) for m in keep_meta] + [(0, m) for m in state.values()]
        events += [(t - from_tick, m) for t, m in after]
        events.sort(key=lambda e: e[0])        # 안정 정렬: 같은 틱의 순서 유지
        new = mido.MidiTrack()
        prev = 0
        for t, m in events:
            new.append(m.copy(time=t - prev))
            prev = t
        new.append(mido.MetaMessage("end_of_track", time=0))
        out.tracks.append(new)
    out.save(dst)


# ============================================================ Qt 부분
try:
    from PyQt6.QtCore import QObject, QTimer, QUrl, Qt, pyqtSignal
    from PyQt6.QtGui import QDesktopServices
    from PyQt6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QSlider, QToolButton, QWidget)
    HAVE_QT = True
except Exception:       # pragma: no cover
    HAVE_QT = False

if HAVE_QT:
    class Player(QObject):
        stateChanged = pyqtSignal(str)                   # playing / paused / stopped
        positionChanged = pyqtSignal(float, float)       # 현재 초, 전체 초
        barChanged = pyqtSignal(int)                     # 재생 중인 마디 인덱스(0-기준)
        notice = pyqtSignal(str)

        def __init__(self, parent=None):
            super().__init__(parent)
            self.info: Optional[MidiInfo] = None
            self.soundfont = ""
            self.volume = 0.8
            self.state = "stopped"
            self._offset = 0.0
            self._last_bar = -2
            self._pg = None
            self._tmp = os.path.join(tempfile.gettempdir(), f"mtxmusictypist_{os.getpid()}.mid")
            self._timer = QTimer(self)
            self._timer.setInterval(80)
            self._timer.timeout.connect(self._tick)

        # ---- 소스
        def set_source(self, path: Optional[str], nbars: int = 0, pickup: float = 0.0) -> str:
            self.stop()
            self.info = None
            if not path or not os.path.exists(path):
                self.positionChanged.emit(0.0, 0.0)
                return "MIDI 없음"
            try:
                self.info = analyze(path, nbars, pickup)
            except Exception as e:
                return f"MIDI 분석 실패: {e}"
            self.positionChanged.emit(0.0, self.info.total_secs)
            return self.info.message

        @property
        def has_midi(self) -> bool:
            return self.info is not None

        @property
        def can_seek_bar(self) -> bool:
            return bool(self.info and self.info.bars_ok)

        # ---- pygame
        def _mixer(self):
            if self._pg is None:
                try:
                    import pygame
                    self._pg = pygame
                except Exception:
                    self._pg = False
            return self._pg or None

        # ---- 사운드폰트
        def _mix_lib(self):
            """pygame 이 쓰는 SDL2_mixer 라이브러리를 ctypes 로 잡는다(이미 적재된 것과 같은 파일)."""
            if getattr(self, "_mixlib", None) is not None:
                return self._mixlib or None
            self._mixlib = False
            try:
                import ctypes, glob
                import pygame
                d = os.path.dirname(pygame.__file__)
                cands = (glob.glob(os.path.join(d, "**", "*SDL2_mixer*"), recursive=True)
                         + glob.glob(os.path.join(os.path.dirname(d), "pygame.libs", "*mixer*")))
                for c in cands:
                    if c.endswith((".py", ".pyc", ".pyi")):
                        continue
                    try:
                        lib = ctypes.CDLL(c)
                        lib.Mix_GetSoundFonts.restype = ctypes.c_char_p
                        lib.Mix_SetSoundFonts.argtypes = [ctypes.c_char_p]
                        lib.Mix_Linked_Version.restype = ctypes.POINTER(ctypes.c_ubyte * 3)
                        self._mixlib = lib
                        break
                    except Exception:
                        continue
            except Exception:
                pass
            return self._mixlib or None

        def apply_soundfont(self):
            """재생 직전에 사운드폰트를 SDL_mixer 에 지정(환경변수 + Mix_SetSoundFonts)."""
            if self.soundfont and os.path.exists(self.soundfont):
                os.environ["SDL_SOUNDFONTS"] = self.soundfont
                lib = self._mix_lib()
                if lib:
                    try:
                        lib.Mix_SetSoundFonts(self.soundfont.encode("utf-8"))
                    except Exception:
                        pass
            else:
                os.environ.pop("SDL_SOUNDFONTS", None)

        def soundfont_report(self) -> str:
            import platform
            L = []
            sf = self.soundfont
            if not sf:
                L.append("설정된 사운드폰트: (없음 — 시스템 기본 소리)")
            else:
                L.append(f"설정된 사운드폰트: {sf}")
                if os.path.exists(sf):
                    L.append(f"  파일 있음, {os.path.getsize(sf) / 1048576:.1f} MB")
                else:
                    L.append("  ※ 파일이 없습니다 → 무시되고 기본 소리로 재생됩니다")
            pg = self._mixer()
            if not pg:
                L.append("pygame 없음 — MIDI 를 기본 프로그램으로 열기 때문에 사운드폰트가 적용되지 않습니다")
                return "\n".join(L)
            L.append(f"pygame {pg.version.ver} / SDL {pg.version.SDL}  ({platform.system()})")
            L.append(f"환경변수 SDL_SOUNDFONTS: {os.environ.get('SDL_SOUNDFONTS') or '(없음)'}")
            lib = self._mix_lib()
            if lib:
                try:
                    v = list(lib.Mix_Linked_Version().contents)
                    cur = lib.Mix_GetSoundFonts()
                    L.append(f"SDL_mixer {v[0]}.{v[1]}.{v[2]} 이 인식한 사운드폰트: "
                             f"{cur.decode('utf-8', 'replace') if cur else '(없음)'}")
                except Exception as e:
                    L.append(f"SDL_mixer 조회 실패: {e}")
            else:
                L.append("SDL_mixer 라이브러리를 직접 조회하지 못했습니다")
            L.append("")
            L.append("참고: 사운드폰트는 SDL_mixer 의 MIDI 백엔드가 FluidSynth(또는 일부 Timidity 설정)일 때만 쓰입니다.")
            if platform.system() == "Windows":
                L.append("Windows 의 pygame 은 보통 시스템 MIDI(Microsoft GS Wavetable)로 재생해 사운드폰트가 무시됩니다.")
            elif platform.system() == "Darwin":
                L.append("macOS 의 pygame 은 보통 Apple 내장 신시사이저로 재생해 사운드폰트가 무시될 수 있습니다.")
            L.append("위에서 ‘인식한 사운드폰트’가 맞는데도 소리가 같다면 백엔드가 이를 쓰지 않는 것입니다 —")
            L.append("그 경우 외부 플레이어(FluidSynth 등)로 mid 를 여는 방법이 확실합니다.")
            return "\n".join(L)

        # ---- 재생 제어
        def play(self, bar_index: Optional[int] = None, sec: Optional[float] = None):
            if not self.info:
                self.notice.emit("재생할 MIDI 가 없습니다 — 헤더에서 ‘MIDI 생성’을 켜고 컴파일하세요")
                return
            start_tick, self._offset = 0, 0.0
            if bar_index is not None and self.can_seek_bar and 0 <= bar_index < len(self.info.bar_ticks):
                start_tick = self.info.bar_ticks[bar_index]
            elif sec is not None and sec > 0:
                start_tick = self.info.sec_to_tick(sec)
            elif bar_index is not None and not self.can_seek_bar:
                self.notice.emit("이 곡은 마디 위치를 계산하지 못해 처음부터 재생합니다: " + self.info.message)
            path = self.info.path
            if start_tick > 0:
                try:
                    trim_midi(self.info.path, start_tick, self._tmp)
                    path = self._tmp
                    self._offset = self.info.tick_to_sec(start_tick)
                except Exception as e:
                    self.notice.emit(f"MIDI 자르기 실패: {e}")
                    return
            pg = self._mixer()
            if pg is None:
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
                self.notice.emit("pygame 이 없어 기본 프로그램으로 엽니다 (pip install pygame)")
                return
            try:
                self.apply_soundfont()
                if pg.mixer.get_init():
                    pg.mixer.quit()
                pg.mixer.init()
                self.apply_soundfont()
                pg.mixer.music.load(path)
                pg.mixer.music.set_volume(self.volume)
                pg.mixer.music.play()
            except Exception as e:
                self.notice.emit(f"재생할 수 없습니다: {e}")
                return
            self._last_bar = -2
            self._set_state("playing")
            self._timer.start()

        def pause_toggle(self):
            pg = self._mixer()
            if not pg or self.state == "stopped":
                return
            if self.state == "playing":
                pg.mixer.music.pause()
                self._set_state("paused")
            else:
                pg.mixer.music.unpause()
                self._set_state("playing")

        def stop(self):
            self._timer.stop()
            pg = self._pg
            if pg:
                try:
                    if pg.mixer.get_init():
                        pg.mixer.music.stop()
                except Exception:
                    pass
            self._set_state("stopped")
            self._last_bar = -2
            if self.info:
                self.positionChanged.emit(0.0, self.info.total_secs)

        def set_volume(self, v: float):
            self.volume = max(0.0, min(1.0, v))
            pg = self._pg
            if pg and pg.mixer.get_init():
                pg.mixer.music.set_volume(self.volume)

        def _set_state(self, s: str):
            if s != self.state:
                self.state = s
                self.stateChanged.emit(s)

        def _tick(self):
            pg = self._pg
            if not pg or not pg.mixer.get_init():
                return
            if self.state == "playing" and not pg.mixer.music.get_busy():
                self.stop()
                return
            pos = self._offset + max(0, pg.mixer.music.get_pos()) / 1000.0
            total = self.info.total_secs if self.info else 0.0
            self.positionChanged.emit(pos, total)
            if self.info and self.info.bars_ok:
                b = self.info.bar_index_at_sec(pos)
                if b != self._last_bar:
                    self._last_bar = b
                    self.barChanged.emit(b)

    class PlayerBar(QWidget):
        """아주 작은 한 줄 플레이어: ▶ ⏸ ■ [커서 마디부터] ──슬라이더── 시간 🔈"""
        playRequested = pyqtSignal(bool)          # True: 커서 마디부터, False: 처음부터

        def __init__(self, player: Player, parent=None):
            super().__init__(parent)
            self.player = player
            lay = QHBoxLayout(self)
            lay.setContentsMargins(6, 2, 6, 2)
            lay.setSpacing(6)

            def btn(text, tip):
                b = QToolButton()
                b.setText(text)
                b.setToolTip(tip)
                b.setAutoRaise(True)
                return b
            self.b_play = btn("▶", "재생 (F8)")
            self.b_pause = btn("⏸", "일시정지/계속")
            self.b_stop = btn("■", "정지")
            self.chk_cursor = QCheckBox("커서 마디부터")
            self.chk_cursor.setChecked(True)
            self.chk_cursor.setToolTip("켜면 편집기 커서가 있는 마디부터, 끄면 처음부터 재생합니다 (Shift+F8: 처음부터)")
            self.slider = QSlider(Qt.Orientation.Horizontal)
            self.slider.setRange(0, 1000)
            self.lbl = QLabel("00:00 / 00:00")
            self.lbl_bar = QLabel("")
            self.vol = QSlider(Qt.Orientation.Horizontal)
            self.vol.setRange(0, 100)
            self.vol.setValue(int(player.volume * 100))
            self.vol.setFixedWidth(70)
            self.vol.setToolTip("볼륨")
            for w in (self.b_play, self.b_pause, self.b_stop, self.chk_cursor):
                lay.addWidget(w)
            lay.addWidget(self.slider, 1)
            lay.addWidget(self.lbl)
            lay.addWidget(self.lbl_bar)
            lay.addWidget(QLabel("🔈"))
            lay.addWidget(self.vol)

            self._dragging = False
            self.b_play.clicked.connect(lambda: self.playRequested.emit(self.chk_cursor.isChecked()))
            self.b_pause.clicked.connect(player.pause_toggle)
            self.b_stop.clicked.connect(player.stop)
            self.vol.valueChanged.connect(lambda v: player.set_volume(v / 100))
            self.slider.sliderPressed.connect(lambda: setattr(self, "_dragging", True))
            self.slider.sliderReleased.connect(self._seek)
            player.positionChanged.connect(self._pos)
            player.stateChanged.connect(self._state)
            self._state("stopped")

        @staticmethod
        def _fmt(s: float) -> str:
            s = int(max(0, s))
            return f"{s // 60:02d}:{s % 60:02d}"

        def _pos(self, cur: float, total: float):
            self.lbl.setText(f"{self._fmt(cur)} / {self._fmt(total)}")
            if not self._dragging and total > 0:
                self.slider.setValue(int(1000 * cur / total))

        def _state(self, s: str):
            self.b_play.setText("▶" if s != "playing" else "▶")
            self.b_pause.setEnabled(s != "stopped")
            self.b_stop.setEnabled(s != "stopped")
            self.b_pause.setText("⏸" if s != "paused" else "⏯")
            if s == "stopped":
                self.lbl_bar.setText("")

        def _seek(self):
            self._dragging = False
            info = self.player.info
            if info:
                self.player.play(sec=info.total_secs * self.slider.value() / 1000.0)

        def show_bar(self, pmx_bar: Optional[int]):
            self.lbl_bar.setText("" if pmx_bar is None else f"마디 {pmx_bar}")
