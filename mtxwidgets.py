#!/usr/bin/env python3
"""mtxwidgets.py - mtxMusicTypist 의 화면 부품: 편집기, PDF 뷰어, 헤더 패널."""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from PyQt6.QtCore import QBuffer, QEvent, QIODevice, QPointF, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (QKeySequence, QShortcut, QColor, QFont, QFontDatabase, QPainter, QPen, QSyntaxHighlighter,
                         QTextCharFormat, QTextCursor, QTextFormat, QTextOption)
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                             QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QTabWidget,
                             QTextEdit, QVBoxLayout, QWidget)

import mtxheader
import mtxpair
from PyQt6.QtCore import QTimer

try:
    from PyQt6.QtPdf import QPdfDocument
    HAVE_QTPDF = True
except Exception:          # pragma: no cover
    QPdfDocument = None
    HAVE_QTPDF = False

Seg = Tuple[int, int]


# =============================================================== 구문 강조
# 구문 색: (라이트 배경용, 다크 배경용). 요란하지 않게, 두 배경 모두에서 충분한 대비가 나오게 고른 값.
_COLORS = {
    #  이름      라이트      다크
    "comment": ("#7a8590", "#8b949e"),
    "lyric":   ("#8a6d1f", "#d3b774"),   # L:/U: 가사 줄
    "key":     ("#2f6fc4", "#6cb2f2"),   # Title: Meter: 같은 헤더 키
    "bar":     ("#b8630f", "#e6a255"),   # | || |] :| |:
    "dir":     ("#1f5fbf", "#7db9f5"),   # o.. K+1 3/4 등 지시 (이전 음표 색)
    "rest":    ("#3f7f8a", "#8fbcc4"),   # r
    "chord":   ("#2f8a45", "#7fcf8e"),   # z 화음 구성음
    "mark":    ("#1f5fbf", "#7db9f5"),   # 대문자로 시작하는 표지 (D.., G.., ...) — 지시어와 같은 파랑
    "at":      ("#c0304a", "#f08a9b"),   # @
    "tex":     ("#8a6d1f", "#c4a35a"),   # \macro
    "slur":    ("#9b30c8", "#d896f5"),   # ( ) { } 슬러/타이 괄호
    "slurmod": ("#b05fd6", "#c58be8"),   # 괄호에 붙은 수식 (u1 S A 1 ...)
}
_BAR_RE = re.compile(r"(?<!\S)(\|\]|\|\||\|:?|:\|:?)(?!\S)")
_TOKEN_RE = re.compile(r"\S+")
_SLUR_RE = re.compile(r"^([(){}])(\S*)$")     # 괄호에 붙은 글자는 음표가 아니라 슬러/타이 수식
_LEAD_RE = re.compile(r"^[()\[\]{}<>]*")
_NO_WORD = r"(?![a-z]{3})"                # rit, dim, cresc 같은 영단어로 시작하면 음표/쉼표로 보지 않는다
# rpb(한 마디 쉼+안 그림) 같은 쉼표 후치사 조합은 영단어 검사에서 제외
_REST_RE = re.compile(r"^(?:r[pb]{1,2}(?![a-z])|" + _NO_WORD + r"r)")
# zcn zfs zbff zcr+ 처럼 z 뒤에 임시표·머리 이동 글자가 붙는 구성음 (영단어 검사 대신 길이로 제한)
_CHORD_RE = re.compile(r"^z[a-g](?:ss|ff|[snf])?[lre]?[+\-=]*\d*[ud]?$")
_DIR_RE = re.compile(r"^(?:o\S*|K[+-]\d\S*|\d+/\d+)$")


class MtxHighlighter(QSyntaxHighlighter):
    """쉼표/화음 구성음(z)/대문자 표지·지시/@ 를 배경 밝기에 맞춰 칠한다(음표는 기본색).
    헤더(첫 빈 줄 앞)는 키만, 본문은 토큰 단위로 색을 입힌다."""

    def __init__(self, doc, dark: bool = False):
        super().__init__(doc)
        self.enabled = True
        self.set_dark(dark, rehighlight=False)

    def set_dark(self, dark: bool, rehighlight: bool = True) -> None:
        def fmt(name, bold=False, italic=False):
            f = QTextCharFormat()
            f.setForeground(QColor(_COLORS[name][1 if dark else 0]))
            if bold:
                f.setFontWeight(QFont.Weight.Bold)
            f.setFontItalic(italic)
            return f
        self.dark = dark
        self.F = {n: fmt(n) for n in _COLORS}
        self.F["comment"] = fmt("comment", italic=True)
        self.F["key"] = fmt("key", bold=True)
        self.F["bar"] = fmt("bar", bold=True)
        self.F["at"] = fmt("at", bold=True)
        self.F["slur"] = fmt("slur", bold=True)
        if rehighlight:
            self.rehighlight()

    def set_enabled(self, on: bool) -> None:
        self.enabled = on
        self.rehighlight()

    def _classify(self, tok: str) -> str:
        core = tok[_LEAD_RE.match(tok).end():]
        if not core:
            return ""
        if core.startswith("@"):
            return "at"
        if core.startswith("\\"):
            return "tex"
        if _DIR_RE.match(core):
            return "dir"
        if core[0].isupper():
            return "mark"
        if _CHORD_RE.match(core):
            return "chord"
        if _REST_RE.match(core):
            return "rest"
        return ""                         # 음표(a-g)는 기본 글자색 그대로

    def highlightBlock(self, text: str) -> None:
        if not self.enabled:
            self.setCurrentBlockState(1)
            return
        in_header = self.previousBlockState() in (-1, 0)
        s = text.strip()
        if in_header and (not s or s == "%%"):
            in_header = False
        self.setCurrentBlockState(0 if in_header else 1)
        if s.startswith("%"):
            self.setFormat(0, len(text), self.F["comment"] if not s.startswith("%%") else self.F["tex"])
            return
        if re.match(r"^\s*[A-Za-z]:", text):            # L: / U: 가사 줄
            self.setFormat(0, len(text), self.F["lyric"])
            return
        m = re.match(r"^([A-Za-z0-9/]+):", text)
        if m and m.group(1)[0].isupper():
            self.setFormat(0, m.end(), self.F["key"])
            return
        if in_header:
            return
        for m in _TOKEN_RE.finditer(text):
            tok = m.group(0)
            if _BAR_RE.fullmatch(tok):
                self.setFormat(m.start(), len(tok), self.F["bar"])
                continue
            sm = _SLUR_RE.match(tok)
            if sm:
                self.setFormat(m.start(), 1, self.F["slur"])
                if sm.group(2):
                    self.setFormat(m.start() + 1, len(sm.group(2)), self.F["slurmod"])
                continue
            kind = self._classify(tok)
            if kind:
                self.setFormat(m.start(), len(tok), self.F[kind])


# =============================================================== 편집기
class _Gutter(QWidget):
    EDGE = 5                               # 오른쪽 가장자리 이 폭 안에서 끌어 조절

    def __init__(self, ed: "CodeEditor"):
        super().__init__(ed)
        self.ed = ed
        self.setMouseTracking(True)
        self._drag = False

    def sizeHint(self):
        return QSize(self.ed.gutter_width(), 0)

    def _on_edge(self, x):
        return x >= self.width() - self.EDGE

    def mouseMoveEvent(self, ev):
        x = int(ev.position().x())
        if self._drag:
            self.ed.set_gutter_width(x)
        self.setCursor(Qt.CursorShape.SplitHCursor if (self._drag or self._on_edge(x)) else Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton and self._on_edge(int(ev.position().x())):
            self._drag = True

    def mouseReleaseEvent(self, ev):
        if self._drag:
            self._drag = False
            self.ed.gutterWidthChanged.emit(self.ed._gutter_user or 0)

    def mouseDoubleClickEvent(self, ev):          # 가장자리 더블클릭: 자동 폭으로 되돌림
        if self._on_edge(int(ev.position().x())):
            self.ed.set_gutter_width(0)
            self.ed.gutterWidthChanged.emit(0)

    def paintEvent(self, ev):
        self.ed.paint_gutter(ev)


class CodeEditor(QPlainTextEdit):
    """줄 번호 + 마디 번호 거터, 마디 강조, 오류 줄 표시가 있는 소스 편집기."""

    gutterWidthChanged = pyqtSignal(int)          # 끌어서 바꾼 거터 폭(0 = 자동)
    pairMessage = pyqtSignal(str)                 # 커서가 닿은 슬러·타이 괄호의 짝 안내(없으면 빈 글자)
    _gutter_user = 0

    def _is_dark(self) -> bool:
        return self.palette().base().color().lightness() < 128

    def changeEvent(self, ev):
        super().changeEvent(ev)
        if ev.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange):
            hl = getattr(self, "highlighter", None)
            if hl is not None and hl.dark != self._is_dark():
                hl.set_dark(self._is_dark())

    def keyPressEvent(self, ev):
        # Ctrl+L: 줄 맨 앞에서 가사 표지 `L: ` 입력 (한글 입력 상태에서도 동작)
        if (ev.modifiers() & Qt.KeyboardModifier.ControlModifier
                and not ev.modifiers() & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.AltModifier)
                and ev.key() == Qt.Key.Key_L):
            cur = self.textCursor()
            if cur.positionInBlock() == 0 and not cur.hasSelection():
                cur.insertText("L: ")
                self.setTextCursor(cur)
            ev.accept()
            return
        super().keyPressEvent(ev)

    def __init__(self, parent=None):
        super().__init__(parent)
        f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        f.setPointSize(12)
        self.setFont(f)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._gutter = _Gutter(self)
        self._bar_labels: Dict[int, str] = {}
        self._bar_spans: List[Seg] = []
        self._error_lines: List[int] = []
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self.refresh_selections)
        self._pairs: Optional[mtxpair.PairInfo] = None
        self.pair_enabled = True
        self._pair_timer = QTimer(self)
        self._pair_timer.setSingleShot(True)
        self._pair_timer.setInterval(150)
        self._pair_timer.timeout.connect(self._recompute_pairs)
        self.document().contentsChanged.connect(self._pair_timer.start)
        self._update_gutter_width()
        self.highlighter = MtxHighlighter(self.document(), self._is_dark())

    # ---- 거터
    def _gutter_min(self) -> int:
        digits = max(3, len(str(max(1, self.blockCount()))))
        return 12 + self.fontMetrics().horizontalAdvance("9") * digits   # 줄 번호만

    def gutter_width(self) -> int:
        digits = max(3, len(str(max(1, self.blockCount()))))
        auto = 12 + self.fontMetrics().horizontalAdvance("9") * (digits + 7)
        if self._gutter_user:
            return max(self._gutter_min(), self._gutter_user)
        return auto

    def set_gutter_width(self, w: int) -> None:
        """w=0 이면 자동 폭. 줄 번호가 보이는 최소 폭 아래로는 줄어들지 않는다."""
        self._gutter_user = max(self._gutter_min(), min(int(w), 600)) if w else 0
        self._update_gutter_width()
        cr = self.contentsRect()
        self._gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))
        self._gutter.update()

    def _update_gutter_width(self, *_):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _update_gutter(self, rect, dy):
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        cr = self.contentsRect()
        self._gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    # ---- 줄 바꿈 (표시만 바꾼다: 소스의 실제 줄은 그대로)
    def set_wrap(self, on: bool) -> None:
        opt = self.document().defaultTextOption()
        flags = opt.flags()
        if on:
            self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
            self.setWordWrapMode(QTextOption.WrapMode.WordWrap)        # 토큰(공백) 사이에서만 끊는다
            opt.setFlags(flags & ~QTextOption.Flag.ShowLineAndParagraphSeparators)   # ¶ 는 paintEvent 에서 회색으로 직접 그린다
        else:
            self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            opt.setFlags(flags & ~QTextOption.Flag.ShowLineAndParagraphSeparators)
        opt.setWrapMode(QTextOption.WrapMode.WordWrap if on else QTextOption.WrapMode.NoWrap)
        self.document().setDefaultTextOption(opt)
        self._gutter.update()
        self.viewport().update()

    def paintEvent(self, ev):
        super().paintEvent(ev)
        if not self.wrap_on():
            return
        p = QPainter(self.viewport())
        col = self.palette().placeholderText().color()
        col.setAlpha(150)
        p.setPen(col)
        p.setFont(self.font())
        off = self.contentOffset()
        margin = self.document().documentMargin()
        block = self.firstVisibleBlock()
        bottom = ev.rect().bottom()
        while block.isValid():
            geo = self.blockBoundingGeometry(block).translated(off)
            if geo.top() > bottom:
                break
            lay = block.layout()
            if block.isVisible() and lay is not None and lay.lineCount() > 0 and block.next().isValid():
                ln = lay.lineAt(lay.lineCount() - 1)
                x = off.x() + margin + ln.x() + ln.naturalTextWidth() + 3
                p.drawText(QPointF(x, geo.top() + ln.y() + ln.ascent()), "¶")
            block = block.next()
        p.end()

    def wrap_on(self) -> bool:
        return self.lineWrapMode() != QPlainTextEdit.LineWrapMode.NoWrap

    def paint_gutter(self, ev):
        p = QPainter(self._gutter)
        pal = self.palette()
        p.fillRect(ev.rect(), pal.window().color())
        block = self.firstVisibleBlock()
        n = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        fm = self.fontMetrics()
        w = self._gutter.width()
        num_w = fm.horizontalAdvance("9") * max(3, len(str(self.blockCount())))
        small = QFont(self.font())
        small.setPointSize(max(7, self.font().pointSize() - 3))
        while block.isValid() and top <= ev.rect().bottom():
            if block.isVisible() and bottom >= ev.rect().top():
                p.setPen(pal.placeholderText().color())
                p.drawText(w - 6 - num_w, top, num_w, fm.height(),
                           Qt.AlignmentFlag.AlignRight, str(n + 1))
                lay = block.layout()
                if lay is not None and lay.lineCount() > 1:       # 줄 바꿈으로 이어지는 화면 줄
                    p.setPen(pal.placeholderText().color())
                    for i in range(1, lay.lineCount()):
                        ly = top + round(lay.lineAt(i).y())
                        if ly <= ev.rect().bottom() and ly + fm.height() >= ev.rect().top():
                            p.drawText(w - 6 - num_w, ly, num_w, fm.height(),
                                       Qt.AlignmentFlag.AlignRight, "↳")
                lab = self._bar_labels.get(n)
                if lab:
                    p.setFont(small)
                    p.setPen(QColor("#d2691e"))
                    p.drawText(4, top, w - num_w - 14, fm.height(),
                               Qt.AlignmentFlag.AlignLeft, "♩" + lab)
                    p.setFont(self.font())
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            n += 1
        p.end()

    def set_bar_labels(self, labels: Dict[int, str]) -> None:
        self._bar_labels = labels
        self._gutter.update()

    # ---- 강조
    def set_bar_spans(self, spans: List[Seg]) -> None:
        self._bar_spans = list(spans)
        self.refresh_selections()

    def set_find_spans(self, spans: List[Seg]) -> None:
        self._find_spans = list(spans)
        self.refresh_selections()

    def set_error_lines(self, lines: List[int]) -> None:
        self._error_lines = list(lines)
        self.refresh_selections()

    def refresh_selections(self) -> None:
        sels = []
        cur = QTextEdit.ExtraSelection()
        cur.format.setBackground(QColor(128, 128, 128, 30))
        cur.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        cur.cursor = self.textCursor()
        cur.cursor.clearSelection()
        sels.append(cur)
        n = self.document().characterCount()
        for s, e in self._bar_spans:
            if 0 <= s < e <= n:
                sel = QTextEdit.ExtraSelection()
                sel.format.setBackground(QColor(255, 200, 40, 85))
                c = QTextCursor(self.document())
                c.setPosition(s)
                c.setPosition(min(e, n - 1), QTextCursor.MoveMode.KeepAnchor)
                sel.cursor = c
                sels.append(sel)
        for ln in self._error_lines:
            blk = self.document().findBlockByNumber(ln - 1)
            if blk.isValid():
                sel = QTextEdit.ExtraSelection()
                sel.format.setBackground(QColor(255, 60, 60, 80))
                sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
                sel.cursor = QTextCursor(blk)
                sels.append(sel)
        n1 = self.document().characterCount()
        for s0, e0 in getattr(self, "_find_spans", ()):
            if 0 <= s0 < e0 <= n1:
                sels.append(self._range_sel(s0, e0, QColor(80, 160, 255, 90)))
        sels += self._pair_selections()
        self.setExtraSelections(sels)

    # ---- 슬러·타이 짝 찾기
    def set_pair_enabled(self, on: bool) -> None:
        self.pair_enabled = on
        self._pairs = None
        self.refresh_selections()

    def _recompute_pairs(self) -> None:
        self._pairs = None
        self.refresh_selections()

    def _pair_info(self) -> Optional["mtxpair.PairInfo"]:
        if not self.pair_enabled:
            return None
        if self._pairs is None:
            import mtxmap
            self._pairs = mtxpair.find_pairs(mtxmap.qt_safe(self.toPlainText()))
        return self._pairs

    def _range_sel(self, a: int, b: int, bg: Optional[QColor] = None, wave: Optional[QColor] = None):
        sel = QTextEdit.ExtraSelection()
        if bg is not None:
            sel.format.setBackground(bg)
        if wave is not None:
            sel.format.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
            sel.format.setUnderlineColor(wave)
        c = QTextCursor(self.document())
        c.setPosition(a)
        c.setPosition(b, QTextCursor.MoveMode.KeepAnchor)
        sel.cursor = c
        return sel

    def _pair_selections(self) -> list:
        pi = self._pair_info()
        if pi is None:
            self.pairMessage.emit("")
            return []
        n = max(0, self.document().characterCount() - 1)
        out = []
        for i in pi.unmatched():                          # 짝 없는 괄호: 빨간 물결 밑줄
            b = pi.brackets[i]
            if b.end <= n:
                out.append(self._range_sel(b.start, b.end, wave=QColor(230, 40, 40)))
        msg = ""
        k = pi.at(self.textCursor().position())
        if k is not None:
            b = pi.brackets[k]
            hl = QColor(0, 160, 255, 110)
            if b.end <= n:
                out.append(self._range_sel(b.start, b.end, bg=hl))
            if b.partner >= 0:
                q = pi.brackets[b.partner]
                if q.end <= n:
                    out.append(self._range_sel(q.start, q.end, bg=hl))
                doc = self.document()
                blk = doc.findBlockByNumber(q.line)
                col = q.start - blk.position() + 1
                txt = blk.text()
                lo = max(0, q.start - blk.position() - 14)
                snip = ("…" if lo else "") + txt[lo:lo + 36].strip() + ("…" if lo + 36 < len(txt) else "")
                where = "같은 줄" if q.line == b.line else f"{abs(q.line - b.line)}줄 {'아래' if q.line > b.line else '위'}"
                msg = f"{b.char} 의 짝 {q.char}: {q.line + 1}줄 {col}열 ({where})   {snip}"
            else:
                msg = f"{b.char} 의 짝이 없습니다"
        self.pairMessage.emit(msg)
        return out

    def jump_to_pair(self) -> bool:
        pi = self._pair_info()
        k = pi.at(self.textCursor().position()) if pi else None
        if k is None or pi.brackets[k].partner < 0:
            return False
        q = pi.brackets[pi.brackets[k].partner]
        self.goto_offset(q.end if not q.is_open else q.start)
        return True

    def jump_unmatched(self, forward: bool = True) -> bool:
        pi = self._pair_info()
        um = pi.unmatched() if pi else []
        if not um:
            return False
        pos = self.textCursor().position()
        starts = [pi.brackets[i].start for i in um]
        if forward:
            cand = [x for x in starts if x > pos] or starts
            target = cand[0]
        else:
            cand = [x for x in starts if x < pos] or starts
            target = cand[-1]
        self.goto_offset(target)
        return True

    # ---- 편집 보조
    def insert_snippet(self, text: str) -> None:
        """헬퍼가 만든 코드를 커서 위치에 넣는다 (앞뒤 공백 보정)."""
        text = text.strip()
        if not text:
            return
        c = self.textCursor()
        doc = self.document()
        pos = c.position()
        before = doc.characterAt(pos - 1) if pos > 0 else "\n"
        after = doc.characterAt(pos)
        if before not in (" ", "\n", "\t", " ") and before != "":
            text = " " + text
        if after not in (" ", "\n", "\t", " ", "\x00"):
            text += " "
        else:
            text += " " if after in ("\n", " ", "\x00") else ""
        c.insertText(text)
        self.setTextCursor(c)
        self.setFocus()

    def replace_minimal(self, new_text: str) -> None:
        """전체 교체 대신 바뀐 부분만 교체(대응표/되돌리기에 유리)."""
        old = self.toPlainText()
        if old == new_text:
            return
        p = 0
        m = min(len(old), len(new_text))
        while p < m and old[p] == new_text[p]:
            p += 1
        s = 0
        while s < m - p and old[len(old) - 1 - s] == new_text[len(new_text) - 1 - s]:
            s += 1
        c = QTextCursor(self.document())
        c.beginEditBlock()
        c.setPosition(p)
        c.setPosition(len(old) - s, QTextCursor.MoveMode.KeepAnchor)
        c.insertText(new_text[p:len(new_text) - s])
        c.endEditBlock()

    def goto_offset(self, off: int, select_to: Optional[int] = None) -> None:
        c = self.textCursor()
        n = max(0, self.document().characterCount() - 1)
        c.setPosition(max(0, min(off, n)))
        if select_to is not None:
            c.setPosition(max(0, min(select_to, n)), QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(c)
        self.centerCursor()
        self.setFocus()


# =============================================================== PDF 뷰어
class _Pages(QWidget):
    def __init__(self, view: "PdfView"):
        super().__init__()
        self.view = view
        self.setMouseTracking(False)

    def paintEvent(self, ev):
        self.view.paint_pages(self, ev)

    def mouseDoubleClickEvent(self, ev):
        self.view.click_at(ev.position())

    def mousePressEvent(self, ev):
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.view.click_at(ev.position())
        else:
            super().mousePressEvent(ev)


class PdfView(QScrollArea):
    """자체 PDF 뷰어: 쪽을 직접 그리므로 (1) 파일을 잠그지 않고 (2) 마디 영역을
    덧그리고 (3) 클릭 좌표를 PDF 좌표로 되돌릴 수 있다."""
    sourceRequested = pyqtSignal(int, float, float)   # page, x_bp, y_bp(아래 기준)

    MARGIN = 14

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(False)
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        # 스크롤바 유무가 바뀌며 폭이 변하고 → 다시 배치 → 또 바뀌는 되먹임을 막는다
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self._rl_timer = QTimer(self)
        self._rl_timer.setSingleShot(True)
        self._rl_timer.setInterval(120)
        self._rl_timer.timeout.connect(self._relayout)
        self._in_relayout = False
        self._w = _Pages(self)
        self.setWidget(self._w)
        self._doc = QPdfDocument(self) if HAVE_QTPDF else None
        self._buf: Optional[QBuffer] = None
        self._bytes = b""
        self._fit = True
        self._scale = 1.5                  # px per bp
        self._layout: List[Tuple[int, int, int, int]] = []  # (top, left, w, h) px
        self._sizes: List[Tuple[float, float]] = []
        self._cache: Dict[Tuple[int, int, int], object] = {}
        self._hl: Optional[Tuple[int, float, float, float, float]] = None
        self.message = QLabel("PDF 없음 — F5 로 컴파일하세요", self)
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message.setStyleSheet("color: gray; font-size: 14px;")
        if self._doc is not None:
            self._doc.statusChanged.connect(self._on_status)

    # ---- 불러오기
    def load_bytes(self, data: bytes) -> bool:
        if self._doc is None:
            return False
        vb = self.verticalScrollBar().value()
        hb = self.horizontalScrollBar().value()
        self._doc.close()
        self._cache.clear()
        self._bytes = data
        self._buf = QBuffer(self)
        self._buf.setData(data)
        self._buf.open(QIODevice.OpenModeFlag.ReadOnly)
        self._doc.load(self._buf)
        self._restore = (hb, vb)
        self._on_status()
        return self._doc.status() == QPdfDocument.Status.Ready

    def load_file(self, path: str) -> bool:
        try:
            with open(path, "rb") as f:
                return self.load_bytes(f.read())
        except OSError:
            return False

    def _on_status(self, *_):
        if self._doc is None or self._doc.status() != QPdfDocument.Status.Ready:
            return
        self._sizes = []
        for i in range(self._doc.pageCount()):
            s = self._doc.pagePointSize(i)
            self._sizes.append((s.width(), s.height()))
        self.message.hide()
        self._relayout()
        if getattr(self, "_restore", None):
            hb, vb = self._restore
            self._restore = None
            self.horizontalScrollBar().setValue(hb)
            self.verticalScrollBar().setValue(vb)

    @property
    def has_pdf(self) -> bool:
        return bool(self._sizes)

    # ---- 배치
    def _relayout(self):
        if not self._sizes or self._in_relayout:
            return
        self._in_relayout = True
        try:
            self._relayout_impl()
        finally:
            self._in_relayout = False

    def _relayout_impl(self):
        if self._fit:
            avail = max(100, self.viewport().width() - 2 * self.MARGIN
                        - (self.verticalScrollBar().sizeHint().width() if False else 0))
            wmax = max(w for w, _ in self._sizes)
            self._scale = avail / wmax
        y = self.MARGIN
        W = int(max(w for w, _ in self._sizes) * self._scale) + 2 * self.MARGIN
        self._layout = []
        for w, h in self._sizes:
            pw, ph = int(w * self._scale), int(h * self._scale)
            self._layout.append((y, (W - pw) // 2, pw, ph))
            y += ph + self.MARGIN
        self._w.resize(max(W, self.viewport().width()), y)
        self._w.update()

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self.message.setGeometry(self.viewport().rect())
        if self._fit:
            self._rl_timer.start()          # 창을 끌어 크기를 바꾸는 동안에는 잠시 기다렸다가 한 번만 배치

    def set_zoom(self, factor: Optional[float]):
        """None = 폭에 맞춤"""
        if factor is None:
            self._fit = True
        else:
            self._fit = False
            self._scale = max(0.3, min(8.0, factor))
        self._relayout()

    def zoom_by(self, mult: float):
        self.set_zoom(self._scale * mult)

    def wheelEvent(self, ev):
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_by(1.1 if ev.angleDelta().y() > 0 else 1 / 1.1)
            ev.accept()
        else:
            super().wheelEvent(ev)

    # ---- 그리기
    def _page_image(self, i: int, w: int, h: int):
        dpr = max(1.0, self.devicePixelRatioF())
        key = (i, w, int(dpr * 100))
        img = self._cache.get(key)
        if img is None:
            if len(self._cache) > 24:
                self._cache.clear()
            img = self._doc.render(i, QSize(int(w * dpr), int(h * dpr)))
            img.setDevicePixelRatio(dpr)
            self._cache[key] = img
        return img

    def paint_pages(self, widget, ev):
        p = QPainter(widget)
        p.fillRect(ev.rect(), QColor("#6b6f76"))
        if not self._layout or self._doc is None:
            p.end()
            return
        for i, (top, left, w, h) in enumerate(self._layout):
            if top > ev.rect().bottom() or top + h < ev.rect().top():
                continue
            p.fillRect(left, top, w, h, Qt.GlobalColor.white)
            p.drawImage(left, top, self._page_image(i, w, h))
        if self._hl:
            r = self._hl_rect_px()
            if r:
                p.setPen(QPen(QColor(230, 120, 0), 1.5))
                p.setBrush(QColor(255, 200, 40, 70))
                p.drawRect(r)
        p.end()

    # ---- 좌표 변환
    def _hl_rect_px(self) -> Optional[QRectF]:
        page, x0, ybot, x1, ytop = self._hl
        i = page - 1
        if not (0 <= i < len(self._layout)):
            return None
        top, left, w, h = self._layout[i]
        ph = self._sizes[i][1]
        s = self._scale
        return QRectF(left + x0 * s, top + (ph - ytop) * s, (x1 - x0) * s, (ytop - ybot) * s)

    def show_rect(self, rect: Optional[Tuple[int, float, float, float, float]], scroll=True):
        self._hl = rect
        self._w.update()
        if rect and scroll and self._layout:
            r = self._hl_rect_px()
            if r:
                vb = self.verticalScrollBar()
                view_top, view_h = vb.value(), self.viewport().height()
                if r.top() < view_top + 20 or r.bottom() > view_top + view_h - 20:
                    vb.setValue(int(r.center().y() - view_h / 2))
                hb = self.horizontalScrollBar()
                if r.right() > hb.value() + self.viewport().width() or r.left() < hb.value():
                    hb.setValue(int(r.left() - 40))

    def click_at(self, pos: QPointF):
        for i, (top, left, w, h) in enumerate(self._layout):
            if top <= pos.y() <= top + h and left <= pos.x() <= left + w:
                s = self._scale
                self.sourceRequested.emit(i + 1, (pos.x() - left) / s,
                                          self._sizes[i][1] - (pos.y() - top) / s)
                return


# =============================================================== 헤더 패널
_FONT_PREFIX = re.compile(r"^((?:\\[A-Za-z@]+\s*)+)(.*)$", re.S)


def split_font_text(full: str) -> Tuple[str, str]:
    """‘\\htitlefont 봉숭아’ -> (‘\\htitlefont’, ‘봉숭아’). 앞의 TeX 명령(\\이름)들이 글꼴 부분, 나머지가 글자."""
    full = full.strip()
    m = _FONT_PREFIX.match(full)
    if not m:
        return "", full
    return m.group(1).strip(), m.group(2).strip()


class FontTextEdit(QWidget):
    """[ 글꼴 명령 ][ 글자 ] 두 칸. text()/setText() 는 합친 한 줄(‘글꼴 글자’)로 주고받는다.
    글꼴 칸은 색으로 구분되고, 비어 있으면 출력에도 나타나지 않는다."""

    def __init__(self, font_hint: str = "\\htitlefont", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.font_edit = QLineEdit()
        self.font_edit.setPlaceholderText(font_hint)
        self.font_edit.setToolTip("글꼴 지정 TeX 명령 (예: \\htitlefont). 없으면 비워 둔다.")
        self.font_edit.setFixedWidth(130)
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        mono.setPointSize(max(8, self.font_edit.font().pointSize() - 1))
        self.font_edit.setFont(mono)
        dark = self.palette().base().color().lightness() < 128
        fg, bg = ("#d6a8ff", "rgba(176,95,214,0.18)") if dark else ("#7a1fa8", "rgba(155,48,200,0.10)")
        self.font_edit.setStyleSheet(f"QLineEdit {{ color:{fg}; background:{bg}; }}")
        self.text_edit = QLineEdit()
        lay.addWidget(self.font_edit)
        lay.addWidget(self.text_edit, 1)

    def text(self) -> str:
        t = self.text_edit.text().strip()
        if not t:                       # 글자가 없으면 글꼴 명령만 홀로 출력하지 않는다
            return ""
        f = self.font_edit.text().strip()
        return f"{f} {t}" if f else t

    def setText(self, full: str) -> None:
        f, t = split_font_text(full)
        self.font_edit.setText(f)
        self.text_edit.setText(t)


class HeaderPanel(QWidget):
    applyRequested = pyqtSignal()
    reloadRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)

        # --- 정보
        info = QWidget(); fl = QFormLayout(info)
        self.title = FontTextEdit("\\htitlefont"); self.name = QLineEdit()
        self.composer = FontTextEdit("\\hcomposerfont"); self.poet = FontTextEdit("\\hcomposerfont")
        fl.addRow("Title", self.title); fl.addRow("Name (악기)", self.name)
        fl.addRow("Composer", self.composer); fl.addRow("Poet", self.poet)
        self.tabs.addTab(info, "정보")

        # --- 음악
        mus = QWidget(); fl = QFormLayout(mus)
        self.meter = QComboBox(); self.meter.addItems(mtxheader.METERS)
        self.mnum = QSpinBox(); self.mnum.setRange(1, 64)
        self.mden = QSpinBox(); self.mden.setRange(1, 64)
        row = QHBoxLayout(); row.addWidget(self.meter); row.addWidget(self.mnum)
        row.addWidget(QLabel("/")); row.addWidget(self.mden)
        fl.addRow("Meter", row)
        self.key = QSpinBox(); self.key.setRange(-7, 7)
        self.key.setToolTip("+ 샵 개수 / - 플랫 개수")
        fl.addRow("조표 (샵+/플랫-)", self.key)
        self.style = QComboBox(); self.style.addItems(mtxheader.STYLES)
        self.style_override = QLineEdit(); self.style_override.setPlaceholderText("예: DUO Piano")
        self.custom_style = QLineEdit(); self.custom_style.setPlaceholderText("예: DUO: Voices S A; Choral; Clefs G G")
        fl.addRow("Style", self.style); fl.addRow("  이름 직접 지정", self.style_override)
        # Custom 정의를 4개 칸(명칭/성부/유형/음자리표)으로 편집 -> 위 정의문으로 조립
        self.cs_name = QLineEdit(); self.cs_name.setPlaceholderText("예: SMP")
        self.cs_voices = QLineEdit(); self.cs_voices.setPlaceholderText("예: A B  (공백으로 구분)")
        self.cs_type = QLineEdit(); self.cs_type.setPlaceholderText("예: Continuo, Choral, Piano … (선택)")
        self.cs_clefs = QLineEdit(); self.cs_clefs.setPlaceholderText("예: G F  (성부 수와 같아야 함)")
        self.cs_warn = QLabel(""); self.cs_warn.setStyleSheet("color:#d33;")
        self.cs_box = QGroupBox("Custom 정의")
        cfl = QFormLayout(self.cs_box)
        cfl.addRow("정의문", self.custom_style)
        cfl.addRow("명칭", self.cs_name); cfl.addRow("성부 (Voices)", self.cs_voices)
        cfl.addRow("유형", self.cs_type); cfl.addRow("음자리표 (Clefs)", self.cs_clefs)
        cfl.addRow("", self.cs_warn)
        fl.addRow(self.cs_box)
        self.style.currentTextChanged.connect(lambda t: self.cs_box.setEnabled(t == "Custom"))
        self.cs_box.setEnabled(self.style.currentText() == "Custom")
        self._cs_busy = False
        for wd in (self.cs_name, self.cs_voices, self.cs_type, self.cs_clefs):
            wd.textEdited.connect(self._cs_compose)
        self.custom_style.textEdited.connect(self._cs_parse)
        self.vocal_beam = QCheckBox("Vocal Beam")
        self.vocal_beam.setToolTip("체크하면 헤더 맨 앞에 ‘Disable: unbeamVocal’ 을 넣는다.\n"
                                   "(M-Tx 는 성악 파트의 음표를 기본으로 보 없이 쓰는데, 그 기본 동작을 끈다 = 보로 묶는다)")
        fl.addRow("", self.vocal_beam)
        self.tabs.addTab(mus, "음악")
        self.meter.currentTextChanged.connect(self._meter_changed)

        # --- 레이아웃
        lo = QWidget(); fl = QFormLayout(lo)
        self.size = QComboBox(); self.size.setEditable(False); self.size.addItems(mtxheader.SIZES)
        self.width = QLineEdit()
        self.width.setPlaceholderText("예: 190  (mm 단위, 숫자만 쓰면 mm 가 붙는다)")
        self.width.editingFinished.connect(lambda: self.width.setText(mtxheader.normalize_width(self.width.text())))
        self.bars_line = QSpinBox(); self.bars_line.setRange(0, 30)
        self.indent = QSpinBox(); self.indent.setRange(0, 100)
        self.indent.setToolTip("첫 줄 들여쓰기 (x 0.01)")
        self.space = QLineEdit(); self.systems = QLineEdit()
        fl.addRow("Size", self.size); fl.addRow("Width", self.width)
        fl.addRow("Bars/Line", self.bars_line); fl.addRow("Indent", self.indent)
        fl.addRow("Space", self.space); fl.addRow("Systems", self.systems)
        self.tabs.addTab(lo, "레이아웃")

        # --- 기타
        ot = QWidget(); v = QVBoxLayout(ot)
        self.luatex = QCheckBox("LuaTeX 사용"); self.hangul = QCheckBox("한글 (luatexko / kotexplain)")
        self.midi = QCheckBox("MIDI 생성 (%%I)"); self.ragged = QCheckBox("\\raggedbottom")
        self.crop = QCheckBox("PDF 여백 자르기 (pdfcrop)")
        self.meter_off = QCheckBox("박자표 그리지 않기 (악보 조각용)")
        self.meter_off.setToolTip("소스에는 ‘% MeterSig: Off’ 주석만 남고, 컴파일할 때만 musixtex 뒤에\n"
                                  "박자표를 끄는 TeX 코드(\\generalmeter 재정의)를 끼워 넣는다. (M-Tx 문법은 아님)")
        for w in (self.luatex, self.hangul, self.midi, self.ragged, self.crop, self.meter_off):
            v.addWidget(w)
        fl = QFormLayout(); self.pmx = QLineEdit(); fl.addRow("PMX:", self.pmx); v.addLayout(fl)
        v.addWidget(QLabel("Extra (한 줄에 하나):"))
        self.options = QPlainTextEdit(); self.options.setMaximumHeight(110); v.addWidget(self.options)
        # MIDI 템포 / 악기 (%%I 줄)
        mbox = QGroupBox("MIDI (%%I 줄)")
        mf = QFormLayout(mbox)
        self.midi_tempo = QSpinBox(); self.midi_tempo.setRange(0, 400)
        self.midi_tempo.setSpecialValueText("(지정 안 함)"); self.midi_tempo.setSuffix(" ♩/분")
        self.midi_instr = QComboBox(); self.midi_instr.setEditable(True)
        self.midi_instr.addItem("")
        for code, name in mtxheader.MIDI_INSTRUMENTS:
            self.midi_instr.addItem(f"{code} — {name}", code)
        self.midi_instr.lineEdit().setPlaceholderText("(지정 안 함)  목록에서 아래 보표부터 차례로 고르세요")
        self.midi_instr.setToolTip("목록에서 고를 때마다 코드가 뒤에 하나씩 이어집니다 (pi → pi va …).\n"
                                   "PMX 의 보표 순서대로 *아래 보표부터* 지정합니다 (예: ipiva = 아래 pi, 위 va).\n"
                                   "직접 입력/수정도 가능하고, 빈 항목을 고르면 지웁니다.")
        mf.addRow("템포", self.midi_tempo); 
        ir = QHBoxLayout(); ir.setContentsMargins(0, 0, 0, 0)
        ir.addWidget(self.midi_instr, 1)
        clr = QPushButton("Clear"); clr.setToolTip("지정한 악기 목록을 모두 지운다")
        clr.clicked.connect(self._instr_clear); ir.addWidget(clr)
        mf.addRow("악기", ir)
        v.addWidget(mbox)
        self.midi_instr.activated.connect(self._instr_picked)
        self.midi_tempo.valueChanged.connect(self._midi_field_changed)
        self._instr_prev = ""
        self.midi_instr.editTextChanged.connect(self._instr_text_changed)
        v.addStretch(1)
        self.tabs.addTab(ot, "기타")

        btns = QHBoxLayout()
        b1 = QPushButton("소스에 적용 (Ctrl+Enter)"); b1.clicked.connect(self.applyRequested)
        # 헤더 설정 안 어느 입력칸에 포커스가 있어도 Ctrl+Enter 로 적용
        for key in ("Ctrl+Return", "Ctrl+Enter"):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(self.applyRequested)
        b2 = QPushButton("소스에서 다시 읽기"); b2.clicked.connect(self.reloadRequested)
        btns.addWidget(b2); btns.addStretch(1); btns.addWidget(b1)
        lay.addLayout(btns)
        self._meter_changed(self.meter.currentText())
        self.hangul.toggled.connect(self._hangul_toggled)

    def _hangul_toggled(self, on: bool):
        # 한글을 켜면 비어 있는 글꼴 칸에 제목/작곡가 글꼴 매크로를 채워 보이게 하고, 끄면 그 매크로만 지운다
        for ed, macro in ((self.title, mtxheader.TITLE_MACRO), (self.composer, mtxheader.COMPOSER_MACRO),
                          (self.poet, mtxheader.COMPOSER_MACRO)):
            cur = ed.font_edit.text().strip()
            if on and not cur:
                ed.font_edit.setText(macro)
            elif not on and cur == macro:
                ed.font_edit.setText("")
        opts = [o.strip() for o in self.options.toPlainText().split("\n") if o.strip()]
        if on:
            self.luatex.blockSignals(True)
            self.luatex.setChecked(True)     # 한글을 켜면 기본은 LuaTeX (엔진 분기는 Extra 의 \ifluatex 가 컴파일 때 처리)
            self.luatex.blockSignals(False)
            opts = mtxheader.add_hangul_extra(opts, True)
        else:
            opts = mtxheader.remove_hangul_extra(opts)
        self.options.setPlainText("\n".join(opts))

    def _instr_picked(self, idx: int):
        """목록에서 고를 때마다 코드가 뒤에 이어진다 (pi -> pi va ...). 빈 항목을 고르면 지운다."""
        code = self.midi_instr.itemData(idx)
        if not code:
            self._instr_prev = ""
            self.midi_instr.setEditText("")
            return
        base = mtxheader.split_instr_text(self._instr_prev)
        self.midi_instr.setEditText(" ".join(base + [code]))

    def _instr_clear(self):
        self._instr_prev = ""
        self.midi_instr.setCurrentIndex(0)
        self.midi_instr.setEditText("")

    def _instr_text_changed(self, text: str):
        # 목록 항목을 고르면 편집창이 항목 글자로 바뀌므로, 직접 쓴/조합된 글자만 ‘이전 값’으로 기억한다
        if self.midi_instr.findText(text) < 0:
            self._instr_prev = text
        self._midi_field_changed()

    def _midi_field_changed(self, *_):
        """템포나 악기를 넣으면 ‘MIDI 생성’ 도 자동으로 켠다."""
        if (self.midi_tempo.value() or self.midi_instr.currentText().strip()) and not self.midi.isChecked():
            self.midi.setChecked(True)

    def _cs_count_check(self):
        nv = len(self.cs_voices.text().split()); nc = len(self.cs_clefs.text().split())
        if nv != nc:
            self.cs_warn.setText(f"성부 {nv}개 ≠ 음자리표 {nc}개 — 개수를 맞춰 주세요")
        else:
            self.cs_warn.setText("")

    def _cs_compose(self, *_):
        """4개 칸 -> 'NAME: Voices A B; Type; Clefs G F'"""
        if self._cs_busy:
            return
        name = self.cs_name.text().strip().rstrip(":").strip()
        v = " ".join(self.cs_voices.text().split()); t = self.cs_type.text().strip()
        c = " ".join(self.cs_clefs.text().split())
        parts = []
        if v: parts.append(f"Voices {v}")
        if t: parts.append(t)
        if c: parts.append(f"Clefs {c}")
        text = (f"{name}: " if name else "") + "; ".join(parts) if (name or parts) else ""
        self.custom_style.setText(text)
        self._cs_count_check()

    def _cs_parse(self, *_):
        """정의문 -> 4개 칸 (정의문을 직접 고치거나 불러올 때)"""
        text = self.custom_style.text().strip()
        name = ""
        if ":" in text:
            name, text = [x.strip() for x in text.split(":", 1)]
        voices = clefs = typ = ""
        for seg in [x.strip() for x in text.split(";") if x.strip()]:
            low = seg.lower()
            if low.startswith("voices"):
                voices = seg[6:].strip()
            elif low.startswith("clefs"):
                clefs = seg[5:].strip()
            else:
                typ = seg
        self._cs_busy = True
        self.cs_name.setText(name); self.cs_voices.setText(voices)
        self.cs_type.setText(typ); self.cs_clefs.setText(clefs)
        self._cs_busy = False
        self._cs_count_check()

    def _meter_changed(self, t):
        c = (t == "Custom")
        self.mnum.setEnabled(c); self.mden.setEnabled(c)

    def set_state(self, st: Dict) -> None:
        self.title.setText(st['title']); self.name.setText(st['name'])
        self.composer.setText(st['composer']); self.poet.setText(st['poet'])
        self.meter.setCurrentText(st['meter_text'])
        self.mnum.setValue(st['meter_num']); self.mden.setValue(st['meter_den'])
        self.key.setValue(st['key'])
        self.style.setCurrentText(st['style'])
        self.style_override.setText(st['style_override']); self.custom_style.setText(st['custom_style']); self._cs_parse()
        self.size.setCurrentText(st['size']); self.width.setText(st['width'])
        self.bars_line.setValue(st['bars_line']); self.indent.setValue(st['indent'])
        self.space.setText(st['space']); self.systems.setText(st['systems'])
        self.luatex.blockSignals(True); self.luatex.setChecked(st['luatex']); self.luatex.blockSignals(False)
        self.hangul.blockSignals(True); self.hangul.setChecked(st['hangul']); self.hangul.blockSignals(False)
        self.vocal_beam.setChecked(st.get('vocal_beam', False))
        self._midi_rest = st.get('midi_rest', '')
        for wd in (self.midi_tempo, self.midi_instr):
            wd.blockSignals(True)
        self.midi_tempo.setValue(st.get('midi_tempo', 0)); self.midi_instr.setEditText(st.get('midi_instr', ''))
        for wd in (self.midi_tempo, self.midi_instr):
            wd.blockSignals(False)
        self.midi.setChecked(st['midi']); self.ragged.setChecked(st['ragged'])
        self.crop.setChecked(st['crop']); self.meter_off.setChecked(st.get('meter_off', False)); self.pmx.setText(st['pmx'])
        self.options.setPlainText("\n".join(st['options']))

    def state(self) -> Dict:
        st = mtxheader.default_state()
        st.update(
            title=self.title.text().strip(), name=self.name.text().strip(),
            composer=self.composer.text().strip(), poet=self.poet.text().strip(),
            meter_text=self.meter.currentText(), meter_num=self.mnum.value(),
            meter_den=self.mden.value(), key=self.key.value(),
            style=self.style.currentText(), style_override=self.style_override.text().strip(),
            custom_style=self.custom_style.text().strip(),
            size=self.size.currentText(), width=mtxheader.normalize_width(self.width.text()),
            bars_line=self.bars_line.value(), indent=self.indent.value(),
            space=self.space.text().strip(), systems=self.systems.text().strip(),
            luatex=self.luatex.isChecked(), hangul=self.hangul.isChecked(),
            midi=self.midi.isChecked(), ragged=self.ragged.isChecked(), vocal_beam=self.vocal_beam.isChecked(),
            midi_tempo=self.midi_tempo.value(), midi_instr=self.midi_instr.currentText().strip(),
            midi_rest=getattr(self, "_midi_rest", ""),
            crop=self.crop.isChecked(), meter_off=self.meter_off.isChecked(), pmx=self.pmx.text().strip(),
            options=[o.strip() for o in self.options.toPlainText().split("\n") if o.strip()],
        )
        return st


# =============================================================== PMX 수정 패널
class PmxPanel(QWidget):
    """컴파일로 만들어진 .pmx 를 직접 고치는 패널. 고친 내용은 원본과의 차이(패치)로 저장된다."""
    savePatchRequested = pyqtSignal(bool)      # True: 저장 후 컴파일
    deletePatchRequested = pyqtSignal()
    jumpRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.info = QLabel("컴파일하면 생성된 pmx 가 여기에 나타납니다.")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        self.editor = CodeEditor()
        lay.addWidget(self.editor, 1)
        row = QHBoxLayout()
        b_jump = QPushButton("현재 마디로"); b_jump.setToolTip("소스 커서가 있는 마디의 pmx 블록으로 이동")
        b_save = QPushButton("패치 저장"); b_save.setToolTip("원본 pmx 와의 차이를 이름.pmx.patch 로 저장")
        b_run = QPushButton("패치 저장 후 컴파일 (F7)")
        b_del = QPushButton("패치 삭제")
        for b in (b_jump, b_save, b_run, b_del):
            row.addWidget(b)
        lay.addLayout(row)
        b_jump.clicked.connect(self.jumpRequested)
        b_save.clicked.connect(lambda: self.savePatchRequested.emit(False))
        b_run.clicked.connect(lambda: self.savePatchRequested.emit(True))
        b_del.clicked.connect(self.deletePatchRequested)

    def set_text(self, text: str, info: str = "") -> None:
        self.editor.setPlainText(text)
        self.editor.document().setModified(False)
        if info:
            self.info.setText(info)

    def text(self) -> str:
        return self.editor.toPlainText()

    def is_modified(self) -> bool:
        return self.editor.document().isModified()

    def scroll_to_line(self, line0: int) -> None:
        """포커스를 가져오지 않고 해당 줄(0-기준)이 가운데 오게 한다."""
        blk = self.editor.document().findBlockByNumber(line0)
        if blk.isValid():
            c = QTextCursor(blk)
            self.editor.setTextCursor(c)
            self.editor.centerCursor()


class FindBar(QWidget):
    """편집기 위쪽의 찾기/바꾸기 줄. 일반 글자 또는 정규식(Python re), 대소문자 구분, 선택 범위만 바꾸기를 지원한다.
    바꾸기 글자에서 정규식 모드일 때는 \\1, \\g<이름>, \\n, \\t 를 쓸 수 있다."""

    def __init__(self, editor: "CodeEditor", parent=None):
        super().__init__(parent)
        self.editor = editor
        self._err = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(2)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("찾기"))
        self.find = QLineEdit()
        self.find.setPlaceholderText("찾을 글자")
        self.find.setClearButtonEnabled(True)
        r1.addWidget(self.find, 1)
        self.b_prev = QPushButton("◀")
        self.b_next = QPushButton("▶")
        self.b_prev.setToolTip("이전 (Shift+F3)")
        self.b_next.setToolTip("다음 (F3, Enter)")
        for b in (self.b_prev, self.b_next):
            b.setAutoDefault(False)
            b.setFixedWidth(34)
            r1.addWidget(b)
        self.c_regex = QCheckBox("정규식")
        self.c_case = QCheckBox("대소문자")
        self.c_sel = QCheckBox("선택 범위만")
        self.c_sel.setToolTip("‘모두 바꾸기’를 현재 선택한 범위 안에서만 한다")
        for c in (self.c_regex, self.c_case, self.c_sel):
            r1.addWidget(c)
        self.lbl = QLabel("")
        r1.addWidget(self.lbl)
        self.b_close = QPushButton("✕")
        self.b_close.setAutoDefault(False)
        self.b_close.setFixedWidth(28)
        self.b_close.setToolTip("닫기 (Esc)")
        r1.addWidget(self.b_close)
        lay.addLayout(r1)
        self.row2 = QWidget()
        r2 = QHBoxLayout(self.row2)
        r2.setContentsMargins(0, 0, 0, 0)
        r2.addWidget(QLabel("바꾸기"))
        self.repl = QLineEdit()
        self.repl.setPlaceholderText("바꿀 글자 (정규식이면 \\1 등 사용 가능)")
        r2.addWidget(self.repl, 1)
        self.b_rep = QPushButton("바꾸기")
        self.b_all = QPushButton("모두 바꾸기")
        for b in (self.b_rep, self.b_all):
            b.setAutoDefault(False)
            r2.addWidget(b)
        lay.addWidget(self.row2)
        self.hide()
        self.find.textChanged.connect(self._refresh)
        self.c_regex.toggled.connect(self._refresh)
        self.c_case.toggled.connect(self._refresh)
        self.find.returnPressed.connect(lambda: self.step(True))
        self.repl.returnPressed.connect(self.replace_one)
        self.b_next.clicked.connect(lambda: self.step(True))
        self.b_prev.clicked.connect(lambda: self.step(False))
        self.b_rep.clicked.connect(self.replace_one)
        self.b_all.clicked.connect(self.replace_all)
        self.b_close.clicked.connect(self.close_bar)
        self.editor.document().contentsChanged.connect(self._doc_changed)

    # ---- 열고 닫기
    def open_bar(self, replace: bool = False) -> None:
        sel = self.editor.textCursor().selectedText()
        if sel and "\u2029" not in sel and len(sel) < 200:
            self.find.setText(sel)
        self.show()
        self.row2.setVisible(replace)
        (self.repl if replace and self.find.text() else self.find).setFocus()
        self.find.selectAll() if not replace or not self.find.text() else None
        self._refresh()

    def close_bar(self) -> None:
        self.hide()
        self.editor.set_find_spans([])
        self.editor.setFocus()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_Escape:
            self.close_bar()
            return
        super().keyPressEvent(ev)

    # ---- 검색
    def _regex(self):
        pat = self.find.text()
        if not pat:
            return None
        if not self.c_regex.isChecked():
            pat = re.escape(pat)
        try:
            r = re.compile(pat, 0 if self.c_case.isChecked() else re.IGNORECASE)
        except re.error as e:
            self._err = str(e)
            return False
        self._err = ""
        return r

    def _text(self) -> str:
        import mtxmap
        return mtxmap.qt_safe(self.editor.toPlainText())

    def _spans(self, text: Optional[str] = None, lo: int = 0, hi: Optional[int] = None):
        r = self._regex()
        if not r:
            return []
        text = self._text() if text is None else text
        hi = len(text) if hi is None else hi
        out = []
        for m in r.finditer(text, lo, hi):
            if m.end() > m.start():
                out.append((m.start(), m.end()))
            if len(out) >= 20000:
                break
        return out

    def _doc_changed(self) -> None:
        if self.isVisible():
            self._refresh()

    def _refresh(self) -> None:
        if not self.isVisible():
            return
        sp = self._spans()
        self.editor.set_find_spans(sp if len(sp) <= 3000 else [])
        if self._regex() is False:
            self.lbl.setText("정규식 오류")
            self.lbl.setToolTip(self._err)
        else:
            self.lbl.setToolTip("")
            self.lbl.setText(f"{len(sp)}개" if self.find.text() else "")

    def step(self, forward: bool = True) -> None:
        if not self.find.text():
            self.open_bar(self.row2.isVisible())
            return
        sp = self._spans()
        if not sp:
            self.lbl.setText("없음" if self._regex() is not False else "정규식 오류")
            return
        cur = self.editor.textCursor()
        a, b = cur.selectionStart(), cur.selectionEnd()
        if forward:
            hit = next((x for x in sp if x[0] >= b), None) if cur.hasSelection() else next((x for x in sp if x[0] >= a), None)
            wrapped = hit is None
            hit = hit or sp[0]
        else:
            hit = next((x for x in reversed(sp) if x[1] <= a), None)
            wrapped = hit is None
            hit = hit or sp[-1]
        self._select(*hit)
        i = sp.index(hit) + 1
        self.lbl.setText(f"{i}/{len(sp)}" + (" (처음부터)" if wrapped and forward else " (끝부터)" if wrapped else ""))

    def _select(self, s: int, e: int) -> None:
        c = QTextCursor(self.editor.document())
        c.setPosition(s)
        c.setPosition(e, QTextCursor.MoveMode.KeepAnchor)
        self.editor.setTextCursor(c)
        self.editor.centerCursor() if not self.editor.cursorRect().intersects(self.editor.viewport().rect()) else None

    # ---- 바꾸기
    def _expand(self, r, text: str, span) -> str:
        m = r.match(text, span[0], span[1])
        if not self.c_regex.isChecked():
            return self.repl.text()
        return m.expand(self.repl.text())

    def replace_one(self) -> None:
        r = self._regex()
        if not r:
            return
        text = self._text()
        cur = self.editor.textCursor()
        a, b = cur.selectionStart(), cur.selectionEnd()
        if cur.hasSelection() and r.fullmatch(text, a, b) is not None and b > a:
            try:
                new = self._expand(r, text, (a, b))
            except (re.error, IndexError) as e:
                self.lbl.setText("바꿀 글자 오류")
                self.lbl.setToolTip(str(e))
                return
            cur.insertText(new)
            self.editor.setTextCursor(cur)
        self.step(True)

    def replace_all(self) -> None:
        r = self._regex()
        if not r:
            return
        text = self._text()
        lo, hi = 0, len(text)
        cur = self.editor.textCursor()
        if self.c_sel.isChecked() and cur.hasSelection():
            lo, hi = cur.selectionStart(), cur.selectionEnd()
        sp = self._spans(text, lo, hi)
        if not sp:
            self.lbl.setText("없음")
            return
        try:
            news = [self._expand(r, text, s) for s in sp]
        except (re.error, IndexError) as e:
            self.lbl.setText("바꿀 글자 오류")
            self.lbl.setToolTip(str(e))
            return
        c = QTextCursor(self.editor.document())
        c.beginEditBlock()
        for (s, e), new in reversed(list(zip(sp, news))):
            c.setPosition(s)
            c.setPosition(e, QTextCursor.MoveMode.KeepAnchor)
            c.insertText(new)
        c.endEditBlock()
        self._refresh()
        self.lbl.setText(f"{len(sp)}곳 바꿈 (Ctrl+Z 로 한 번에 되돌림)")
