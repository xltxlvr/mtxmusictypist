#!/usr/bin/env python3
# +++++++++++++++++++++++++++++++
# mtxMusicTypist  (v1.13)
#   mtxstarter + mtxvisualhelper 통합본: 편집기 + PDF 뷰어 + 입력 도크 + 컴파일
#   PDF 의 마디를 두 번 클릭하면 mtx 소스의 해당 위치로, 커서를 옮기면 PDF 의 해당 마디로.
# +++++++++++++++++++++++++++++++
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import tempfile
import sys
import types
from dataclasses import dataclass, field
from typing import List, Optional

from PyQt6.QtCore import QEvent, QObject, QProcess, QProcessEnvironment, QSettings, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QAction, QDesktopServices, QFont, QIcon, QKeySequence, QShortcut
from PyQt6.QtWidgets import (QApplication, QDockWidget, QFileDialog, QLabel, QMainWindow,
                             QMessageBox, QPlainTextEdit, QFontComboBox, QSpinBox, QDialog, QFormLayout, QDialogButtonBox, QProgressBar, QScrollArea, QSplitter,
                             QWidget, QVBoxLayout, QLineEdit, QPushButton, QHBoxLayout, QListWidget)

import mtxbeats
import mtxheader
import mtxmap
import mtxpos
import mtxplayer
import pmxpatch
from mtxwidgets import CodeEditor, FindBar, HeaderPanel, PdfView, PmxPanel

__version__ = "1.44"
FROZEN = bool(getattr(sys, "frozen", False))
if getattr(sys, "_MEIPASS", None):               # PyInstaller (onefile: 임시 풀림 폴더, onedir/.app: _internal 등)
    APP_DIR = os.path.abspath(sys._MEIPASS)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))


def asset_dirs() -> List[str]:
    """데이터 파일(아이콘, 패치된 .tex 등)을 찾아 볼 폴더들. 패키징 형태가 달라도 찾도록 넉넉히."""
    c = [APP_DIR, os.path.dirname(os.path.abspath(__file__))]
    if FROZEN:
        exe = os.path.dirname(os.path.abspath(sys.executable))
        c += [exe, os.path.join(exe, "_internal"),
              os.path.join(exe, "..", "Resources"), os.path.join(exe, "..", "Frameworks")]   # macOS .app
    c.append(os.getcwd())
    out = []
    for d in c:
        d = os.path.normpath(d)
        if d not in out:
            out.append(d)
    return out


def asset(name: str) -> str:
    for base in asset_dirs():
        p = os.path.join(base, name)
        if os.path.exists(p):
            return p
    return os.path.join(APP_DIR, name)


def child_env_fix(env) -> None:
    """PyInstaller 가 바꿔 놓은 라이브러리 경로를 되돌린다. (그대로 두면 번들 라이브러리가 먼저 잡혀
    시스템의 luatex/pdftex 등 외부 프로그램이 실행에 실패할 수 있다.) env: QProcessEnvironment 또는 dict"""
    if not FROZEN:
        return
    is_q = hasattr(env, "insert")
    for var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH", "LIBPATH"):
        orig = env.value(var + "_ORIG", "") if is_q else env.get(var + "_ORIG", "")
        cur = env.value(var, "") if is_q else env.get(var, "")
        if not (orig or cur):
            continue
        if is_q:
            env.insert(var, orig) if orig else env.remove(var)
        elif orig:
            env[var] = orig
        else:
            env.pop(var, None)


DOC_URLS = [
    ("M-Tx 매뉴얼 (mtx060.pdf)", "https://icking-music-archive.org/software/mtx/mtx060.pdf"),
    ("PMX 매뉴얼 (pmx300.pdf)", "https://icking-music-archive.org/software/pmx/pmx300.pdf"),
    ("PMX 부록 (pmxccn.pdf)", "https://www.icking-music-archive.org/software/pmx/pmxccn.pdf"),
]
TEXDOC_NAMES = [("M-Tx", "m-tx"), ("PMX", "pmx"), ("MusiXTeX", "musixtex")]


def split_tex_path(text: str) -> List[str]:
    """‘TeX 경로’ 입력(여러 개는 ; 또는 : 로 구분) -> 존재하는 폴더 목록. ~ 와 환경변수 확장."""
    out = []
    parts = text.replace("\n", ";").split(";") if sys.platform == "win32" else text.replace("\n", ":").split(":")
    for p in parts:
        p = os.path.expandvars(os.path.expanduser(p.strip().strip('"')))
        if p:
            out.append(p)
    return out


def build_stem(stem: str) -> str:
    """컴파일 중간 파일 이름. prepmx/pmxab 같은 외부 프로그램은 (특히 Windows 에서) 한글 등 비ASCII 파일 이름을
    못 읽는 일이 있으므로, 비ASCII 곡 이름은 ASCII 이름으로 바꿔서 빌드하고 결과를 원래 이름으로 복사한다."""
    if stem.isascii() and " " not in stem:
        return f"{stem}__mtxbuild"
    import hashlib
    h = hashlib.md5(stem.encode("utf-8")).hexdigest()[:8]
    return f"mtxsong_{h}__mtxbuild"


def path_with_extra(extra_dirs: List[str], base: str) -> str:
    return os.pathsep.join(extra_dirs + ([base] if base else []))


# ====================================================================== 입력 도크(기존 헬퍼 재사용)
def _load_helper_class():
    """mtxvisualhelper.py 를 그대로 가져와 쓰되, pyautogui 의존(키 전송)은 쓰지 않는다."""
    try:
        import pyautogui  # noqa: F401
    except Exception:
        sys.modules['pyautogui'] = types.ModuleType('pyautogui')   # 가짜 모듈
    if APP_DIR not in sys.path:
        sys.path.insert(0, APP_DIR)
    import mtxvisualhelper as mvh
    from PyQt6.QtGui import QShortcut as _QS

    class HelperPanel(mvh.MTxVisualInputWidget):
        textReady = pyqtSignal(str)

        def __init__(self, initial_key=0):
            super().__init__(initial_key)
            # 단축키(b, v, r, 숫자 ...)가 메인 편집기 타이핑을 가로채지 않도록 범위를 제한
            for sc in self.findChildren(_QS):
                sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            # 통합 창 안에서는 의미가 달라지는 항목 정리
            from PyQt6.QtWidgets import QPushButton as _PB
            self.top_hint_cb.hide()
            hot = {"none": "Alt+X", "s": "Alt+S", "f": "Alt+F", "n": "Alt+N", "ss": "Alt+Shift+S", "ff": "Alt+Shift+F"}
            for b in self.acc_btn_group.buttons():
                k = hot.get(b.property("acc_val"))
                if k:
                    b.setToolTip(f"{k} (편집기에서도) — 헬퍼에서는 소문자 키")
            self.abs_pitch_btn.setToolTip("Alt+= (편집기에서도)")
            for b in self.findChildren(_PB):
                if "VS Code" in b.text():
                    b.setText("📥 편집기에 삽입")
                    b.setToolTip("Ctrl+Enter — 편집기의 커서 위치에 코드를 넣습니다")

            # (1) 취소 단추가 좁아 글자가 잘리므로 글자 폭에 맞춰 넓힌다
            for b in self.findChildren(_PB):
                if b.text() == "취소":
                    b.setFixedWidth(b.fontMetrics().horizontalAdvance("취소") + 28)
            # (2) 포커스 표시: 단축키는 헬퍼 영역에 포커스가 있을 때만 듣는다
            self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
            self.focus_lbl = QLabel()
            self.focus_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.focus_lbl.setMinimumHeight(22)
            lay = self.layout()
            if lay is not None:
                lay.insertWidget(0, self.focus_lbl)
            QApplication.instance().focusChanged.connect(self._update_focus_label)
            self._update_focus_label()
            # (3) 한글 입력 상태에서도 단축키가 듣도록: 글자가 아니라 ‘자판 위치’로 해석
            self._key_actions = {}
            for sc in self.findChildren(_QS):
                ks = sc.key().toString()
                if len(ks) == 1 and ks.isalpha():
                    self._key_actions[ks.lower()] = sc

        _STATE = {
            "on":   ("● 단축키 사용 중 — 헬퍼에 포커스", "#1b5e20", "#c8e6c9"),
            "text": ("✎ 텍스트 편집 중 — Esc 로 단축키 모드", "#7a5200", "#ffe0b2"),
            "off":  ("○ 단축키 꺼짐 — 이 패널의 빈 곳을 클릭하세요", "#555555", "#e0e0e0"),
        }

        def _update_focus_label(self, *_):
            w = QApplication.focusWidget()
            if w is None or not (w is self or self.isAncestorOf(w)):
                st = "off"
            elif w is self.text_edit or self.text_edit.isAncestorOf(w):
                st = "text"
            else:
                st = "on"
            t, fg, bg = self._STATE[st]
            self.focus_lbl.setText(t)
            self.focus_lbl.setStyleSheet(
                f"color:{fg}; background:{bg}; font-weight:bold; border-radius:3px; padding:2px 6px;")

        # 2벌식 한글 자판 -> 같은 자리의 영문 키 (조합 중 자음/모음이 그대로 들어오는 경우)
        _JAMO = dict(zip("ㅂㅈㄷㄱㅅㅛㅕㅑㅐㅔㅁㄴㅇㄹㅎㅗㅓㅏㅣㅋㅌㅊㅍㅠㅜㅡ", "qwertyuiopasdfghjklzxcvbnm"))

        def keyPressEvent(self, ev):
            mods = ev.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
            if mods in (Qt.KeyboardModifier.NoModifier,):
                ch = self._JAMO.get(ev.text()[:1], "")
                if not ch and sys.platform == "win32":
                    vk = ev.nativeVirtualKey()          # IME 가 가로채도 가상 키 코드는 A-Z 그대로
                    if 0x41 <= vk <= 0x5A and not ev.text().isascii():
                        ch = chr(vk).lower()
                sc = self._key_actions.get(ch)
                if sc is not None:
                    sc.activated.emit()
                    ev.accept()
                    return
            super().keyPressEvent(ev)

        def toggle_always_on_top(self):      # 도크에서는 쓰지 않음
            pass

        def smart_send(self):          # 원본: 클립보드 + 키 입력 흉내 -> 여기서는 신호만
            txt = self.text_edit.toPlainText()
            if txt.strip():
                self.last_sent_text = txt
                self.textReady.emit(txt)
                self.text_edit.clear()
                self.action_history.clear()
                self.current_beats = 0
                self.text_edit.clearFocus()
                self.setFocus()

    return HelperPanel


# ====================================================================== 컴파일러
@dataclass
class BuildResult:
    ok: bool = False
    log: str = ""
    tmp_stem: str = ""
    workdir: str = ""
    pdf: str = ""
    pmx_text: str = ""          # 패치 전(M-Tx 가 만든 그대로) — 마디 대응/패치 기준
    pmx_final: str = ""         # 실제로 조판한 pmx (패치 적용 후)
    patch_report: List[str] = field(default_factory=list)
    patched: bool = False
    pos_path: str = ""
    midi: str = ""
    line_offset: int = 0
    hook: bool = False
    meter_off: bool = False
    snapshot: str = ""
    stem: str = ""
    error_lines: List[int] = field(default_factory=list)


class Compiler(QObject):
    """musixtex 호출. 패치가 있으면 단계를 나눈다: prepmx -> (pmx 패치) -> musixtex -p."""
    finished = pyqtSignal(object)
    status = pyqtSignal(str)

    TIMEOUT_MS = 90_000

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc: Optional[QProcess] = None
        self.res: Optional[BuildResult] = None
        self._tmpdir = ""
        self.tex_path = ""              # 설정의 ‘TeX 경로’ (이 앱이 띄우는 프로세스의 PATH 앞에만 붙인다)
        self._queue: List[tuple] = []
        self._buffer = ""
        self._env: Optional[QProcessEnvironment] = None
        self._patch_text: Optional[str] = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._timeout)

    @property
    def running(self) -> bool:
        return self.proc is not None

    @staticmethod
    def insert_hook(text: str, extra=None):
        """헤더 끝(빈 줄 바로 앞)에 `%% \\input …` 줄들(기본: mtxpos 후크)을 끼워 넣은 사본과 끼워 넣은 줄 수."""
        extra = list(extra) if extra is not None else [mtxpos.HOOK_LINE]
        lines = text.split("\n")
        idx = next((i for i, l in enumerate(lines) if not l.strip() or l.strip() == "%%"), None)
        if idx is None or idx == 0:
            return text, 0
        lines[idx:idx] = extra
        return "\n".join(lines), len(extra)

    def start(self, mtx_path: str, text: str, hook: bool, luatex: bool, hangul: bool,
              patch_text: Optional[str] = None) -> None:
        workdir = os.path.dirname(os.path.abspath(mtx_path))
        stem = os.path.splitext(os.path.basename(mtx_path))[0]
        tmp_stem = build_stem(stem)
        res = BuildResult(tmp_stem=tmp_stem, workdir=workdir, stem=stem,
                          snapshot=text, hook=hook)
        self.res = res
        self._buffer = ""
        self._patch_text = patch_text
        for f in glob.glob(os.path.join(workdir, glob.escape(tmp_stem) + ".*")):
            try: os.remove(f)
            except OSError: pass
        meter_off = bool(mtxheader.parse_header(text)[0].get('meter_off'))
        extra = ([mtxpos.HOOK_LINE] if hook else []) + ([mtxpos.NOMETER_LINE] if meter_off else [])
        build_text, off = self.insert_hook(text, extra) if extra else (text, 0)
        res.hook = hook and off == len(extra)
        res.meter_off = meter_off and off == len(extra)
        res.line_offset = off
        with open(os.path.join(workdir, tmp_stem + ".mtx"), "w", encoding="utf-8", newline="\n") as f:
            f.write(build_text)
        # 앱 전용 임시 폴더: 후크(mtxpos.tex)와 패치된 musixtex/pmx(LuaTeX + 한글)를 여기에 풀고
        # 컴파일 동안만 TEXINPUTS 맨 앞에 둔다. 악보 폴더는 건드리지 않는다.
        self._tmpdir = ""
        need_tmp = res.hook or res.meter_off or (luatex and hangul)
        if need_tmp:
            self._tmpdir = tempfile.mkdtemp(prefix="mtxtypist_tex_")
            if res.hook:
                with open(os.path.join(self._tmpdir, "mtxpos.tex"), "w", encoding="utf-8") as f:
                    f.write(mtxpos.MTXPOS_TEX)
            if res.meter_off:
                with open(os.path.join(self._tmpdir, "mtxnometer.tex"), "w", encoding="utf-8") as f:
                    f.write(mtxpos.NOMETER_TEX)
                self._buffer += "[박자표 숨김] mtxnometer.tex\n"
            if luatex and hangul:
                missing = []
                for name in ("musixtex", "pmx"):
                    src = asset(f"{name}_patched.tex")
                    if os.path.exists(src):
                        shutil.copy(src, os.path.join(self._tmpdir, f"{name}.tex"))
                        self._buffer += f"[패치된 TeX] {name}.tex <- {src}\n"
                    else:
                        missing.append(f"{name}_patched.tex")
                if missing:
                    self._finish(False, "\n패치된 TeX 파일을 찾을 수 없습니다: " + ", ".join(missing)
                                 + "\n찾아 본 폴더:\n  " + "\n  ".join(asset_dirs())
                                 + "\n(앱을 패키징했다면 데이터 파일로 포함해야 합니다 — BUILD.md 참고)\n")
                    return

        engine = "luahbtex" if luatex else "pdftex"
        env = QProcessEnvironment.systemEnvironment()
        child_env_fix(env)
        sep = ";" if sys.platform == "win32" else ":"
        first = (self._tmpdir + sep) if self._tmpdir else ""
        env.insert("TEXINPUTS", f"{first}.{sep}{workdir}{sep}" + env.value("TEXINPUTS", ""))
        if sys.platform == "darwin":
            extra = os.pathsep.join(["/opt/homebrew/bin", "/usr/local/bin", "/Library/TeX/texbin"])
            env.insert("PATH", extra + os.pathsep + env.value("PATH", ""))
        extra = split_tex_path(self.tex_path)
        if extra:
            env.insert("PATH", path_with_extra(extra, env.value("PATH", "")))
            self._buffer += "[TeX 경로] " + os.pathsep.join(extra) + "\n"
        self._env = env
        fopt = ["-F", f"{engine} -interaction=nonstopmode"]
        if patch_text is None:
            # 기존 방식 그대로: 한 번에
            self._queue = [("musixtex", fopt + ["-p", tmp_stem + ".mtx"], None)]
        else:
            # 단계 분리: (1) prepmx -> .pmx  (2) 패치  (3) musixtex -p 이름.pmx  (확장자까지 지정해야 pmx 로 처리됨)
            self._queue = [("prepmx", [tmp_stem], self._after_mtx),
                           ("musixtex", fopt + ["-p", tmp_stem + ".pmx"], None)]
        self._next_stage()

    # ------------------------------------------------------------------
    def _next_stage(self):
        prog, args, _ = self._queue[0]
        self.status.emit("컴파일 중… (" + prog + ")")
        self._buffer += f"$ {prog} {' '.join(args)}\n"
        self.proc = QProcess(self)
        self.proc.setProcessEnvironment(self._env)
        self.proc.setWorkingDirectory(self.res.workdir)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.finished.connect(self._stage_done)
        self.proc.errorOccurred.connect(self._error)
        # QProcess 는 부모의 PATH 로 실행 파일을 찾으므로, 추가한 TeX 경로에서 직접 찾아 전체 경로로 시작한다
        exe = shutil.which(prog, path=self._env.value("PATH", "")) or prog
        self.proc.start(exe, args)
        self._timer.start(self.TIMEOUT_MS)

    def _stage_done(self, code, status):
        if self.proc is None:
            return
        self._buffer += bytes(self.proc.readAll()).decode("utf-8", "replace") + "\n"
        prog, _, after = self._queue.pop(0)
        if code != 0:
            self._finish(False, f"\n[{prog}] 종료 코드 {code}\n")
            return
        if after and not after():
            return
        if self._queue:
            self.proc = None
            self._next_stage()
        else:
            self._finish(True)

    def _after_mtx(self) -> bool:
        """m-tx 직후: pmx 를 읽어 원본을 보관하고 패치를 적용해 덮어쓴다."""
        res = self.res
        pmx = os.path.join(res.workdir, res.tmp_stem + ".pmx")
        try:
            with open(pmx, encoding="utf-8", errors="replace") as f:
                res.pmx_text = f.read()
        except OSError:
            self._finish(False, "\n[prepmx] .pmx 가 만들어지지 않았습니다 (M-Tx 문법 오류 가능성)\n")
            return False
        result = pmxpatch.apply_patch(res.pmx_text, self._patch_text or "")
        res.patch_report = [f"PMX 패치 {result.applied}/{result.total} 적용"] + result.messages
        res.patched = True
        with open(pmx, "w", encoding="utf-8", newline="\n") as f:
            f.write(result.text)
        res.pmx_final = result.text
        return True

    # ------------------------------------------------------------------
    def _cleanup_patches(self):
        if getattr(self, "_tmpdir", ""):
            shutil.rmtree(self._tmpdir, ignore_errors=True)
        self._tmpdir = ""

    def _log_files(self, res: BuildResult) -> str:
        out = ""
        for name in ("musixtex.log", res.tmp_stem + ".log"):
            p = os.path.join(res.workdir, name)
            if os.path.exists(p):
                try:
                    with open(p, encoding="utf-8", errors="replace") as f:
                        out += f"\n[{name}]\n" + f.read()[-6000:]
                except OSError:
                    pass
        return out

    def _finish(self, ok: bool, extra_log: str = ""):
        res = self.res
        self._timer.stop()
        self._cleanup_patches()
        if self.proc is not None:
            try:
                self._buffer += bytes(self.proc.readAll()).decode("utf-8", "replace")
            except Exception:
                pass
        self.proc = None
        self._queue = []
        res.log = self._buffer + extra_log
        if res.patch_report:
            res.log = "\n".join(res.patch_report) + "\n\n" + res.log
        w = res.workdir
        pdf = os.path.join(w, res.tmp_stem + ".pdf")
        pmx = os.path.join(w, res.tmp_stem + ".pmx")
        res.ok = ok and os.path.exists(pdf) and os.path.exists(pmx) and os.path.getsize(pmx) > 0
        if res.ok:
            res.pdf = pdf
            if not res.pmx_text:
                with open(pmx, encoding="utf-8", errors="replace") as f:
                    res.pmx_text = f.read()
                res.pmx_final = res.pmx_text
            res.pos_path = os.path.join(w, res.tmp_stem + ".mtxpos")
            midi = os.path.join(w, res.tmp_stem + ".mid")
            res.midi = midi if os.path.exists(midi) else ""
        else:
            res.log += self._log_files(res)
            res.error_lines = self.parse_error_lines(res.log, res.line_offset)
        self.finished.emit(res)

    def _error(self, err):
        if err == QProcess.ProcessError.FailedToStart and self._queue:
            prog = self._queue[0][0]
            hint = ("TeX Live/MiKTeX 의 PATH 를 확인하거나 설정 ▸ ‘TeX 경로…’ 에 bin 폴더를 지정하세요." if prog == "musixtex"
                    else "M-Tx 설치(prepmx)와 설정 ▸ ‘TeX 경로…’ 를 확인하세요. 패치 없이 컴파일하려면 ‘PMX 패치 적용’을 끄세요.")
            self._finish(False, f"\n'{prog}' 를 실행할 수 없습니다. {hint}\n")

    def _timeout(self):
        if self.proc:
            self.proc.kill()
            self._finish(False, "\n[시간 초과] 90초 안에 끝나지 않아 중단했습니다 (TeX 무한 루프 가능성).\n")

    @staticmethod
    def parse_error_lines(log: str, line_offset: int) -> List[int]:
        found = []
        for m in re.finditer(r"(?i)\bline\s+(\d+)\b", log):
            n = int(m.group(1)) - (line_offset if int(m.group(1)) > line_offset else 0)
            if n > 0 and n not in found:
                found.append(n)
        return found[:3]


# ====================================================================== 메인 창
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("ktug.org", "MTxMusicTypist")
        self.path: Optional[str] = None
        self.barmap: Optional[mtxmap.BarMap] = None
        self.posmap: Optional[mtxpos.PosMap] = None
        self.compiler = Compiler(self)
        self.compiler.finished.connect(self.on_build_finished)
        self._pending_edits: List[tuple] = []
        self._loading = False
        self._suppress_follow = False
        self.midi_path = ""
        self.helper = None
        self.HelperClass = None
        self.current_bar: Optional[int] = None
        self._crop_proc = None

        self.editor = CodeEditor()
        self.pdf = PdfView()
        edwrap = QWidget()
        edl = QVBoxLayout(edwrap)
        edl.setContentsMargins(0, 0, 0, 0)
        edl.setSpacing(0)
        bar = QWidget()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(4, 3, 4, 3)
        self.btn_build = QPushButton("▶ 빌드")
        self.btn_build.setToolTip("컴파일 (F5)")
        self.btn_build.setAutoDefault(False)
        self.btn_build.clicked.connect(lambda: self.compile())
        bl.addWidget(self.btn_build)
        bl.addStretch(1)
        edl.addWidget(bar)
        self.findbar = FindBar(self.editor)
        edl.addWidget(self.findbar)
        edl.addWidget(self.editor, 1)
        self.editor.set_gutter_width(self.settings.value("gutter_width", 0, type=int))
        self.editor.gutterWidthChanged.connect(lambda w: self.settings.setValue("gutter_width", w))
        self.player = mtxplayer.Player(self)
        self.player.soundfont = self.settings.value("soundfont_path", "")
        self.player.volume = self.settings.value("midi_volume", 0.8, type=float)
        self.player_bar = mtxplayer.PlayerBar(self.player)
        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(edwrap, 1)
        cl.addWidget(self.player_bar)
        self.setCentralWidget(central)
        # PDF 뷰어는 떼어낼 수 있는 도크 (기본은 편집기 오른쪽)
        self.dock_pdf = self._dock("PDF", self.pdf, Qt.DockWidgetArea.RightDockWidgetArea)
        self._pdf_sized = False
        self.player_bar.playRequested.connect(self.play_midi)
        self.player.barChanged.connect(self.on_play_bar)
        self.player.notice.connect(lambda m: self.statusBar().showMessage(m, 7000))

        # 도크: 헤더 / 입력 헬퍼 / 로그
        self.header_panel = HeaderPanel()
        self.header_panel.applyRequested.connect(self.apply_header)
        self.header_panel.reloadRequested.connect(self.reload_header_from_source)
        self.dock_header = self._dock("헤더 설정", self.header_panel, Qt.DockWidgetArea.RightDockWidgetArea)
        self.dock_header.hide()

        self.dock_helper = QDockWidget("음표 입력", self)
        self.dock_helper.setObjectName("dock_helper")
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.dock_helper)
        self._build_helper(0)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.dock_log = self._dock("컴파일 로그", self.log, Qt.DockWidgetArea.BottomDockWidgetArea)
        self.dock_log.hide()

        self.pmx_pristine = ""
        self.pmx_panel = PmxPanel()
        self.pmx_panel.editor.setFont(self.editor.font())
        self.pmx_panel.savePatchRequested.connect(self.save_pmx_patch)
        self.pmx_panel.deletePatchRequested.connect(self.delete_pmx_patch)
        self.pmx_panel.jumpRequested.connect(self.jump_pmx_to_current_bar)
        self.dock_pmx = self._dock("PMX 수정", self.pmx_panel, Qt.DockWidgetArea.RightDockWidgetArea)
        # 배치: [편집기] [PDF] [헤더 설정 / PMX 수정 (위아래)] — 이전의 화면 구성과 같게
        self.splitDockWidget(self.dock_pdf, self.dock_header, Qt.Orientation.Horizontal)
        self.splitDockWidget(self.dock_header, self.dock_pmx, Qt.Orientation.Vertical)
        # Ctrl+W 는 파일 메뉴의 ‘작업 창 닫기’ 하나가 맡는다 (_close_work). 떠 있는 도크가 활성일 때도
        # Qt 가 메인 창의 단축키를 그대로 전달하므로, 도크별 단축키를 따로 두면 겹쳐서(ambiguous) 둘 다 안 먹는다.

        # 편집기에 포커스가 있어도 쓸 수 있는 임시표 단축키 (헬퍼 안에서는 s/f/n/x/= 그대로 동작)
        for keyseq, val in (("Alt+X", "none"), ("Alt+S", "s"), ("Alt+F", "f"), ("Alt+N", "n"),
                            ("Alt+Shift+S", "ss"), ("Alt+Shift+F", "ff")):
            sc = QShortcut(QKeySequence(keyseq), self)
            sc.activated.connect(lambda v=val: self.accidental_hotkey(v))
        sc = QShortcut(QKeySequence("Alt+="), self)
        sc.activated.connect(self.abs_pitch_hotkey)
        self.dock_pmx.hide()

        self._make_actions()
        self.lbl_bar = QLabel("")
        self.lbl_pos = QLabel("")
        self.statusBar().addPermanentWidget(self.lbl_pos)
        self.lbl_pair = QLabel("")
        self.lbl_pair.setStyleSheet("color:#0a7bd6;")
        self.statusBar().insertWidget(0, self.lbl_pair)
        self.editor.pairMessage.connect(self.lbl_pair.setText)
        self.statusBar().addPermanentWidget(self.lbl_bar)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setMaximumWidth(160)
        self.progress.hide()
        self.statusBar().addPermanentWidget(self.progress)

        self.editor.document().contentsChange.connect(self._on_contents_change)
        self.editor.document().modificationChanged.connect(lambda _: self._update_title())
        self.editor.cursorPositionChanged.connect(self._on_cursor_moved)
        self.pdf.sourceRequested.connect(self.on_pdf_click)

        self._follow_timer = QTimer(self)
        self._follow_timer.setSingleShot(True)
        self._follow_timer.timeout.connect(self._follow)
        self._label_timer = QTimer(self)
        self._label_timer.setSingleShot(True)
        self._label_timer.timeout.connect(self._refresh_labels)

        geo = self.settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        else:
            self.resize(1700, 950)
        st = self.settings.value("windowState")
        if st:
            if self.restoreState(st):
                self._pdf_sized = True        # 저장된 배치가 있으면 폭을 건드리지 않는다
        fam = self.settings.value("editor_font_family", "")
        size = self.settings.value("editor_font_size", 12, type=int)
        if fam or size != 12:
            self._set_editor_font(fam, size, save=False)
        self.new_file(ask=False)
        self.dock_pdf.hide()

    # ---------------------------------------------------------------- 구성
    def _dock(self, title, widget, area):
        d = QDockWidget(title, self)
        d.setObjectName("dock_" + title)
        d.setWidget(widget)
        self.addDockWidget(area, d)
        return d

    def _build_helper(self, key: int):
        if self.HelperClass is None:
            try:
                self.HelperClass = _load_helper_class()
            except Exception as e:      # mtxvisualhelper.py 가 없거나 오디오 모듈 문제 등
                lab = QLabel(f"입력 헬퍼를 불러오지 못했습니다:\n{e}")
                lab.setWordWrap(True)
                self.dock_helper.setWidget(lab)
                self.HelperClass = False
                return
        if self.HelperClass is False:
            return
        self.helper = self.HelperClass(key)
        self.helper.textReady.connect(self.insert_from_helper)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(self.helper)
        sc.setMinimumWidth(470)
        self.dock_helper.setWidget(sc)

    def _make_actions(self):
        m = self.menuBar()
        f = m.addMenu("파일")
        self._act(f, "새로 만들기", self.new_file, QKeySequence.StandardKey.New)
        self._act(f, "열기…", self.open_file, QKeySequence.StandardKey.Open)
        self._act(f, "작업 창 닫기 (= 새로 만들기)", self._close_work, QKeySequence("Ctrl+W"))
        self._act(f, "저장", self.save, QKeySequence.StandardKey.Save)
        self._act(f, "다른 이름으로 저장…", self.save_as, QKeySequence("Ctrl+Shift+S"))
        self._act(f, "MIDI 파일 들여오기 (실험적)…", self.import_midi)
        f.addSeparator()
        self.menu_recent = f.addMenu("최근에 연 파일")
        self.menu_recent.aboutToShow.connect(self._fill_recent)
        f.addSeparator()
        self._act(f, "끝내기", self.close, QKeySequence.StandardKey.Quit)

        e = m.addMenu("편집")
        self._act(e, "찾기…", lambda: self.findbar.open_bar(False), QKeySequence.StandardKey.Find)
        self._act(e, "찾기/바꾸기…", lambda: self.findbar.open_bar(True), QKeySequence("Ctrl+H"))
        self._act(e, "다음 찾기", lambda: self.findbar.step(True), QKeySequence("F3"))
        self._act(e, "이전 찾기", lambda: self.findbar.step(False), QKeySequence("Shift+F3"))

        b = m.addMenu("빌드")
        self.act_compile = self._act(b, "컴파일 (PDF 갱신)", self.compile, QKeySequence("F5"))
        self._act(b, "현재 커서 마디로 PDF 이동", lambda: self._follow(force=True), QKeySequence("Ctrl+J"))
        self.act_follow = self._act(b, "커서를 따라 PDF 이동", None, QKeySequence("F6"), checkable=True)
        self.act_follow.setChecked(True)
        self.act_beat = self._act(b, "컴파일 전에 마디 박자 검사", None, checkable=True)
        self.act_beat.setChecked(self.settings.value("beat_check", True, type=bool))
        self._act(b, "마디 박자 검사 (컴파일 안 함)", self.check_beats_now, QKeySequence("Ctrl+Shift+B"))
        self.act_patch = self._act(b, "PMX 패치 적용 (이름.pmx.patch 가 있을 때)", None, checkable=True)
        self.act_patch.setChecked(self.settings.value("use_patch", True, type=bool))
        self._act(b, "PMX 패치 저장 후 컴파일", lambda: self.save_pmx_patch(True), QKeySequence("F7"))
        b.addSeparator()
        self._act(b, "부수 파일 청소… (pdf·log·pmx·tex·mid 삭제, mtx 만 남김)", self.clean_files)
        b.addSeparator()
        self._act(b, "MIDI 재생 / 정지 (커서 마디부터)", self.toggle_midi, QKeySequence("F8"))
        self._act(b, "MIDI 처음부터 재생", lambda: self.play_midi(False), QKeySequence("Shift+F8"))

        v = m.addMenu("보기")
        for dock, key in ((self.dock_helper, "Ctrl+1"), (self.dock_header, "Ctrl+2"), (self.dock_log, "Ctrl+3"), (self.dock_pmx, "Ctrl+4"),
                          (self.dock_pdf, "Ctrl+5")):
            a = dock.toggleViewAction()
            a.setShortcut(QKeySequence(key))
            v.addAction(a)
        v.addSeparator()
        self._act(v, "괄호 짝으로 이동", self._jump_pair, QKeySequence("Ctrl+]"))
        self._act(v, "다음 짝 없는 괄호로 이동", lambda: self._jump_unmatched(True), QKeySequence("Ctrl+Shift+]"))
        self._act(v, "이전 짝 없는 괄호로 이동", lambda: self._jump_unmatched(False), QKeySequence("Ctrl+Shift+["))
        v.addSeparator()
        self._act(v, "PDF 폭 맞춤", lambda: self.pdf.set_zoom(None), QKeySequence("Ctrl+0"))
        self._act(v, "PDF 확대", lambda: self.pdf.zoom_by(1.25), QKeySequence("Ctrl+="))
        self._act(v, "PDF 축소", lambda: self.pdf.zoom_by(0.8), QKeySequence("Ctrl+-"))
        self.act_wrap = self._act(v, "줄 바꿈 표시 & 워드 랩", self._toggle_wrap, QKeySequence("Alt+Z"), checkable=True)
        self.act_wrap.setChecked(self.settings.value("wrap", False, type=bool))
        self.editor.set_wrap(self.act_wrap.isChecked())
        self._act(v, "편집기 글꼴 키우기", lambda: self._font(+1), QKeySequence("Ctrl+Shift+="))
        self._act(v, "편집기 글꼴 줄이기", lambda: self._font(-1), QKeySequence("Ctrl+Shift+-"))

        c = m.addMenu("설정")
        self._act(c, "편집기 글꼴…", self.choose_font)
        self._act(c, "TeX 경로…", self.choose_tex_path)
        self.act_extpdf = self._act(c, "시스템 PDF 뷰어로 보기 (내장 뷰어·PDF 동기화 끄기)", self._toggle_external_pdf, checkable=True)
        self.act_extpdf.setChecked(self.settings.value("external_pdf", False, type=bool))
        self._act(c, "MIDI 사운드폰트 (sf2/sf3)…", self.choose_soundfont)
        self._act(c, "사운드폰트 해제 (기본 소리)", self.clear_soundfont)
        self._act(c, "현재 사운드폰트 확인…", self.show_soundfont)
        c.addSeparator()
        self.act_restore = self._act(c, "시작할 때 마지막 작업 파일 열기", None, checkable=True)
        self.act_restore.setChecked(self.settings.value("restore_last", True, type=bool))
        self.act_restore.toggled.connect(lambda on: self.settings.setValue("restore_last", on))
        self.act_pair = self._act(c, "슬러·타이 짝 강조 (커서가 괄호에 닿으면 짝 표시, 짝 없는 괄호는 밑줄)", self._toggle_pair, checkable=True)
        self.act_pair.setChecked(self.settings.value("pair_hl", True, type=bool))
        self.editor.set_pair_enabled(self.act_pair.isChecked())
        self.act_syntax = self._act(c, "구문 색상 (쉼표·화음·표지·@)", self._toggle_syntax, checkable=True)
        self.act_syntax.setChecked(self.settings.value("syntax_color", True, type=bool))
        self.editor.highlighter.set_enabled(self.act_syntax.isChecked())
        self.act_hook = self._act(c, "PDF 클릭 → 소스 이동 기록 사용 (실험적)", None, checkable=True)
        self.act_hook.setChecked(self.settings.value("use_hook", True, type=bool))

        h = m.addMenu("도움말")
        self._act(h, "사용법", self.show_help)
        h.addSeparator()
        for title, url in DOC_URLS:
            self._act(h, title + " (웹)", lambda _=False, u=url: QDesktopServices.openUrl(QUrl(u)))
        self.menu_texdoc = h.addMenu("설치된 문서 (texdoc)")
        for title, name in TEXDOC_NAMES:
            self._act(self.menu_texdoc, title, lambda _=False, n=name: self.open_texdoc(n))
        h.addSeparator()
        self._act(h, "진단 정보 (패키징 점검)", self.show_diagnostics)

    def _act(self, menu, text, slot, shortcut=None, checkable=False):
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(shortcut)
        a.setCheckable(checkable)
        if slot:
            a.triggered.connect(slot)
        menu.addAction(a)
        return a

    def _set_editor_font(self, family: str, size: int, save=True):
        f = QFont(self.editor.font())
        if family:
            f.setFamily(family)
        f.setStyleHint(QFont.StyleHint.Monospace)
        f.setPointSize(max(7, min(48, size)))
        self.editor.setFont(f)
        self.editor.setTabStopDistance(self.editor.fontMetrics().horizontalAdvance(" ") * 4)
        self.editor._gutter.update()
        self.editor._update_gutter_width()
        if hasattr(self, "pmx_panel"):
            self.pmx_panel.editor.setFont(self.editor.font())
        if save:
            self.settings.setValue("editor_font_family", f.family())
            self.settings.setValue("editor_font_size", f.pointSize())

    def _font(self, d):
        self._set_editor_font("", self.editor.font().pointSize() + d)

    # ---- 부수 파일 청소
    CLEAN_EXTS = (".log", ".pmx", ".tex", ".pdf", ".mid", ".mtxpos", ".aux", ".dvi", ".mx1", ".mx2", ".ps", ".out")

    def clean_candidates(self) -> List[str]:
        """현재 곡 이름에서 만들어진 결과물만 모은다. .mtx 와 .pmx.patch(손으로 고친 PMX 패치)는 절대 포함하지 않는다."""
        if not self.path:
            return []
        d = os.path.dirname(os.path.abspath(self.path))
        stem = os.path.splitext(os.path.basename(self.path))[0]
        tmp = build_stem(stem)
        names = {stem + e for e in self.CLEAN_EXTS} | {stem + "_cropped.pdf", "musixtex.log"}
        out = []
        for n in sorted(os.listdir(d)):
            full = os.path.join(d, n)
            if not os.path.isfile(full) or n.endswith(".pmx.patch") or n == os.path.basename(self.path):
                continue
            if (n in names or n.startswith(tmp + ".")
                    or re.fullmatch(re.escape(stem) + r"\.\d+", n)):
                out.append(full)
        return out

    def clean_files(self):
        if not self.path:
            QMessageBox.information(self, "부수 파일 청소", "저장된 파일이 없습니다. 먼저 저장하세요.")
            return
        if self.compiler.running:
            QMessageBox.information(self, "부수 파일 청소", "컴파일이 진행 중입니다. 끝난 뒤에 다시 하세요.")
            return
        files = self.clean_candidates()
        if not files:
            self.statusBar().showMessage("지울 부수 파일이 없습니다", 5000)
            return
        names = "\n".join("  " + os.path.basename(f) for f in files)
        keep = os.path.basename(self.path)
        patch = os.path.basename(self.patch_path() or "")
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("부수 파일 청소")
        box.setText(f"다음 {len(files)}개 파일을 삭제합니다 (PDF 포함, 되돌릴 수 없음).\n\n{names}\n\n"
                    f"남는 것: {keep}" + (f" 와 {patch} (있으면)" if patch else ""))
        yes = box.addButton("삭제", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("취소", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is not yes:
            return
        # 파일을 쥐고 있는 것부터 놓는다: MIDI 재생, PDF 자르기, PDF 창
        self.player.stop()
        self.player.set_source(None)
        self.midi_path = ""
        self._crop_abort()
        self.pdf.show_rect(None)
        self.dock_pdf.hide()
        self.barmap = None
        self.posmap = None
        self.editor.set_bar_labels({})
        self.editor.set_bar_spans([])
        self.editor.set_error_lines([])
        self.lbl_pos.setText("")
        failed = []
        for f in files:
            try:
                os.remove(f)
            except OSError as e:
                failed.append(f"{os.path.basename(f)}: {e}")
        if failed:
            QMessageBox.warning(self, "일부 삭제 실패", "삭제하지 못한 파일 (다른 프로그램이 열고 있나요?):\n" + "\n".join(failed))
        self.statusBar().showMessage(f"부수 파일 {len(files) - len(failed)}개를 삭제했습니다", 6000)

    def _toggle_pair(self, on: bool = True):
        self.editor.set_pair_enabled(self.act_pair.isChecked())
        self.settings.setValue("pair_hl", self.act_pair.isChecked())

    def _jump_pair(self):
        if not self.editor.jump_to_pair():
            self.statusBar().showMessage("커서가 짝이 있는 ( ) { } [ ] 에 닿아 있지 않습니다", 4000)

    def _jump_unmatched(self, fwd: bool):
        if not self.editor.jump_unmatched(fwd):
            self.statusBar().showMessage("짝 없는 괄호가 없습니다", 4000)

    def _toggle_wrap(self, on: bool = False):
        self.editor.set_wrap(self.act_wrap.isChecked())
        self.settings.setValue("wrap", self.act_wrap.isChecked())

    def _toggle_syntax(self, on: bool):
        self.editor.highlighter.set_enabled(on)
        self.settings.setValue("syntax_color", on)

    def choose_tex_path(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("TeX 경로")
        dlg.setMinimumWidth(520)
        lay = QVBoxLayout(dlg)
        info = QLabel("musixtex / prepmx / pdfcrop 등이 있는 폴더. 컴파일할 때 이 앱이 실행하는 프로그램의 PATH 앞에만\n"
                      "임시로 붙입니다(시스템 PATH 는 바뀌지 않음). 여러 개는 "
                      + (";" if sys.platform == "win32" else ":") + " 로 구분.")
        info.setWordWrap(True)
        lay.addWidget(info)
        row = QHBoxLayout()
        ed = QLineEdit(self.settings.value("tex_path", "", type=str))
        ed.setPlaceholderText(r"예: C:\texlive\2026\bin\windows" if sys.platform == "win32" else "예: /usr/local/texlive/2026/bin/universal-darwin")
        b = QPushButton("폴더 추가…")
        row.addWidget(ed, 1); row.addWidget(b)
        lay.addLayout(row)
        res = QLabel("")
        res.setWordWrap(True)
        lay.addWidget(res)

        def refresh():
            extra = split_tex_path(ed.text())
            p = path_with_extra(extra, os.environ.get("PATH", ""))
            lines = []
            for prog in ("musixtex", "prepmx", "pmxab", "pdfcrop"):
                found = shutil.which(prog, path=p)
                lines.append(f"{'✔' if found else '✘'} {prog}: {found or '찾을 수 없음'}")
            bad = [d for d in extra if not os.path.isdir(d)]
            if bad:
                lines.append("⚠ 없는 폴더: " + ", ".join(bad))
            res.setText("\n".join(lines))

        def add():
            d = QFileDialog.getExistingDirectory(dlg, "TeX bin 폴더", ed.text().split(os.pathsep)[0] if ed.text() else "")
            if d:
                sep = ";" if sys.platform == "win32" else ":"
                ed.setText((ed.text().rstrip(sep + " ") + sep if ed.text().strip() else "") + d)

        ed.textChanged.connect(refresh)
        b.clicked.connect(add)
        refresh()
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec():
            self.settings.setValue("tex_path", ed.text().strip())
            self.statusBar().showMessage("TeX 경로를 저장했습니다 (다음 컴파일부터 적용)", 5000)

    def choose_font(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("편집기 글꼴")
        fl = QFormLayout(dlg)
        combo = QFontComboBox()
        combo.setFontFilters(QFontComboBox.FontFilter.MonospacedFonts)
        combo.setCurrentFont(self.editor.font())
        spin = QSpinBox()
        spin.setRange(7, 48)
        spin.setValue(self.editor.font().pointSize())
        fl.addRow("모노스페이스 글꼴", combo)
        fl.addRow("크기(pt)", spin)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        fl.addRow(bb)
        if dlg.exec():
            self._set_editor_font(combo.currentFont().family(), spin.value())

    # ---------------------------------------------------------------- 파일
    def _update_title(self):
        name = os.path.basename(self.path) if self.path else "제목 없음"
        self.setWindowTitle(f"{name}[*] — mtxMusicTypist {__version__}")
        self.setWindowModified(self.editor.document().isModified())

    def _maybe_save(self) -> bool:
        if not self.editor.document().isModified():
            return True
        r = QMessageBox.question(self, "저장", "변경 내용을 저장할까요?",
                                 QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                                 | QMessageBox.StandardButton.Cancel)
        if r == QMessageBox.StandardButton.Save:
            return self.save()
        return r == QMessageBox.StandardButton.Discard

    def _set_text(self, text: str):
        self._loading = True
        self.editor.setPlainText(text)
        self.editor.document().setModified(False)
        self._loading = False
        self.barmap = None
        self.posmap = None
        self.editor.set_bar_labels({})
        self.editor.set_bar_spans([])
        self.editor.set_error_lines([])
        self.pdf.show_rect(None)
        self.dock_pdf.hide()                      # 새로 열면 컴파일 전까지 PDF 창을 가린다
        self.midi_path = ""
        self.player.set_source(None)
        self.reload_header_from_source()

    def _show_pdf(self):
        self.dock_pdf.show()
        if not self._pdf_sized and not self.dock_pdf.isFloating():
            self._pdf_sized = True
            QTimer.singleShot(0, lambda: self.resizeDocks([self.dock_pdf, self.dock_header], [620, 330],
                                                         Qt.Orientation.Horizontal))

    def _close_work(self):
        """Ctrl+W: 떠 있는 도크면 닫기, 붙어 있는 도크면 아무 일 없음, 작업 창이면 새로 만들기와 같다."""
        docks = (self.dock_helper, self.dock_header, self.dock_log, self.dock_pmx, self.dock_pdf)
        aw = QApplication.activeWindow()
        if aw in docks and aw.isFloating():          # 떠 있는 도크: 그 도크를 닫는다
            aw.close()
            return
        w = QApplication.focusWidget()
        while w is not None:
            if w in docks:
                return
            w = w.parentWidget()
        self.new_file()

    def new_file(self, ask=True):
        if ask and not self._maybe_save():
            return
        self.path = None
        st = mtxheader.default_state()
        st['meter_text'] = '4/4'
        st['style'] = 'Singer'
        st['midi'] = True                    # 새 악곡은 MIDI 생성을 기본으로 켠다
        self._set_text(mtxheader.apply_state("", st))
        self._update_title()
        self.dock_header.show()

    def open_file(self):
        if not self._maybe_save():
            return
        d = self.settings.value("last_dir", os.path.expanduser("~"))
        p, _ = QFileDialog.getOpenFileName(self, "M-Tx 파일 열기", d, "M-Tx (*.mtx);;모든 파일 (*)")
        if p:
            self.load_file(p)

    def import_midi(self):
        """MIDI -> M-Tx 변환(실험적). 경고·조성 강제 대화상자 -> 파일 선택 -> 새 문서로 열기"""
        try:
            import mtxmidi
        except ImportError as e:                                  # mido 가 없을 때
            QMessageBox.warning(self, "MIDI 들여오기", f"변환 모듈을 불러올 수 없습니다: {e}")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("MIDI 파일 들여오기 (실험적)")
        lay = QVBoxLayout(dlg)
        warn = QLabel("MIDI 파일의 정보는 조판 정보와 동일하지 않으므로\n완전한 변환을 보증하지 않습니다.")
        warn.setStyleSheet("color:#b35c00; font-weight:bold;")
        lay.addWidget(warn)
        from PyQt6.QtWidgets import QCheckBox
        chk = QCheckBox("조성 강제")
        row = QHBoxLayout()
        row.addWidget(chk)
        spin = QSpinBox()
        spin.setRange(-7, 7)
        spin.setEnabled(False)
        spin.setToolTip("# 의 수는 양수, b 의 수는 음수 (예: -3 은 b 세 개)")
        spin.setSpecialValueText("0 (조표 없음)")
        row.addWidget(spin)
        row.addWidget(QLabel("(+ : 샵 개수, − : 플랫 개수)"))
        row.addStretch(1)
        lay.addLayout(row)
        chk.toggled.connect(spin.setEnabled)
        note = QLabel("강제하지 않으면 MIDI 의 조표를 쓰고, 없으면 음 분포로 추정합니다.")
        note.setStyleSheet("color:gray;")
        lay.addWidget(note)
        drums = QCheckBox("드럼(타악기) 트랙도 변환 — 악기별 오선 위치에 그림, 소리 높이는 아님")
        lay.addWidget(drums)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText("MIDI 파일 선택…")
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        key = spin.value() if chk.isChecked() else None
        want_drums = drums.isChecked()
        if not self._maybe_save():
            return
        d = self.settings.value("last_dir", os.path.expanduser("~"))
        p, _ = QFileDialog.getOpenFileName(self, "MIDI 파일 선택", d, "MIDI (*.mid *.midi);;모든 파일 (*)")
        if not p:
            return
        try:
            text, info = mtxmidi.convert(p, key=key, drums=want_drums)
        except Exception as e:                                    # 손상된 MIDI 등
            QMessageBox.warning(self, "MIDI 들여오기 실패", f"{type(e).__name__}: {e}")
            return
        self.settings.setValue("last_dir", os.path.dirname(p))
        self.path = None
        self._set_text(text)
        self.editor.document().setModified(True)
        self._update_title()
        msg = f"{info['bars']}마디, {len(info['tracks'])}개 보표, {info['meter']}, 조표 {info['fifths']:+d}"
        if info["warnings"]:
            msg += "\n\n" + "\n".join("· " + w for w in info["warnings"])
        QMessageBox.information(self, "MIDI 들여오기", msg + "\n\n새 문서로 열었습니다. 저장한 뒤 컴파일하세요.")

    def load_file(self, p: str):
        try:
            with open(p, encoding="utf-8") as f:
                text = f.read().replace("\r\n", "\n")
        except (OSError, UnicodeDecodeError) as e:
            QMessageBox.warning(self, "열기 실패", str(e))
            return
        self.path = p
        self.settings.setValue("last_dir", os.path.dirname(p))
        self._add_recent(p)
        self._set_text(text)
        self._update_title()
        # 이전 컴파일로 생긴 MIDI 가 있으면 바로 재생할 수 있게 한다
        # (마디 단위 시작 위치는 다시 컴파일해야 켜짐 — 처음부터 재생만 가능)
        mid = os.path.splitext(p)[0] + ".mid"
        if os.path.exists(mid):
            self.midi_path = mid
            self.player.set_source(mid)
            self.statusBar().showMessage("이전에 만든 MIDI 를 불러왔습니다 (마디 위치 재생은 컴파일 후)", 6000)
        # 이전 컴파일의 PDF 가 있으면 함께 보여 준다 (마디 대응표가 없으니 보기만, 동기화는 컴파일 후)
        pdf = os.path.splitext(p)[0] + ".pdf"
        if os.path.exists(pdf) and not self.external_pdf() and self.pdf.load_file(pdf):
            self._show_pdf()

    def save(self) -> bool:
        if not self.path:
            return self.save_as()
        try:
            with open(self.path, "w", encoding="utf-8", newline="\n") as f:
                f.write(self.editor.toPlainText())
        except OSError as e:
            QMessageBox.warning(self, "저장 실패", str(e))
            return False
        self.editor.document().setModified(False)
        return True

    def save_as(self) -> bool:
        d = self.settings.value("last_dir", os.path.expanduser("~"))
        p, _ = QFileDialog.getSaveFileName(self, "다른 이름으로 저장", d, "M-Tx (*.mtx)")
        if not p:
            return False
        if not p.lower().endswith(".mtx"):
            p += ".mtx"
        self.path = p
        self.settings.setValue("last_dir", os.path.dirname(p))
        self._add_recent(p)
        ok = self.save()
        self._update_title()
        return ok

    # ---- 최근 파일 / 마지막 작업 파일 복원
    RECENT_MAX = 10

    def _recent_list(self) -> List[str]:
        v = self.settings.value("recent_files", [])
        if isinstance(v, str):
            v = [v]
        return [x for x in (v or []) if isinstance(x, str) and x]

    def _add_recent(self, p: str):
        p = os.path.abspath(p)
        lst = [x for x in self._recent_list() if x != p]
        self.settings.setValue("recent_files", [p] + lst[:self.RECENT_MAX - 1])

    def _fill_recent(self):
        self.menu_recent.clear()
        lst = [x for x in self._recent_list() if os.path.isfile(x)]
        for p in lst:
            a = self.menu_recent.addAction(f"{os.path.basename(p)}    —  {os.path.dirname(p)}")
            a.triggered.connect(lambda _=False, q=p: self._open_recent(q))
        if not lst:
            self.menu_recent.addAction("(없음)").setEnabled(False)
        else:
            self.menu_recent.addSeparator()
            self.menu_recent.addAction("목록 지우기").triggered.connect(
                lambda: self.settings.setValue("recent_files", []))

    def _open_recent(self, p: str):
        if self._maybe_save():
            self.load_file(p)

    def restore_last(self) -> bool:
        """지난번 닫을 때의 작업 파일을 커서·스크롤 위치까지 복원. 파일이 없으면 조용히 빈 문서로 둔다."""
        if not self.act_restore.isChecked():
            return False
        p = self.settings.value("last_file", "", type=str)
        if not p or not os.path.isfile(p):
            return False
        self.load_file(p)
        if self.path != p:
            return False
        cur = self.editor.textCursor()
        cur.setPosition(min(int(self.settings.value("last_cursor", 0) or 0),
                            max(0, self.editor.document().characterCount() - 1)))
        self.editor.setTextCursor(cur)
        sb = self.editor.verticalScrollBar()
        sb.setValue(int(self.settings.value("last_scroll", 0) or 0))
        self.editor.document().setModified(False)
        self.statusBar().showMessage(f"지난번 작업 파일을 열었습니다: {os.path.basename(p)}", 5000)
        return True

    def closeEvent(self, ev):
        if not self._maybe_save():
            ev.ignore()
            return
        self.settings.setValue("last_file", self.path or "")
        self.settings.setValue("last_cursor", self.editor.textCursor().position())
        self.settings.setValue("last_scroll", self.editor.verticalScrollBar().value())
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("windowState", self.saveState())
        self.settings.setValue("use_hook", self.act_hook.isChecked())
        self.settings.setValue("beat_check", self.act_beat.isChecked())
        self.settings.setValue("syntax_color", self.act_syntax.isChecked())
        self.settings.setValue("use_patch", self.act_patch.isChecked())
        self.settings.setValue("midi_volume", self.player.volume)
        self.player.stop()
        ev.accept()

    # ---------------------------------------------------------------- 헤더
    def reload_header_from_source(self):
        st, _ = mtxheader.parse_header(self.editor.toPlainText())
        self.header_panel.set_state(st)
        if self.HelperClass is not False and (self.helper is None or self.helper.initial_key != st['key']):
            self._build_helper(st['key'])

    def apply_header(self):
        st = self.header_panel.state()
        new = mtxheader.apply_state(self.editor.toPlainText(), st)
        self.editor.replace_minimal(new)
        self.statusBar().showMessage("헤더를 소스에 적용했습니다 (Ctrl+Z 로 되돌릴 수 있음)", 4000)
        self.reload_header_from_source()     # 자동 삽입된 매크로 등을 패널에 반영

    # ---------------------------------------------------------------- 입력 헬퍼
    ACC_SYMBOL = {"none": "X", "s": "♯", "f": "♭", "n": "♮", "ss": "𝄪", "ff": "𝄫"}

    def accidental_hotkey(self, val: str):
        """Alt+X/S/F/N (+Shift: 𝄪 𝄫): 헬퍼의 임시표 버튼을 누른다(같은 키를 다시 누르면 해제)."""
        h = self.helper
        if h is None:
            self.statusBar().showMessage("음표 입력 도크가 없습니다", 3000)
            return
        for btn in h.acc_btn_group.buttons():
            if btn.property("acc_val") == val:
                btn.click()
                break
        on = [self.ACC_SYMBOL.get(b.property("acc_val"), "?") for b in h.acc_btn_group.buttons() if b.isChecked()]
        self.statusBar().showMessage("임시표: " + (" ".join(on) if on else "없음") + "  (다음 음표 한 번에만 적용)", 4000)

    def abs_pitch_hotkey(self):
        """Alt+= : 헬퍼의 ‘=’ 버튼 토글."""
        h = self.helper
        if h is None:
            return
        h.abs_pitch_btn.click()
        self.statusBar().showMessage("‘=’ 버튼: " + ("켜짐" if h.abs_pitch_btn.isChecked() else "꺼짐"), 4000)

    def insert_from_helper(self, text: str):
        self.editor.insert_snippet(text)

    # ---------------------------------------------------------------- 컴파일
    def compile(self, *_, check: bool = True):
        if self.compiler.running:
            return
        self._crop_abort()
        if not self.path and not self.save_as():
            return
        if not self.save():
            return
        if self.pmx_panel.is_modified():
            r = QMessageBox.question(self, "PMX 수정", "PMX 수정 내용이 아직 패치로 저장되지 않았습니다.",
                                     QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                                     | QMessageBox.StandardButton.Cancel)
            if r == QMessageBox.StandardButton.Cancel:
                return
            if r == QMessageBox.StandardButton.Save:
                self.save_pmx_patch(False, recompile=False)
            else:
                self.pmx_panel.editor.document().setModified(False)
        text = self.editor.toPlainText()
        if check and self.act_beat.isChecked() and not self._beats_ok(text):
            return
        st, _ = mtxheader.parse_header(text)
        patch_text = None
        pp = self.patch_path()
        if self.act_patch.isChecked() and pp and os.path.exists(pp):
            try:
                with open(pp, encoding="utf-8") as f:
                    patch_text = f.read()
            except OSError:
                patch_text = None
            if patch_text is not None and not patch_text.strip():
                patch_text = None
        self._pending_edits = []
        self.editor.set_error_lines([])
        self.act_compile.setEnabled(False)
        self.btn_build.setEnabled(False)
        self.progress.show()
        self.statusBar().showMessage("컴파일 중…")
        self.compiler.tex_path = self.settings.value("tex_path", "", type=str)
        self.compiler.start(self.path, text, self.act_hook.isChecked(), st['luatex'], st['hangul'], patch_text)

    # ---------------------------------------------------------------- 박자 검사
    def _beats_ok(self, text: str, ask: bool = True) -> bool:
        """마디 박자를 검사한다. 문제가 없거나 사용자가 ‘그래도 컴파일’ 을 고르면 True."""
        try:
            issues = mtxbeats.check(mtxmap.qt_safe(text))
        except Exception as e:                       # 검사기 오류가 컴파일을 막지는 않는다
            self.statusBar().showMessage(f"박자 검사를 건너뜀: {e}", 5000)
            return True
        if not issues:
            return True
        return self._show_beat_issues(issues, ask)

    def _show_beat_issues(self, issues, ask: bool) -> bool:
        lines = sorted({i.line for i in issues})
        self.editor.set_error_lines(lines)
        dlg = QDialog(self)
        dlg.setWindowTitle("마디 박자 검사")
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(f"박자가 맞지 않는 마디가 {len(issues)}곳 있습니다. 목록을 고르면 편집기에서 그 마디를 보여 줍니다.\n"
                             "(첫 마디의 못갖춘마디와 마지막 마디는 짧아도 검사하지 않습니다. PMX 의 free beat 처럼 일부러 그런 곳은 무시해도 됩니다.)"))
        lst = QListWidget()
        shown = issues[:300]
        for it in shown:
            lst.addItem(it.describe())
        lay.addWidget(lst)
        if len(issues) > len(shown):
            lay.addWidget(QLabel(f"… 그 밖에 {len(issues) - len(shown)}곳"))

        def jump(row: int):
            if 0 <= row < len(shown):
                it = shown[row]
                c = self.editor.textCursor()
                c.setPosition(it.start)
                c.setPosition(it.end, c.MoveMode.KeepAnchor)
                self.editor.setTextCursor(c)
                self.editor.centerCursor()
        lst.currentRowChanged.connect(jump)
        row = QHBoxLayout()
        row.addStretch(1)
        if ask:
            b_go = QPushButton("그래도 컴파일")
            b_go.clicked.connect(dlg.accept)
            row.addWidget(b_go)
        b_no = QPushButton("수정하러 가기" if ask else "닫기")
        b_no.setDefault(True)
        b_no.clicked.connect(dlg.reject)
        row.addWidget(b_no)
        lay.addLayout(row)
        dlg.resize(560, 360)
        lst.setCurrentRow(0)
        dlg.move(self.geometry().right() - dlg.width() - 40, self.geometry().bottom() - dlg.height() - 80)
        ok = dlg.exec() == QDialog.DialogCode.Accepted
        if ok:
            self.editor.set_error_lines([])
        return ok

    def check_beats_now(self):
        text = self.editor.toPlainText()
        try:
            issues = mtxbeats.check(mtxmap.qt_safe(text))
        except Exception as e:
            QMessageBox.warning(self, "박자 검사", f"검사 중 오류: {e}")
            return
        if not issues:
            self.editor.set_error_lines([])
            self.statusBar().showMessage("마디 박자 검사: 문제 없음", 5000)
            return
        self._show_beat_issues(issues, ask=False)

    # ---------------------------------------------------------------- PMX 패치
    def patch_path(self) -> Optional[str]:
        return (os.path.splitext(self.path)[0] + ".pmx.patch") if self.path else None

    def save_pmx_patch(self, compile_after: bool, recompile: bool = True):
        if not self.pmx_pristine:
            QMessageBox.information(self, "PMX 수정", "먼저 F5 로 컴파일해서 pmx 를 만들어야 합니다.")
            return
        if not self.path and not self.save_as():
            return
        patch = pmxpatch.make_patch(self.pmx_pristine, self.pmx_panel.text())
        pp = self.patch_path()
        try:
            if patch:
                with open(pp, "w", encoding="utf-8", newline="\n") as f:
                    f.write(patch)
                self.statusBar().showMessage(f"PMX 패치 저장: {os.path.basename(pp)}", 5000)
            elif os.path.exists(pp):
                os.remove(pp)
                self.statusBar().showMessage("원본과 같아서 패치를 삭제했습니다", 5000)
        except OSError as e:
            QMessageBox.warning(self, "패치 저장 실패", str(e))
            return
        self.pmx_panel.editor.document().setModified(False)
        if compile_after and recompile:
            self.compile(check=False)

    def delete_pmx_patch(self):
        pp = self.patch_path()
        if not pp or not os.path.exists(pp):
            self.statusBar().showMessage("삭제할 패치가 없습니다", 3000)
            return
        if QMessageBox.question(self, "패치 삭제", f"{os.path.basename(pp)} 를 삭제할까요?") \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            os.remove(pp)
        except OSError as e:
            QMessageBox.warning(self, "삭제 실패", str(e))
            return
        if self.pmx_pristine:
            self.pmx_panel.set_text(self.pmx_pristine, "패치 없음 — 원본 pmx")
        self.statusBar().showMessage("패치를 삭제했습니다 (다음 컴파일부터 원본 pmx)", 5000)

    def jump_pmx_to_current_bar(self):
        if self.current_bar is None:
            return
        ln = pmxpatch.find_bar_line(self.pmx_panel.text(), self.current_bar)
        if ln is not None:
            self.pmx_panel.scroll_to_line(ln)

    def on_build_finished(self, res: BuildResult):
        self.act_compile.setEnabled(True)
        self.btn_build.setEnabled(True)
        self.progress.hide()
        self.log.setPlainText(res.log)
        w = res.workdir

        def cleanup():
            for f in glob.glob(os.path.join(w, glob.escape(res.tmp_stem) + ".*")):
                try: os.remove(f)
                except OSError: pass

        if not res.ok:
            cleanup()
            self.dock_log.show()
            self.editor.set_error_lines(res.error_lines)
            if res.error_lines:
                self.editor.goto_offset(self.editor.document().findBlockByNumber(res.error_lines[0] - 1).position())
            self.statusBar().showMessage("컴파일 실패 — 로그를 확인하세요", 8000)
            return

        # 1) 결과물을 원래 이름으로
        stem_path = os.path.join(w, res.stem)
        pdf_final = stem_path + ".pdf"
        pdf_view = res.pdf
        with open(res.pdf, "rb") as f:
            pdf_bytes = f.read()
        try:
            shutil.copy(res.pdf, pdf_final)
            shutil.copy(os.path.join(w, res.tmp_stem + ".pmx"), stem_path + ".pmx")
        except OSError as e:
            self.log.appendPlainText(f"\n(결과 복사 실패: {e})")
        self.midi_path = ""
        if res.midi:
            try:
                shutil.copy(res.midi, stem_path + ".mid")
                self.midi_path = stem_path + ".mid"
            except OSError:
                pass
        st, _ = mtxheader.parse_header(res.snapshot)
        want_crop = bool(st['crop'])         # 미리보기는 자르지 않은 PDF, 결과 파일만 자른다 (화면 갱신 뒤에 비동기로)

        # 2) 대응표 (컴파일 순간의 소스 기준 -> 이후 편집은 apply_edit 로 반영)
        bm = mtxmap.build_barmap(mtxmap.qt_safe(res.snapshot), res.pmx_text, res.line_offset)
        for (pos, rem, add) in self._pending_edits:
            bm.apply_edit(pos, rem, add)
        self._pending_edits = []
        self.barmap = bm
        self._refresh_labels()

        # 3) 위치 기록
        self.posmap = None
        pos_msg = "PDF 클릭→소스: 꺼짐"
        if res.hook:
            pm = mtxpos.PosMap.load(res.pos_path, bm.order)
            if pm.ok:
                self.posmap = pm
                pos_msg = "PDF 클릭→소스: 켜짐 (마디를 두 번 클릭)"
            elif pm.message:
                pos_msg = pm.message
        self.lbl_pos.setText(pos_msg)

        # 3.2) PMX 수정 패널: 원본은 보관, 화면에는 실제로 조판한 pmx
        self.pmx_pristine = res.pmx_text
        if res.patched:
            nfail = [m for m in res.patch_report[1:] if "건너뜀" in m]
            info = f"{res.patch_report[0]} — 소스: {os.path.basename(self.patch_path() or '')}"
            if nfail:
                info += f"  (건너뜀 {len(nfail)}개, 로그 참조)"
                self.dock_log.show()
        else:
            pp = self.patch_path()
            info = ("패치 있음이지만 ‘PMX 패치 적용’이 꺼져 있어 원본 pmx" if pp and os.path.exists(pp)
                    else "패치 없음 — 여기서 pmx 를 고치고 ‘패치 저장 후 컴파일’(F7)")
        self.pmx_panel.set_text(res.pmx_final or res.pmx_text, info)

        # 3.5) MIDI: 마디 시작 시간표 계산 (못갖춘마디는 pmx 헤더에서)
        note = self.player.set_source(self.midi_path or None, len(bm.order),
                                      mtxplayer.parse_pickup_beats(res.pmx_text))
        if note and self.midi_path:
            self.log.appendPlainText(f"\n[MIDI] 마디 위치 기능 꺼짐: {note}")

        # 4) PDF 표시 (메모리에서 읽으므로 파일이 잠기지 않는다)
        ext = self.external_pdf()
        if ext:
            self.dock_pdf.hide()
        else:
            if self.dock_pdf.isHidden():
                self._show_pdf()
            if not self.pdf.load_bytes(pdf_bytes):
                QDesktopServices.openUrl(QUrl.fromLocalFile(pdf_final))
        cleanup()

        msg = f"컴파일 완료 — 마디 {len(bm.bars)}개"
        approx = [b for b in bm.bars.values() if not b.exact]
        if approx:
            msg += f" (줄 단위 근사 {len(approx)}개)"
        self.statusBar().showMessage(msg, 8000)
        if bm.warnings:
            self.log.appendPlainText("\n[마디 대응 경고]\n" + "\n".join(bm.warnings))
        self._follow(force=True)
        if want_crop:
            self._crop_async(pdf_final)          # 외부 뷰어는 자르기가 끝난 뒤 연다
        elif ext:
            self._external_open(pdf_final)

    def _crop_env(self) -> dict:
        env = os.environ.copy()
        env.update(TMP=".", TEMP=".", TMPDIR=".")
        child_env_fix(env)
        if sys.platform == "darwin":
            env["PATH"] = path_with_extra(["/opt/homebrew/bin", "/usr/local/bin", "/Library/TeX/texbin"], env.get("PATH", ""))
        extra = split_tex_path(self.settings.value("tex_path", "", type=str))
        if extra:
            env["PATH"] = path_with_extra(extra, env.get("PATH", ""))
        return env

    def _crop_async(self, pdf_path: str):
        """결과 PDF 를 pdfcrop 으로 제자리에서 자른다. 화면 갱신이 끝난 뒤 백그라운드로 돌고, 결과를 로그/상태줄에 알린다."""
        self._crop_abort()
        env = self._crop_env()
        exe = shutil.which("pdfcrop", path=env.get("PATH", ""))
        if not exe:
            self.log.appendPlainText("\n[pdfcrop] 찾을 수 없어 자르지 못했습니다 — TeX Live 의 pdfcrop 과 Ghostscript 가 필요합니다. "
                                     "설정 ▸ TeX 경로를 확인하세요")
            self.statusBar().showMessage("PDF 여백 자르기 실패: pdfcrop 을 찾을 수 없음", 8000)
            return
        tmp = pdf_path[:-4] + "_cropped.pdf"
        qenv = QProcessEnvironment()
        for k, v in env.items():
            qenv.insert(k, v)
        p = QProcess(self)
        p.setProcessEnvironment(qenv)
        p.setWorkingDirectory(os.path.dirname(pdf_path))
        p.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._crop_proc, self._crop_tmp, self._crop_target = p, tmp, pdf_path
        self.log.appendPlainText(f"\n[pdfcrop] {exe} {os.path.basename(pdf_path)} {os.path.basename(tmp)}")
        self.statusBar().showMessage("PDF 여백 자르는 중…")
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: p.kill())
        timer.start(60_000)

        def done(code, status):
            timer.stop()
            out = bytes(p.readAll()).decode("utf-8", "replace").strip()
            if self._crop_proc is not p:                 # 취소된 것
                return
            self._crop_proc = None
            if status != QProcess.ExitStatus.NormalExit or code != 0 or not os.path.exists(tmp):
                why = "시간 초과(60초)" if status != QProcess.ExitStatus.NormalExit else f"코드 {code}"
                self.log.appendPlainText(f"[pdfcrop] 실패 ({why})\n{out[-600:]}")
                self.statusBar().showMessage("PDF 여백 자르기 실패 — 로그 참고 (자르지 않은 PDF 는 그대로 있음)", 8000)
            else:
                try:
                    os.replace(tmp, pdf_path)
                    self.log.appendPlainText("[pdfcrop] 완료")
                    self.statusBar().showMessage("PDF 여백 자르기 완료", 5000)
                except OSError as e:
                    self.log.appendPlainText(f"[pdfcrop] 결과를 덮어쓰지 못했습니다(다른 프로그램이 PDF 를 열고 있나요?): {e}")
                    self.statusBar().showMessage("PDF 여백 자르기: 덮어쓰기 실패 — 로그 참고", 8000)
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            p.deleteLater()
            if self.external_pdf():
                self._external_open(pdf_path)

        def failed(err):
            if err == QProcess.ProcessError.FailedToStart and self._crop_proc is p:
                timer.stop()
                self._crop_proc = None
                self.log.appendPlainText("[pdfcrop] 실행하지 못했습니다")
                self.statusBar().showMessage("PDF 여백 자르기 실패: 실행 불가", 8000)

        p.finished.connect(done)
        p.errorOccurred.connect(failed)
        p.start(exe, [os.path.basename(pdf_path), os.path.basename(tmp)])

    def external_pdf(self) -> bool:
        return self.act_extpdf.isChecked()

    def _external_open(self, pdf_path: str):
        """시스템 PDF 뷰어로 연다(빌드할 때마다). 뷰어를 닫았어도 다시 열리고, 열려 있으면 보통 같은 창을 다시 읽는다."""
        QDesktopServices.openUrl(QUrl.fromLocalFile(pdf_path))

    def _toggle_external_pdf(self):
        on = self.act_extpdf.isChecked()
        self.settings.setValue("external_pdf", on)
        if on:
            self.dock_pdf.hide()
            self.statusBar().showMessage("시스템 PDF 뷰어를 사용합니다 — 내장 뷰어와 PDF↔소스 동기화는 꺼집니다", 7000)
        else:
            pdf = os.path.splitext(self.path)[0] + ".pdf" if self.path else ""
            if pdf and os.path.exists(pdf) and self.pdf.load_file(pdf):
                self._show_pdf()
            self.statusBar().showMessage("내장 PDF 뷰어를 사용합니다", 5000)

    def _crop_abort(self):
        p = getattr(self, "_crop_proc", None)
        if p is not None:
            self._crop_proc = None
            p.kill()
            try:
                os.remove(self._crop_tmp)
            except OSError:
                pass

    # ---------------------------------------------------------------- 편집 추적 / 마디 이동
    def _on_contents_change(self, pos, removed, added):
        if self._loading:
            return
        if self.compiler.running:
            self._pending_edits.append((pos, removed, added))
        if self.barmap:
            self.barmap.apply_edit(pos, removed, added)
            self._label_timer.start(250)

    def _refresh_labels(self):
        if not self.barmap:
            self.editor.set_bar_labels({})
            return
        doc = self.editor.document()
        per_line = {}
        for n in self.barmap.order:
            b = self.barmap.bars[n]
            if b.start <= 0 and not b.spans():
                continue
            blk = doc.findBlock(b.start).blockNumber()
            per_line.setdefault(blk, []).append(n)
        labels = {}
        for ln, ns in per_line.items():
            labels[ln] = str(ns[0]) if len(ns) == 1 else f"{ns[0]}–{ns[-1]}"
        self.editor.set_bar_labels(labels)

    def _on_cursor_moved(self):
        if self._suppress_follow:
            return
        self._follow_timer.start(120)

    def _follow(self, force=False):
        if not self.barmap:
            return
        bar = self.barmap.bar_at_offset(self.editor.textCursor().position())
        if bar is None:
            return
        self.current_bar = bar
        self.editor.set_bar_spans(self.barmap.spans_for_bar(bar))
        info = self.barmap.bars.get(bar)
        approx = "≈" if info and not info.exact else ""
        self.lbl_bar.setText(f"마디 {approx}{bar}")
        if (force or self.act_follow.isChecked()) and self.posmap and self.posmap.ok:
            self.pdf.show_rect(self.posmap.rect_for_bar(bar), scroll=True)
        if self.dock_pmx.isVisible() and self.pmx_pristine and not self.pmx_panel.editor.hasFocus():
            self.jump_pmx_to_current_bar()

    def on_pdf_click(self, page: int, x: float, y: float):
        if not (self.posmap and self.posmap.ok and self.barmap):
            self.statusBar().showMessage("이 PDF 는 위치 정보가 없습니다 — F5 로 다시 컴파일하세요 "
                                         "(‘PDF 클릭 → 소스 이동 기록’ 켜기)", 6000)
            return
        bar = self.posmap.bar_at(page, x, y)
        if bar is None or not self.barmap.has(bar):
            self.statusBar().showMessage("해당 위치의 마디를 찾지 못했습니다", 3000)
            return
        info = self.barmap.bars[bar]
        spans = info.spans()
        if not spans:
            return
        self._suppress_follow = True
        self.editor.goto_offset(info.start)
        self.editor.set_bar_spans(spans)
        self.lbl_bar.setText(f"마디 {'≈' if not info.exact else ''}{bar}")
        self.pdf.show_rect(self.posmap.rect_for_bar(bar), scroll=False)
        QTimer.singleShot(250, lambda: setattr(self, "_suppress_follow", False))

    # ---------------------------------------------------------------- MIDI
    def toggle_midi(self):
        if self.player.state == "stopped":
            self.play_midi(True)
        else:
            self.player.stop()

    def _cursor_bar_index(self) -> Optional[int]:
        if not self.barmap:
            return None
        bar = self.barmap.bar_at_offset(self.editor.textCursor().position())
        order = self.barmap.order
        return order.index(bar) if bar in order else None

    def play_midi(self, from_cursor: bool = True):
        idx = self._cursor_bar_index() if from_cursor else None
        if from_cursor and idx is None and self.player.has_midi:
            self.statusBar().showMessage("컴파일 후에야 마디 위치를 알 수 있어 처음부터 재생합니다", 4000)
        self.player.play(bar_index=idx)

    def on_play_bar(self, idx: int):
        """재생 중인 마디를 PDF/소스에서 표시 (커서는 옮기지 않는다)."""
        if not self.barmap or not (0 <= idx < len(self.barmap.order)):
            return
        bar = self.barmap.order[idx]
        self.player_bar.show_bar(bar)
        self.editor.set_bar_spans(self.barmap.spans_for_bar(bar))
        if self.posmap and self.posmap.ok:
            self.pdf.show_rect(self.posmap.rect_for_bar(bar), scroll=True)

    def choose_soundfont(self):
        d = os.path.dirname(self.player.soundfont) if self.player.soundfont else self.settings.value("last_dir", "")
        p, _ = QFileDialog.getOpenFileName(self, "사운드폰트 선택", d, "SoundFont (*.sf2 *.sf3);;모든 파일 (*)")
        if p:
            self.player.soundfont = p
            self.settings.setValue("soundfont_path", p)
            self.statusBar().showMessage(f"사운드폰트: {os.path.basename(p)} (다음 재생부터 적용)", 5000)

    SOUNDFONT_LINKS = [
        ("FluidR3_GM.sf2 내려받기 (SourceForge, PianoBooster)",
         "https://sourceforge.net/projects/pianobooster/files/pianobooster/1.0.0/FluidR3_GM.sf2/download"),
        ("GeneralUser GS 2.0.3 내려받기 (Google Drive)",
         "https://drive.google.com/uc?export=download&id=12ZzM70Nxnr4vqyUF0bbRKE_HXQgLRNid"),
    ]

    def show_soundfont(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("현재 사운드폰트")
        lay = QVBoxLayout(dlg)
        lab = QLabel(self.player.soundfont_report())
        lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lab.setWordWrap(True)
        lay.addWidget(lab)
        lay.addWidget(QLabel("대표적인 사운드폰트 (누르면 브라우저로 열립니다):"))
        for title, url in self.SOUNDFONT_LINKS:
            b = QPushButton(title)
            b.setToolTip(url)
            b.setAutoDefault(False)
            b.clicked.connect(lambda _=False, u=url: QDesktopServices.openUrl(QUrl(u)))
            lay.addWidget(b)
        row = QHBoxLayout()
        pick = QPushButton("사운드폰트 선택…")
        pick.setAutoDefault(False)
        pick.clicked.connect(lambda: (dlg.accept(), self.choose_soundfont()))
        close = QPushButton("닫기")
        close.clicked.connect(dlg.accept)
        row.addWidget(pick)
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)
        dlg.exec()

    def clear_soundfont(self):
        self.player.soundfont = ""
        self.settings.setValue("soundfont_path", "")
        self.statusBar().showMessage("사운드폰트를 해제했습니다 (다음 재생부터 기본 소리)", 5000)

    def open_texdoc(self, name: str):
        """TeX 배포판에 설치된 문서를 texdoc 으로 연다 (설정의 ‘TeX 경로’ 반영)."""
        extra = split_tex_path(self.settings.value("tex_path", "", type=str))
        exe = shutil.which("texdoc", path=path_with_extra(extra, os.environ.get("PATH", "")))
        if not exe:
            self.statusBar().showMessage("texdoc 을 찾을 수 없습니다 — 웹 문서를 쓰거나 설정 ▸ TeX 경로를 확인하세요", 7000)
            return
        env = QProcessEnvironment.systemEnvironment()
        child_env_fix(env)
        if extra:
            env.insert("PATH", path_with_extra(extra, env.value("PATH", "")))
        p = QProcess(self)
        p.setProcessEnvironment(env)
        p.finished.connect(lambda code, _s, n=name, pr=p: (
            self.statusBar().showMessage(f"texdoc {n}: 문서를 찾지 못했거나 열지 못했습니다 (웹 문서를 이용하세요)", 7000)
            if code != 0 else None, pr.deleteLater()))
        p.start(exe, ["--view", name] if sys.platform != "win32" else [name])

    def show_diagnostics(self):
        lines = [f"버전 {__version__}",
                 f"실행 형태: {'패키징됨(PyInstaller)' if FROZEN else '소스 실행'}",
                 f"APP_DIR: {APP_DIR}", ""]
        for n in ("musixtex_patched.tex", "pmx_patched.tex", "mtxvisualhelper.py",
                  os.path.join("assets", "icon.png")):
            p = asset(n)
            lines.append(f"{'✔' if os.path.exists(p) else '✘'} {n}: {p if os.path.exists(p) else '없음'}")
        extra = split_tex_path(self.settings.value("tex_path", "", type=str))
        pth = path_with_extra(extra, os.environ.get("PATH", ""))
        lines.append("")
        for prog in ("musixtex", "prepmx", "luahbtex", "pdftex", "pdfcrop"):
            f = shutil.which(prog, path=pth)
            lines.append(f"{'✔' if f else '✘'} {prog}: {f or '찾을 수 없음'}")
        lines += ["", "찾아 보는 폴더:"] + ["  " + d for d in asset_dirs()]
        QMessageBox.information(self, "진단 정보", "\n".join(lines))

    # ---------------------------------------------------------------- 도움말
    def show_help(self):
        QMessageBox.information(self, "mtxMusicTypist 사용법", (
            "F5  컴파일 → 오른쪽에 PDF 갱신\n"
            "PDF 마디를 두 번 클릭(또는 Ctrl+클릭) → 소스의 그 마디로 이동\n"
            "소스에서 커서를 옮기면 PDF 의 그 마디가 표시됨 (F6 로 끄기, Ctrl+J 로 즉시 이동)\n"
            "왼쪽 ‘음표 입력’ 도크: Ctrl+Enter 로 커서 위치에 코드 삽입\n"
            "Ctrl+1/2/3/4/5  입력 도크 / 헤더 / 로그 / PMX / PDF 보이기 (PDF 창도 제목줄을 끌어 떼어낼 수 있음)\n"
            "Ctrl+W  떠 있는 도크(입력/헤더/로그/PMX)는 바로 닫기, 작업 창에서는 새로 만들기(Ctrl+N)와 같음\n"
            "Alt+X / S / F / N  임시표 X ♯ ♭ ♮ (+Shift: 𝄪 𝄫),  Alt+=  ‘=’ 버튼 — 편집기에 포커스가 있어도 동작\n"
            "Ctrl+L  줄 맨 앞에서 ‘L: ’ 입력 (가사 줄, 한/영 전환 불필요)\n"
            "Ctrl+4  PMX 수정 패널 — 생성된 pmx 를 직접 고치고 F7 (패치 저장 후 컴파일)\n"
            "F8  MIDI 를 커서가 있는 마디부터 재생/정지,  Shift+F8  처음부터 (아래 한 줄 플레이어)\n"
            "설정 메뉴에서 사운드폰트·편집기 글꼴을 지정\n\n"
            "거터의 ♩숫자는 pmx 기준 마디 번호입니다. ≈ 표시는 줄 단위 근사입니다."))


class _App(QApplication):
    """macOS Finder 의 ‘다음으로 열기’/더블클릭(QFileOpenEvent)을 받아 연다."""
    fileOpened = pyqtSignal(str)

    def event(self, ev):
        if ev.type() == QEvent.Type.FileOpen:
            self.fileOpened.emit(ev.file())
            return True
        return super().event(ev)


def _cli_file(argv):
    """명령줄 인자에서 열 파일을 고른다(옵션 `-…` 제외, 첫 번째 파일). 없으면 None."""
    for a in argv[1:]:
        if a.startswith("-"):
            continue
        return os.path.abspath(os.path.expanduser(a))
    return None


def main():
    if "--version" in sys.argv[1:]:
        print(f"mtxMusicTypist {__version__}")
        return
    if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
        print("usage: mtxmusictypist [file.mtx]\n  인자로 준 M-Tx 파일을 열어서 시작합니다.")
        return
    if os.environ.get("MTX_HANG_DEBUG"):
        # 얼어붙는 문제 진단용: 5초마다 모든 스레드의 호출 스택을 stderr 로 쏜다 (console=True 빌드에서 확인)
        import faulthandler
        faulthandler.dump_traceback_later(5, repeat=True)
    app = _App(sys.argv)
    app.setApplicationName("mtxMusicTypist")
    if sys.platform == "win32":          # 작업 표시줄에 python 이 아닌 이 앱의 아이콘이 보이도록
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ktug.mtxmusictypist")
        except Exception:
            pass
    icon = QIcon(asset(os.path.join("assets", "icon.ico" if sys.platform == "win32" else "icon.png")))
    app.setWindowIcon(icon)
    w = MainWindow()
    w.setWindowIcon(icon)
    w.show()

    def open_path(p):
        if os.path.isfile(p):
            if w._maybe_save():
                w.load_file(p)
        else:
            w.statusBar().showMessage(f"파일을 찾을 수 없습니다: {p}", 8000)
    app.fileOpened.connect(open_path)
    p = _cli_file(sys.argv)
    if p:
        open_path(p)
    else:
        w.restore_last()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
