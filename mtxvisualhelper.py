#!/usr/bin/env python3
#
# M-Tx Visual Helper 
# (C) 2026 Nova de Hi
# version 2.2.3 (accidentals+none)
# version 2.2.2 (asterisk syntax in apply_rhythm)
# version 2.2 (enharmonic flip & rests in rhythm & double sharps|flats )
# version 2.0 (Key Signature based enharmonic & argparse)
# version 1.9.4 enharmonic combo -> button
# version 1.9.3 (UI)
# version 1.9.2 (apply_rhythm to selection)
# version 1.9.1 (minor adjustments)
# version 1.9   (apply rhythm overlay)
import sys
import os
import math
import struct
import platform
import pyautogui
import re
import argparse
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, 
                             QPushButton, QButtonGroup, QCheckBox, QComboBox, 
                             QSpinBox, QLabel, QPlainTextEdit, QGroupBox, QGridLayout,
                             QMessageBox, QFrame, QInputDialog)
from PyQt6.QtGui import QTextCursor, QKeySequence, QShortcut, QIcon
from PyQt6.QtCore import Qt, QTimer, QBuffer, QByteArray, QIODevice, QEvent, QSettings
from PyQt6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices

__version__ = "2.2.3"

class MTxVisualInputWidget(QWidget):
    def __init__(self, initial_key=0):
        super().__init__()
        self.initial_key = initial_key  # [추가] 외부에서 전달받은 조표 값
        self.octave_shift = 0
        self.current_beats = 0.0
        self.action_history = []
        self.last_pitch_value = None 
        self.pending_duration = None
        self.tuplet_remaining = 0

        self.grace_mode = False
        self.grace_target_count = 0  
        self.grace_wait_main = True
        self.grace_pitches = []

        self.last_pitch_value = None 
        self.last_diatonic_index = None  
        self.pending_duration = None

        self.last_sent_text = ""

        # 코드 버퍼
        self.chord_buffer = []
        self.force_absolute_next = False  
        
        # 오디오 설정
        self.audio_format = QAudioFormat()
        self.audio_format.setSampleRate(44100)
        self.audio_format.setChannelCount(1)
        self.audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        self.audio_sink = QAudioSink(QMediaDevices.defaultAudioOutput(), self.audio_format, self)
        self.audio_buffer = QBuffer(self)
        self.pcm_data = QByteArray()
        
        self.pitch_offsets = {'c': 0, 'cs': 1, 'df': 1, 'd': 2, 'ds': 3, 'ef': 3, 'e': 4, 'f': 5,
                              'fs': 6, 'gf': 6, 'g': 7, 'gs': 8, 'af': 8, 'a': 9, 'as': 10, 'bf': 10, 'b': 11}
        
        self.sharp_to_flat = {'cs': 'df', 'ds': 'ef', 'fs': 'gf', 'gs': 'af', 'as': 'bf'}

        self.beat_values = {
            '0': 4.0, '0d': 6.0, '2': 2.0, '2d': 3.0, '4': 1.0, '4d': 1.5,
            '8': 0.5, '8d': 0.75, '1': 0.25, '1d': 0.375, '3': 0.125, '3d': 0.1875,
            '6': 0.0625, '6d': 0.09375, '9': 8.0, '9d': 12.0
        }
        
        self.note_chain = {
            '0': '2', '2': '4', '2d': '4', '4': '8', '4d': '8', 
            '8': '1', '8d': '1', '1': '3', '1d': '3', '3': '6'
        }
        
        self.ornaments_data = [("스타카토 (o.)", "o."), ("테누토 (o_)", "o_"), ("악센트 (o>)", "o>"),
                               ("스포르찬도 (o^)", "o^"), 
                               ("모르덴트 (om)", "om"),
                               ("셰이크 (ot)", "ot"),
                               ("체주라 (oc)", "oc"), ("숨표 (ob)", "ob"),
                               ("페르마타 위 (of)", "of"), ("페르마타 아래 (ofd)", "ofd"),
                               ("트릴 (oT0)", "oT0"),
                               ("트릴 (oT)", "oT"),
                               ("아르페지오 토글", "?")]

        self.initUI()
        self.setup_shortcuts()
        self.text_edit.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj == self.text_edit and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_BracketLeft:
                self.smart_line_shortcut()
                return True
            elif event.key() == Qt.Key.Key_BracketRight:
                self.line_end_btn.click()
                return True
            elif event.key() == Qt.Key.Key_Escape:
                self.text_edit.moveCursor(QTextCursor.MoveOperation.End)
                self.text_edit.clearFocus()
                self.setFocus()
                return True
        return super().eventFilter(obj, event)

    def play_note_sound(self, pitch_val):
        freq = 440.0 * math.pow(2.0, (pitch_val - 69) / 12.0)
        sample_rate, duration_ms = 44100, 400
        num_samples = int(sample_rate * duration_ms / 1000)
        fade_samples = int(sample_rate * 50 / 1000)
        
        self.pcm_data.clear()
        for i in range(num_samples):
            vol = (num_samples - i) / fade_samples if i > num_samples - fade_samples else 1.0
            val = math.sin(2.0 * math.pi * freq * i / sample_rate)
            sample = int(val * 0.2 * 32767 * vol)
            self.pcm_data.append(struct.pack('<h', sample))
        
        try:
            if self.audio_sink.state() != 0: self.audio_sink.stop()
        except: pass
            
        self.audio_buffer.close()
        self.audio_buffer.setData(self.pcm_data)
        self.audio_buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self.audio_buffer.seek(0)
        self.audio_sink.start(self.audio_buffer)

    def initUI(self):
        main_layout = QVBoxLayout()
        main_layout.setSpacing(5)
        main_layout.setContentsMargins(10, 10, 10, 10)

        insert_action_style = "QPushButton { background-color: #dcedc8; color: black; border: 1px solid #9ccc65; border-radius: 4px; padding: 4px; }"

        # ==========================================
        # 1. 음표 길이 그룹
        # ==========================================
        dur_group = QGroupBox("음표 길이 (Duration)")
        dur_layout = QGridLayout()
        self.dur_btn_group = QButtonGroup(self)
        
        durations = [
            ('0', '온', 0, 0), ('2', '2', 0, 1), ('2d', '2.', 0, 2), ('4', '♩', 0, 3), ('4d', '♩.', 0, 4),
            ('8', '♪', 1, 0), ('8d', '♪.', 1, 1), ('1', '♬', 1, 2), ('1d', '♬.', 1, 3), ('3', '32', 1, 4), ('6', '64', 1, 5)
        ]
        
        for v, s, r, c in durations:
            btn = QPushButton(f"{s}\n{v}")
            btn.setProperty("mtx_val", v)
            btn.setCheckable(True)
            btn.setFixedSize(45, 42)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            if v == '4': btn.setChecked(True)
            self.dur_btn_group.addButton(btn)
            dur_layout.addWidget(btn, r, c)
            
        rest_btn = QPushButton("쉼표\n(r)")
        rest_btn.setFixedSize(45, 42)
        rest_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        rest_btn.setStyleSheet(insert_action_style)
        rest_btn.clicked.connect(self.insert_rest)
        dur_layout.addWidget(rest_btn, 0, 5)

        mode_btn_style = """
            QPushButton { background-color: #acacac; color: black; border: 1px solid #b0b0b0; border-radius: 4px; padding: 4px; }
            QPushButton:checked { background-color: #aaccff; color: black; border: 1px solid #0055ff; font-weight: bold; }
        """

        self.mode_dotted = QPushButton("Dot+1/2\n(부점)")
        self.mode_dotted.setCheckable(True)
        self.mode_dotted.setStyleSheet(mode_btn_style)
        self.mode_dotted.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.mode_compound = QPushButton("4 + 8\n(세트)")
        self.mode_compound.setCheckable(True)
        self.mode_compound.setStyleSheet(mode_btn_style)
        self.mode_compound.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.mode_dotted.toggled.connect(lambda checked: self.mode_compound.setChecked(False) if checked else None)
        self.mode_compound.toggled.connect(lambda checked: self.mode_dotted.setChecked(False) if checked else None)

        dur_layout.addWidget(self.mode_dotted, 0, 6)
        dur_layout.addWidget(self.mode_compound, 1, 6)
        self.dur_btn_group.buttonClicked.connect(self.reset_modes)
        dur_group.setLayout(dur_layout)
        main_layout.addWidget(dur_group)

        # ==========================================
        # 2. 수식어 및 패턴 제어 (접기/펼치기)
        # ==========================================
        tools_group = QGroupBox("수식어 및 특수 모드")
        tools_group.setCheckable(True)
        tools_group.setChecked(False) 

        self.tools_container = QWidget()
        tools_layout = QGridLayout(self.tools_container)
        tools_layout.setSpacing(3)
        tools_layout.setContentsMargins(0, 5, 0, 0) 
                
        # --- [Row 0] 악상/장식음 ---
        self.dyn_combo = QComboBox()
        self.dyn_combo.addItems(['mf','mp','f','p','ff','pp','fff','ppp','fp','sfz','<','>'])
        dyn_btn = QPushButton("악상")
        dyn_btn.setStyleSheet(insert_action_style)
        dyn_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        dyn_btn.clicked.connect(self.insert_dynamic)
        
        self.orn_combo = QComboBox()
        for t, _ in self.ornaments_data: self.orn_combo.addItem(t)
        orn_btn = QPushButton("장식음")
        orn_btn.setStyleSheet(insert_action_style)
        orn_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        orn_btn.clicked.connect(self.insert_ornament)
        
        tools_layout.addWidget(self.dyn_combo, 0, 0)
        tools_layout.addWidget(dyn_btn, 0, 1)
        tools_layout.addWidget(self.orn_combo, 0, 2)
        tools_layout.addWidget(orn_btn, 0, 3)

        # --- [Row 1] 타이 패턴 ---
        self.tie_pattern_panel = QFrame()
        self.tie_pattern_panel.setFrameShape(QFrame.Shape.StyledPanel)
        self.tie_pattern_panel.setStyleSheet(
            "QFrame { background-color: #f8f9fa; border: 1px solid #dee2e6; border-radius: 4px; } "
            "QCheckBox { font-size: 11px; color: black; }"
        )
        pattern_layout = QHBoxLayout(self.tie_pattern_panel)
        pattern_layout.setContentsMargins(5, 2, 5, 2)
        pattern_layout.setSpacing(5)
        
        self.pattern_combo = QComboBox()
        self.load_custom_patterns()
        self.pattern_combo.setFixedWidth(110)
        self.pattern_combo.currentIndexChanged.connect(self.on_pattern_combo_changed)
        
        self.continuous_cb = QCheckBox("연속")
        self.pattern_mode_btn = QPushButton("타이 패턴")
        self.pattern_mode_btn.setCheckable(True)
        self.pattern_mode_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.pattern_mode_btn.toggled.connect(self.toggle_pattern_mode)
        
        pattern_layout.addWidget(QLabel("🔗"))
        pattern_layout.addWidget(self.pattern_combo)
        pattern_layout.addWidget(self.continuous_cb)
        pattern_layout.addWidget(self.pattern_mode_btn, 1)
        tools_layout.addWidget(self.tie_pattern_panel, 1, 0, 1, 4)

        # --- [Row 2] 선 제어 ---
        self.line_combo = QComboBox()
        self.line_combo.addItems(['슬러 ( )', '타이 { }', '점선 (" )', '빔 [ ]'])
        self.line_id_spin = QSpinBox()
        self.line_id_spin.setRange(0, 9)
        self.line_id_spin.setPrefix("ID: ")
        self.line_id_spin.setSpecialValueText("없음")

        self.line_start_btn = QPushButton("시작 예약")
        self.line_start_btn.setCheckable(True)
        self.line_start_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        
        self.line_end_btn = QPushButton("위치 종료")
        self.line_end_btn.setStyleSheet(insert_action_style)
        self.line_end_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.line_end_btn.clicked.connect(self.insert_line_end)
        
        tools_layout.addWidget(self.line_combo, 2, 0)
        tools_layout.addWidget(self.line_id_spin, 2, 1)
        tools_layout.addWidget(self.line_start_btn, 2, 2)
        tools_layout.addWidget(self.line_end_btn, 2, 3)

        self.shortcut_slur = QShortcut(QKeySequence("Alt+L"), self)
        self.shortcut_slur.activated.connect(lambda: self.line_combo.setCurrentIndex(0))
        self.shortcut_tie = QShortcut(QKeySequence("Alt+T"), self)
        self.shortcut_tie.activated.connect(lambda: self.line_combo.setCurrentIndex(1))
        self.shortcut_dotted = QShortcut(QKeySequence("Alt+D"), self)
        self.shortcut_dotted.activated.connect(lambda: self.line_combo.setCurrentIndex(2))
        self.shortcut_beam = QShortcut(QKeySequence("Alt+B"), self)
        self.shortcut_beam.activated.connect(lambda: self.line_combo.setCurrentIndex(3))

        # --- [Row 3] 마디(Bar) & 잇단음표(Tuplet) ---
        self.bar_combo = QComboBox()
        self.bar_combo.addItems(['|', '||', ':|', '|:', '|]'])
        bar_btn = QPushButton("Bar")
        bar_btn.setStyleSheet(insert_action_style)
        bar_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        bar_btn.clicked.connect(self.insert_bar)
        
        tools_layout.addWidget(self.bar_combo, 3, 0)
        tools_layout.addWidget(bar_btn, 3, 1)

        tuplet_layout = QHBoxLayout()
        self.tuplet_spin = QSpinBox()
        self.tuplet_spin.setRange(3, 9)
        self.tuplet_spin.setValue(3)
        self.tuplet_spin.setFixedWidth(35)
        
        self.tuplet_btn = QPushButton("잇단음표")
        self.tuplet_btn.setCheckable(True)
        self.tuplet_btn.setStyleSheet(mode_btn_style)
        self.tuplet_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        
        tuplet_layout.addWidget(self.tuplet_spin)
        tuplet_layout.addWidget(self.tuplet_btn)
        tools_layout.addLayout(tuplet_layout, 3, 2, 1, 2)

        # --- [Row 5] 그레이스 노트 ---
        self.grace_combo = QComboBox()
        self.grace_combo.addItems(["1음 (사선 X)", "1음 (사선 O)", "2음 (앞 꾸밈)"])
        self.grace_btn = QPushButton("🎵 꾸밈음 모드 시작")
        self.grace_btn.setCheckable(True)
        self.grace_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.grace_btn.clicked.connect(self.toggle_grace_mode)
        tools_layout.addWidget(self.grace_combo, 4, 0, 1, 2)
        tools_layout.addWidget(self.grace_btn, 4, 2, 1, 2)

        group_layout = QVBoxLayout()
        group_layout.setContentsMargins(5, 5, 5, 5) 
        group_layout.addWidget(self.tools_container)
        tools_group.setLayout(group_layout)

        tools_group.toggled.connect(self.tools_container.setVisible)
        self.tools_container.setVisible(tools_group.isChecked())

        main_layout.addWidget(tools_group)

        # ==========================================
        # 2.5 일회성 임시표 및 화음
        # ==========================================
        acc_chord_layout = QHBoxLayout()
        acc_chord_layout.setSpacing(0)
        
        self.acc_btn_group = QButtonGroup(self)
        self.acc_btn_group.setExclusive(False) # 자유롭게 켜고 끄기 위함
        
        # '기본', 'None' 삭제. 순수 오버라이드 임시표만 남김
        acc_data = [("X", "none"), ("♯", "s"), ("♭", "f"), ("♮", "n"), ("𝄪", "ss"), ("𝄫", "ff")]
        for text, val in acc_data:
            btn = QPushButton(text)
            btn.setProperty("acc_val", val)
            btn.setCheckable(True)
            btn.setFixedSize(32, 30)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.clicked.connect(self._on_acc_toggled)
            self.acc_btn_group.addButton(btn)
            acc_chord_layout.addWidget(btn)
        
        self.abs_pitch_btn = QPushButton("=")
        self.abs_pitch_btn.setCheckable(True)
        self.abs_pitch_btn.setChecked(True)
        self.abs_pitch_btn.setFixedSize(30, 30)
        self.abs_pitch_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        acc_chord_layout.addWidget(self.abs_pitch_btn)

        self.base_c3_cb = QCheckBox("3")
        self.base_c3_cb.setToolTip("체크 시 낮은음자리표(왼손) 보표용으로 C3를 기준(=)으로 삼습니다.")
        self.base_c3_cb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.base_c3_cb.setStyleSheet("QCheckBox { margin-left: 2px; margin-right: 2px; }") 
        acc_chord_layout.addWidget(self.base_c3_cb)

        acc_chord_layout.addStretch()

        # ==========================================
        self.flip_enhar_btn = QPushButton("이명동음(Flip)")
        self.flip_enhar_btn.setToolTip("마지막 입력 음표의 이명동음 전환 (Ctrl+/)")
        self.flip_enhar_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.flip_enhar_btn.setStyleSheet("background-color: #e0e0e0; color: black; border: 1px solid #b0b0b0; border-radius: 4px; padding: 2px 6px;")
        self.flip_enhar_btn.clicked.connect(self.flip_last_enharmonic)
        acc_chord_layout.addWidget(self.flip_enhar_btn)

        acc_chord_layout.addStretch()

        self.chord_cb = QCheckBox("화음(z)")
        self.chord_cb.setStyleSheet("font-weight: bold;")
        self.chord_cb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        acc_chord_layout.addWidget(self.chord_cb)
        self.chord_cb.toggled.connect(self.on_chord_toggled)

        main_layout.addLayout(acc_chord_layout)

        # ==========================================
        # 3. 피아노 영역 및 조표(Key) 스핀박스
        # ==========================================
        piano_header = QHBoxLayout()
        self.oct_label = QLabel("F3 ~ A5")
        btn_dn = QPushButton("◀")
        btn_dn.clicked.connect(lambda: self.change_octave(-1))
        btn_dn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        
        btn_up = QPushButton("▶")
        btn_up.clicked.connect(lambda: self.change_octave(1))
        btn_up.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        
        self.mute_cb = QCheckBox("소리 끔")
        self.mute_cb.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        
        # enharmonic 버튼 삭제하고 Key Signature 적용
        self.key_spin = QSpinBox()
        self.key_spin.setRange(-7, 7)
        self.key_spin.setValue(self.initial_key)
        self.key_spin.setPrefix("Key: ")
        self.key_spin.setToolTip("+는 샵(Sharp), -는 플랫(Flat) 조표")
        self.key_spin.setStyleSheet("background-color: #f0f0f0; color: black; font-weight: bold;")
        self.key_spin.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        
        piano_header.addWidget(btn_dn)
        piano_header.addWidget(self.oct_label)
        piano_header.addWidget(btn_up)
        piano_header.addStretch()
        piano_header.addWidget(self.mute_cb)
        piano_header.addWidget(self.key_spin)

        self.piano_widget = QWidget()
        self.piano_widget.setFixedSize(425, 90)
        self.draw_piano_keys()
        
        main_layout.addLayout(piano_header)
        main_layout.addWidget(self.piano_widget, alignment=Qt.AlignmentFlag.AlignCenter)

        # ==========================================
        # 4. 하단 영역
        # ==========================================
        self.text_edit = QPlainTextEdit()
        self.text_edit.setMaximumHeight(60)
        self.text_edit.textChanged.connect(self.recalculate_beats)
        main_layout.addWidget(self.text_edit)

        action_layout = QHBoxLayout()
        self.beat_label = QLabel("0.0 박")
        
        self.top_hint_cb = QCheckBox("항상 위")
        self.top_hint_cb.setChecked(True)
        self.top_hint_cb.stateChanged.connect(self.toggle_always_on_top)

        self.rhythm_btn = QPushButton("리듬")
        self.rhythm_btn.setStyleSheet("background-color: #ffb74d; color: black; font-weight: bold;")
        self.rhythm_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.rhythm_btn.clicked.connect(self.apply_rhythm_overlay)

        send_btn = QPushButton("🚀 VS Code 전송")
        send_btn.setStyleSheet("background-color: #007acc; color: white; font-weight: bold;")
        send_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        send_btn.clicked.connect(self.smart_send)
        
        back_btn = QPushButton("취소")
        back_btn.setToolTip("마지막 입력 취소 (Ctrl+Z)")
        back_btn.setFixedWidth(40)
        back_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        back_btn.clicked.connect(self.backspace_text)

        reset_btn = QPushButton("🗑️")
        reset_btn.setToolTip("모두 지우고 새로 시작 (Ctrl+BS))")
        reset_btn.setFixedWidth(35) 
        reset_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        reset_btn.clicked.connect(self.reset_all)
        
        action_layout.addWidget(self.beat_label); action_layout.addStretch()
        action_layout.addStretch()
        action_layout.addWidget(self.rhythm_btn)
        action_layout.addWidget(self.top_hint_cb)
        action_layout.addWidget(send_btn)
        action_layout.addWidget(back_btn)
        action_layout.addWidget(reset_btn) 

        main_layout.addLayout(action_layout)

        self.setLayout(main_layout)
 
    # --- 추가 메서드들 ---
    def _on_acc_toggled(self):
        sender = self.sender()
        if sender.isChecked():
            sender_val = sender.property("acc_val")
            for btn in self.acc_btn_group.buttons():
                if btn != sender:
                    btn_val = btn.property("acc_val")
                    
                    # 'none'과 'f'(또는 's')는 서로 배타적으로 작동하지 않고 함께 켜질 수 있도록 허용
                    if (sender_val == "none" and btn_val in ["f", "s"]) or \
                       (sender_val in ["f", "s"] and btn_val == "none"):
                        continue
                        
                    btn.setChecked(False)

    def load_custom_patterns(self):
        self.pattern_combo.clear()
        defaults = ["4+4", "8+8", "8+4", "16+4", "16+8", "16+16", "4.+4"]
        settings = QSettings("ktug.org", "MTxVisualHelper")
        customs = settings.value("custom_patterns", [])
        self.pattern_combo.addItems(defaults)
        if customs: self.pattern_combo.addItems(customs)
        self.pattern_combo.insertSeparator(self.pattern_combo.count())
        self.pattern_combo.addItem("➕ 새 패턴 등록...")

    def on_pattern_combo_changed(self, index):
        if self.pattern_combo.itemText(index) == "➕ 새 패턴 등록...": self.add_custom_pattern()

    def add_custom_pattern(self):
        msg = "새 리듬 패턴을 입력하세요.\n예: 4+8, 16+16, 4.+4"
        new_pattern, ok = QInputDialog.getText(self, "새 패턴 등록", msg)
        if ok and new_pattern.strip() and "+" in new_pattern:
            pattern = new_pattern.strip().replace(" ", "")
            settings = QSettings("ktug.org", "MTxVisualHelper")
            customs = settings.value("custom_patterns", [])
            if pattern not in customs:
                customs.append(pattern)
                settings.setValue("custom_patterns", customs)
            self.load_custom_patterns()
            new_index = self.pattern_combo.findText(pattern)
            if new_index != -1: self.pattern_combo.setCurrentIndex(new_index)
        else:
            self.pattern_combo.setCurrentIndex(0)

    def get_mtx_duration(self, user_val):
        mapping = {"16": "1", "8": "8", "4": "4", "2": "2", "4.": "4d", "8.": "8d"}
        return mapping.get(user_val.strip(), user_val)

    def toggle_pattern_mode(self, checked):
        if checked:
            self.pattern_mode_btn.setText("🔗 패턴 모드 ON")
            self.pattern_mode_btn.setStyleSheet("background-color: #d1ffd1; border: 2px solid #5cb85c; font-weight: bold; color: black;")
        else:
            self.pattern_mode_btn.setText("타이 패턴")
            self.pattern_mode_btn.setStyleSheet("")

    def on_chord_toggled(self, checked):
        if checked:
            self.chord_buffer = []
            self.chord_cb.setText("화음 담는 중... (끄면 출력)")
            self.chord_cb.setStyleSheet("background-color: #ffcccc;") 
        else:
            self.chord_cb.setText("화음 (z)")
            self.chord_cb.setStyleSheet("")
            if self.chord_buffer:
                self.commit_chord()

    def format_mtx_note(self, name, mod, dur):
        if len(name) > 1 and mod:
            return f"{name}{dur}{mod}"
        return f"{name}{mod}{dur}"

    def commit_chord(self):
        sel_dur = self.dur_btn_group.checkedButton().property('mtx_val')
        actual_dur = sel_dur
        b_add = 0

        if self.tuplet_btn.isChecked():
            if self.tuplet_remaining == 0:
                n = self.tuplet_spin.value()
                self.tuplet_remaining = n - 1
                actual_dur = f"{sel_dur}x{n}"
                b_add = self.beat_values.get(sel_dur, 0)
            else:
                self.tuplet_remaining -= 1; actual_dur = ""
                if self.tuplet_remaining <= 0: self.tuplet_btn.setChecked(False)
        else:
            if self.pending_duration: actual_dur = self.pending_duration; self.pending_duration = None
            elif self.mode_dotted.isChecked(): actual_dur = sel_dur + "d"; self.pending_duration = self.note_chain.get(sel_dur)
            elif self.mode_compound.isChecked(): actual_dur = sel_dur; self.pending_duration = self.note_chain.get(sel_dur)
            b_add = self.beat_values.get(actual_dur, 0)

        base_n = self.chord_buffer[0]
        base_str = self.format_mtx_note(base_n['name'], base_n['mod'], actual_dur)
        chord_strs = [base_str]

        for n in self.chord_buffer[1:]:
            z_str = self.format_mtx_note(n['name'], n['mod'], "")
            chord_strs.append(f"z{z_str}")
            
        final_txt = " ".join(chord_strs) + " "

        prefix = ""
        if self.line_start_btn.isChecked() and not self.pending_duration:
            choice = self.line_combo.currentText()
            val = self.line_id_spin.value()
            l_id = str(val) if val > 0 else ""

            if "슬러" in choice: prefix = f" ({l_id} "
            elif "타이" in choice: prefix = f" {{{l_id} "
            elif "점선" in choice: prefix = f" (\"{l_id} "
            elif "빔" in choice: prefix = " [ "
            
            self.line_start_btn.setChecked(False)

        self.process(prefix + final_txt, b_add, None, None)
        self.chord_buffer = []

    def on_piano_clicked(self, name, oct_loc):
        # 1. 피치 값 및 사운드 재생 (원래대로 바로 재생!)
        act_oct = oct_loc + self.octave_shift + 4
        current_val = (act_oct * 12) + self.pitch_offsets[name] + 12

        if not self.mute_cb.isChecked():
            self.play_note_sound(current_val)

        # 2. 조표(Key Signature)에 따른 스펠링 및 임시표 계산
        key_sig = self.key_spin.value()
        pitch_idx = self.pitch_offsets[name] 

        sharp_order = ['f', 'c', 'g', 'd', 'a', 'e', 'b']
        flat_order = ['b', 'e', 'a', 'd', 'g', 'c', 'f']

        key_mod = {}
        if key_sig > 0:
            for n in sharp_order[:key_sig]: key_mod[n] = 's'
        elif key_sig < 0:
            for n in flat_order[:abs(key_sig)]: key_mod[n] = 'f'

        spellings_sharp = [('c',''), ('c','s'), ('d',''), ('d','s'), ('e',''), 
                           ('f',''), ('f','s'), ('g',''), ('g','s'), ('a',''), ('a','s'), ('b','')]
        spellings_flat = [('c',''), ('d','f'), ('d',''), ('e','f'), ('e',''), 
                          ('f',''), ('g','f'), ('g',''), ('a','f'), ('a',''), ('b','f'), ('b','')]

        default_spellings = spellings_sharp if key_sig >= 0 else spellings_flat
        base_note, default_acc = default_spellings[pitch_idx]

        # 체크된 모든 버튼의 acc_val 값을 리스트로 수집
        active_accs = [btn.property("acc_val") for btn in self.acc_btn_group.buttons() if btn.isChecked()]
        
        if not active_accs:
            expected_acc = key_mod.get(base_note, '')
            if default_acc == expected_acc:
                final_pitch_name = base_note
            else:
                final_pitch_name = f"{base_note}n" if default_acc == '' else f"{base_note}{default_acc}"
        else:
            if "none" in active_accs:
                # "none"이 눌린 상태에서 "f"나 "s"가 함께 눌렸는지 확인하여 스펠링 결정
                if "f" in active_accs:
                    base_note = spellings_flat[pitch_idx][0]
                elif "s" in active_accs:
                    base_note = spellings_sharp[pitch_idx][0]
                else:
                    base_note = default_spellings[pitch_idx][0]
                    
                final_pitch_name = base_note
            else:
                # none 없이 일반 임시표가 단독으로 눌린 경우 (기존 강제 임시표 로직)
                acc_val = active_accs[0]
                acc_shift = {'ss': 2, 's': 1, 'n': 0, 'f': -1, 'ff': -2}.get(acc_val, 0)
                
                raw_base_pitch = pitch_idx - acc_shift
                base_pitch = raw_base_pitch % 12
                
                pitch_to_base_note = {0: 'c', 2: 'd', 4: 'e', 5: 'f', 7: 'g', 9: 'a', 11: 'b'}
                
                if base_pitch in pitch_to_base_note:
                    base_note = pitch_to_base_note[base_pitch]
                    act_oct += (raw_base_pitch // 12)
                else:
                    if acc_val in ['s', 'ss']: base_note = spellings_sharp[pitch_idx][0]
                    elif acc_val in ['f', 'ff']: base_note = spellings_flat[pitch_idx][0]
                
                final_pitch_name = f"{base_note}{acc_val}" if acc_val != 'n' else f"{base_note}n"
            
            # 사용 후 모든 임시표 버튼 체크 일괄 해제
            for btn in self.acc_btn_group.buttons():
                btn.setChecked(False)

        # 3. 온음계적 위치 계산 (변경된 base_note와 act_oct 기준)
        diatonic_map = {'c':0, 'd':1, 'e':2, 'f':3, 'g':4, 'a':5, 'b':6}
        current_diatonic = (act_oct * 7) + diatonic_map[base_note]

        mod = ""
        is_tuplet_active = self.tuplet_btn.isChecked()
        force_abs = getattr(self, 'force_absolute_next', False)
        
        if getattr(self, 'last_diatonic_index', None) is None or ((self.abs_pitch_btn.isChecked() or force_abs) and not is_tuplet_active):            
            base_octave = 3 if self.base_c3_cb.isChecked() else 4
            diff = act_oct - base_octave
            
            mod = "=" + ("+" * diff if diff > 0 else "-" * abs(diff))
            self.abs_pitch_btn.setChecked(False)
            self.force_absolute_next = False 
        else:
            d_diatonic = current_diatonic - self.last_diatonic_index
            if d_diatonic > 3:   
                mod = "+" * ((d_diatonic + 3) // 7)
            elif d_diatonic < -3: 
                mod = "-" * ((abs(d_diatonic) + 3) // 7)
            
            if force_abs:
                self.force_absolute_next = False
            
        # 화음 모드일 경우
        if self.chord_cb.isChecked():
            self.chord_buffer.append({
                'name': final_pitch_name,
                'mod': mod,
                'val': current_val,
                'dia': current_diatonic
            })
            
            self.last_pitch_value = current_val
            self.last_diatonic_index = current_diatonic
            
            chord_preview = " ".join([f"{n['name']}{n['mod']}" for n in self.chord_buffer])
            self.chord_cb.setText(f"화음: {chord_preview} (끄면 출력)")
            return

        # 패턴 모드
        if self.pattern_mode_btn.isChecked():
            pattern = self.pattern_combo.currentText()
            parts = pattern.split('+')
            d1 = self.get_mtx_duration(parts[0])
            d2 = self.get_mtx_duration(parts[1])
            n1 = self.format_mtx_note(final_pitch_name, mod, d1)
            n2 = self.format_mtx_note(final_pitch_name, "", d2)
            res = f"{{ {n1} {n2} }} "
            total_b = self.beat_values.get(d1, 0) + self.beat_values.get(d2, 0)
            self.process(res, total_b, current_val, current_diatonic)
            if not self.continuous_cb.isChecked(): self.pattern_mode_btn.setChecked(False)
            return

        # 꾸밈음 모드
        if self.grace_mode:
            clean_p = (final_pitch_name + mod).replace('=', '')
            if len(self.grace_pitches) < self.grace_target_count:
                self.grace_pitches.append(clean_p)
                self.grace_btn.setText(f"입력 중 ({len(self.grace_pitches)}/{self.grace_target_count+1})")
                return
            else:
                sel_dur = self.dur_btn_group.checkedButton().property('mtx_val')
                g = self.grace_pitches
                idx = self.grace_combo.currentIndex()
                main_note = self.format_mtx_note(final_pitch_name, mod, sel_dur)
                if idx == 0: res = f"Gs{g[0]} {main_note} "
                elif idx == 1: res = f"Gsx{g[0]} {main_note} "
                else: res = f"G2sm2{g[0]} {g[1]} {main_note} "
                self.process(res, self.beat_values.get(sel_dur, 0), current_val, current_diatonic)
                self.reset_grace_mode(); return

        sel_dur = self.dur_btn_group.checkedButton().property('mtx_val')
        actual_dur = sel_dur
        b_add = 0

        # 길이 설정
        if self.tuplet_btn.isChecked():
            if self.tuplet_remaining == 0:
                n = self.tuplet_spin.value()
                self.tuplet_remaining = n - 1
                actual_dur = f"{sel_dur}x{n}"
                b_add = self.beat_values.get(sel_dur, 0)
            else:
                self.tuplet_remaining -= 1
                actual_dur = ""
                if self.tuplet_remaining <= 0: self.tuplet_btn.setChecked(False)
        else:
            if self.pending_duration: actual_dur = self.pending_duration; self.pending_duration = None
            elif self.mode_dotted.isChecked(): actual_dur = sel_dur + "d"; self.pending_duration = self.note_chain.get(sel_dur)
            elif self.mode_compound.isChecked(): actual_dur = sel_dur; self.pending_duration = self.note_chain.get(sel_dur)
            b_add = self.beat_values.get(actual_dur, 0)
        
        prefix = ""
        if self.line_start_btn.isChecked() and not self.pending_duration:
            choice = self.line_combo.currentText()
            val = self.line_id_spin.value()
            l_id = str(val) if val > 0 else ""
            
            if "슬러" in choice: prefix = f" ({l_id} "
            elif "타이" in choice: prefix = f" {{{l_id} "
            elif "점선" in choice: prefix = f" (\"{l_id} "
            elif "빔" in choice: prefix = " [ "
            
            self.line_start_btn.setChecked(False)

        main_note = self.format_mtx_note(final_pitch_name, mod, actual_dur)
        final_txt = f"{prefix}{'z' if self.chord_cb.isChecked() else ''}{main_note} "
        
        self.process(final_txt, b_add, None if self.chord_cb.isChecked() else current_val, None if self.chord_cb.isChecked() else current_diatonic)

    def process(self, txt, b, p=None, d_idx=None):
        current_text = self.text_edit.toPlainText()
        if current_text and not current_text.endswith((' ', '\n')) and not txt.startswith(' '):
            txt = ' ' + txt

        self.action_history.append({'t': txt, 'b': b, 'p': self.last_pitch_value, 'd': getattr(self, 'last_diatonic_index', None)})
        self.current_beats += b
        if p is not None: self.last_pitch_value = p
        if d_idx is not None: self.last_diatonic_index = d_idx
        self.text_edit.insertPlainText(txt)

    def recalculate_beats(self):
        text = self.text_edit.toPlainText()
        total = 0.0
        last_dur = '4'  
        
        ignore_count = 0 

        for token in text.split():
            match = re.search(r'^z?[a-gr][snf]*[=+-]*([02481369]d?)?', token)
            
            if not match: continue

            if ignore_count > 0:
                ignore_count -= 1
                continue
                
            if match.group(1): 
                last_dur = match.group(1)
            
            total += self.beat_values.get(last_dur, 0.0)
            
            tuplet_match = re.search(r'x(\d+)', token)
            if tuplet_match:
                tuplet_num = int(tuplet_match.group(1))
                if tuplet_num > 1:
                    ignore_count = tuplet_num - 1
                
        self.current_beats = total
        eighth_beats = self.current_beats * 2
        
        display_text = f"{self.current_beats:g} 박 <span style='color: #888888;'>({eighth_beats:g})</span>"
        
        if self.pending_duration:
            display_text += " (대기)"
            
        self.beat_label.setText(display_text)

    def apply_rhythm_overlay(self):
        cursor = self.text_edit.textCursor()
        has_selection = cursor.hasSelection()

        if has_selection:
            text = cursor.selectedText().replace('\u2029', '\n').strip()
        else:
            text = self.text_edit.toPlainText().strip()

        if not text: return

        rhythm_str, ok = QInputDialog.getText(self, "리듬 덮어쓰기", "리듬 템플릿을 입력하세요\n예: ( 4d / / z z / 8 )")
        if not ok or not rhythm_str.strip(): return

        original_tokens = text.split()
        rhythm_tokens = rhythm_str.split()
        
        is_target = re.compile(r'^[a-grz]')
        pitch_pattern = re.compile(r'^([a-grz][a-zA-Z\+\-\#\&\=]*)([\d\.x]+)?(.*)$')
        is_duration = re.compile(r'^(\d.*|\~|z|/)$')
        # [추가됨] r 또는 r+숫자 형태의 쉼표 삽입 패턴
        is_rest_insert = re.compile(r'^r([\d\.x]+)?$')

        targets = []
        non_targets = {}  
        
        for i, tok in enumerate(original_tokens):
            if is_target.match(tok):
                m = pitch_pattern.match(tok)
                if m:
                    targets.append({'idx': i, 'pitch': m.group(1), 'suffix': m.group(3)})
                else:
                    targets.append({'idx': i, 'pitch': tok, 'suffix': ''})
            else:
                if i not in non_targets: non_targets[i] = []
                non_targets[i].append(tok)

        durations = [rt for rt in rhythm_tokens if is_duration.match(rt)]
        
        if len(targets) != len(durations):
            msg = f"적용 대상의 타겟 개수({len(targets)}개)와 입력된 리듬 개수({len(durations)}개)가 다릅니다.\n매칭되는 부분까지만 적용하시겠습니까?"
            reply = QMessageBox.warning(self, "개수 불일치", msg, 
                                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.No: return

        new_tokens = []
        target_ptr = 0
        last_idx = -1
        last_duration = ""

        for rt in rhythm_tokens:
            if is_duration.match(rt):
                if target_ptr < len(targets):
                    t = targets[target_ptr]
                    
                    for idx in range(last_idx + 1, t['idx']):
                        if idx in non_targets:
                            new_tokens.extend(non_targets[idx])
                    
                    p = t['pitch']
                    s = t['suffix']

                    if rt == 'z':
                        if not p.startswith('z'): p = 'z' + p
                        new_tokens.append(f"{p}{s}")
                    elif rt == '~':
                        new_tokens.append(f"{p}{s}")
                    elif rt == '/':
                        new_tokens.append(f"{p}{last_duration}{s}")
                    # --- [추가/수정된 부분 시작] ---
                    elif '*' in rt:
                        # '8*4' 같은 문자열을 ['8', '4']로 분리
                        parts = rt.split('*')
                        
                        # 타이에 묶이는 두 번째 음부터는 임시표(+, -, #, =, & 등)를 제거
                        # p가 'f-' 라면 base_p는 'f'가 됨
                        base_p = re.sub(r'[\+\-\#\=\&]', '', p)
                        
                        tied_notes_list = []
                        for j, val in enumerate(parts):
                            if j == 0:
                                # 첫 번째 음은 임시표 포함
                                tied_notes_list.append(f"{p}{val}")
                            else:
                                # 두 번째 음부터는 임시표 제거
                                tied_notes_list.append(f"{base_p}{val}")
                                
                        tied_notes = " ".join(tied_notes_list)
                        new_tokens.append(f"{{ {tied_notes} }}{s}")
                        
                        last_duration = rt 
                    # --- [추가/수정된 부분 끝] ---
                    else:
                        new_tokens.append(f"{p}{rt}{s}")
                        last_duration = rt
                        
                    last_idx = t['idx']
                    target_ptr += 1

            elif is_rest_insert.match(rt):
                new_tokens.append(rt)  # 원본 타겟 음표를 소모하지 않고 바로 추가
                m = is_rest_insert.match(rt)
                if m and m.group(1): 
                    # r 뒤에 숫자가 붙어있다면 last_duration을 갱신 (예: r8 -> 이후 '/'가 8을 물려받음)
                    last_duration = m.group(1)
            else:
                new_tokens.append(rt)

        for idx in range(last_idx + 1, len(original_tokens)):
            new_tokens.append(original_tokens[idx])

        new_text = " ".join(new_tokens)

        if has_selection:
            cursor.insertText(new_text)
        else:
            self.text_edit.setPlainText(new_text)
            cursor = self.text_edit.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.text_edit.setTextCursor(cursor)

        final_text = self.text_edit.toPlainText()
        self.action_history.append({
            'type': 'overlay',
            'new_text': final_text,
            'p': getattr(self, 'last_pitch_value', None),
            'd': getattr(self, 'last_diatonic_index', None)
        })

        self.text_edit.clearFocus()
        self.setFocus()

    def draw_piano_keys(self):
        for child in self.piano_widget.children(): child.deleteLater()
        
        w_p = [('f',-1),('g',-1),('a',-1),('b',-1),('c',0),('d',0),('e',0),('f',0),('g',0),('a',0),('b',0),('c',1),('d',1),('e',1),('f',1),('g',1),('a',1)]
        b_p = [('fs',-1,0),('gs',-1,1),('as',-1,2),('cs',0,4),('ds',0,5),('fs',0,7),('gs',0,8),('as',0,9),('cs',1,11),('ds',1,12),('fs',1,14),('gs',1,15),('as',1,16)]
        
        kw, kh, bw, bh = 25, 90, 16, 55
        
        for i, (n, o) in enumerate(w_p):
            btn = QPushButton(self.piano_widget)
            btn.setGeometry(i*kw, 0, kw, kh)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            
            actual = o + self.octave_shift + 4
            if n == 'c': color = "#ffe082" if actual == 4 else "#f0f5f9"
            else: color = "white"        
                
            btn.setStyleSheet(f"background-color:{color};border:1px solid gray;")
            btn.clicked.connect(lambda ch, n=n, o=o: self.on_piano_clicked(n, o))
            btn.show()
            
        for n, o, idx in b_p:
            current_bw = bw // 2 if n == 'as' and o == 1 else bw
            btn = QPushButton(self.piano_widget)
            btn.setGeometry(idx*kw + (kw-bw//2), 0, current_bw, bh)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setStyleSheet("background-color:black;border:1px solid gray;")
            btn.clicked.connect(lambda ch, n=n, o=o: self.on_piano_clicked(n, o)); btn.show()

    def change_octave(self, d):
        self.octave_shift += d
        self.oct_label.setText(f"F{3+self.octave_shift} ~ A{5+self.octave_shift}")
        self.draw_piano_keys()

    def reset_modes(self, btn=None):
        self.mode_dotted.setChecked(False); self.mode_compound.setChecked(False); self.pending_duration = None

    def insert_rest(self): 
        v = self.dur_btn_group.checkedButton().property('mtx_val')
        self.process(f"r{v} ", self.beat_values.get(v, 0))

    def insert_dynamic(self): self.process(f"D{self.dyn_combo.currentText()} ", 0)
    def insert_ornament(self): self.process(f"{self.ornaments_data[self.orn_combo.currentIndex()][1]} ", 0)
    def insert_bar(self): self.process(f"{self.bar_combo.currentText()} ", 0)
    
    def insert_line_end(self): 
        c = self.line_combo.currentText()
        val = self.line_id_spin.value()
        i = str(val) if val > 0 else ""
        char = f" ){i} " if "슬러" in c or "점선" in c else (f" }}{i} " if "타이" in c else " ] ")
        self.process(char, 0)

    def backspace_text(self):
        if not self.action_history: return
        popped = self.action_history.pop()
        
        self.last_pitch_value = popped.get('p')
        self.last_diatonic_index = popped.get('d')
        
        result_text = ""
        for x in self.action_history:
            if x.get('type') == 'overlay':
                result_text = x.get('new_text', "")
            elif 't' in x:
                result_text += x['t']
                
        self.text_edit.setPlainText(result_text)
        
        cursor = self.text_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.text_edit.setTextCursor(cursor)
        
        self.text_edit.clearFocus()
        self.setFocus()

    def smart_send(self):
        txt = self.text_edit.toPlainText()
        if txt: 
            self.last_sent_text = txt
            QApplication.clipboard().setText(txt)
            QTimer.singleShot(100, self._execute_keys)

    def _execute_keys(self):
        sys_name = platform.system()
        if sys_name == 'Darwin':
            import subprocess
            subprocess.run(['osascript', '-e', 'tell application "Visual Studio Code" to activate'])
            pyautogui.sleep(0.3)
            subprocess.run(['osascript', '-e', 'tell application "System Events" to keystroke "v" using command down'])
        else:
            try:
                import pygetwindow as gw
                wins = gw.getWindowsWithTitle('Visual Studio Code')
                if wins: 
                    wins[0].activate()
                    pyautogui.sleep(0.2)
            except: 
                pyautogui.hotkey('alt', 'tab')
                pyautogui.sleep(0.2)
            pyautogui.hotkey('ctrl', 'v')
            
        self.text_edit.clear()
        self.action_history.clear()
        self.current_beats = 0
        self.text_edit.clearFocus()
        self.setFocus()

    def restore_last_sent(self):
        if hasattr(self, 'last_sent_text') and self.last_sent_text:
            current_text = self.text_edit.toPlainText()
            if not current_text.strip():
                new_text = self.last_sent_text
            else:
                new_text = current_text + " " + self.last_sent_text
                
            self.text_edit.setPlainText(new_text)
            
            cursor = self.text_edit.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.text_edit.setTextCursor(cursor)
            
            self.text_edit.clearFocus()
            self.setFocus()

    def smart_line_shortcut(self):
        cursor = self.text_edit.textCursor()
        if cursor.hasSelection():
            t = cursor.selectedText().replace('\u2029', '\n')
            c = self.line_combo.currentText()
            
            val = self.line_id_spin.value()
            i = str(val) if val > 0 else ""
            t = t.strip(' ')
            
            if "슬러" in c: s, e = (f" ({i} ", f" ){i} ")
            elif "타이" in c: s, e = (f" {{{i} ", f" }}{i} ")
            elif "빔" in c: s, e = (" [ ", " ] ")
            else: s, e = (f" (\"{i} ", f" ){i} ")
                
            cursor.insertText(f"{s}{t}{e}")
        else: 
            self.line_start_btn.click()

    def setup_shortcuts(self):
        shorts = {'0':'0','Shift+0':'0d','2':'2','Shift+2':'2d','4':'4','Shift+4':'4d','8':'8','Shift+8':'8d','1':'1','Shift+1':'1d','3':'3','Shift+3':'3d','6':'6','Shift+6':'6d', 'q': '4', 'w': '4d', 'e': '8'}
        for k, v in shorts.items(): QShortcut(QKeySequence(k), self).activated.connect(lambda val=v: self.select_duration(val))
        
        QShortcut(QKeySequence("["), self).activated.connect(self.smart_line_shortcut)
        QShortcut(QKeySequence("b"), self).activated.connect(self.smart_line_shortcut)
        QShortcut(QKeySequence("]"), self).activated.connect(self.line_end_btn.click)
        QShortcut(QKeySequence("v"), self).activated.connect(self.line_end_btn.click)
        
        QShortcut(QKeySequence("r"), self).activated.connect(self.insert_rest)
        QShortcut(QKeySequence("z"), self).activated.connect(self.chord_cb.toggle)
        QShortcut(QKeySequence("="), self).activated.connect(self.abs_pitch_btn.toggle)
        QShortcut(QKeySequence("x"), self).activated.connect(self.pattern_mode_btn.toggle)
        
        QShortcut(QKeySequence("s"), self).activated.connect(lambda: self.select_accidental("s"))
        QShortcut(QKeySequence("f"), self).activated.connect(lambda: self.select_accidental("f"))
        QShortcut(QKeySequence("n"), self).activated.connect(lambda: self.select_accidental("n"))

        QShortcut(QKeySequence("F1"), self).activated.connect(self.show_help_dialog)
        QShortcut(QKeySequence("Ctrl+Z"), self).activated.connect(self.backspace_text)
        # QShortcut(QKeySequence("Alt+Z"), self).activated.connect(self.reset_all)
        QShortcut(QKeySequence("Ctrl+Backspace"), self).activated.connect(self.reset_all)
        QShortcut(QKeySequence("Ctrl+Return"), self).activated.connect(self.smart_send)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self).activated.connect(self.restore_last_sent)
        QShortcut(QKeySequence("Alt+Z"), self).activated.connect(self.restore_last_sent)

        QShortcut(QKeySequence("Ctrl+/"), self).activated.connect(self.flip_last_enharmonic)

    def select_accidental(self, val):
        for btn in self.acc_btn_group.buttons():
            if btn.property("acc_val") == val:
                btn.setChecked(True)
                break

    def select_duration(self, val):
        for b in self.dur_btn_group.buttons():
            if b.property("mtx_val") == val: b.click(); break

    def flip_last_enharmonic(self):
        """마지막으로 입력된 음표(또는 버퍼에 담긴 화음)의 이명동음을 상호 변환합니다. (더블 샵/플랫 제외)"""
        flip_map = {
            # 기존 흑건 이명동음
            'cs': 'df', 'df': 'cs',
            'ds': 'ef', 'ef': 'ds',
            'fs': 'gf', 'gf': 'fs',
            'gs': 'af', 'af': 'gs',
            'as': 'bf', 'bf': 'as',
            
            # 추가된 백건 이명동음 (미-파, 시-도 구간)
            'es': 'f',  'f': 'es',
            'ff': 'e',  'e': 'ff',
            'bs': 'c',  'c': 'bs',
            'cf': 'b',  'b': 'cf'
        }
        
        # 핵심 1: \b 를 통해 반드시 음표의 시작 위치에서만 매칭되게 함 (dff4 내부의 ff 매칭 방지)
        # 핵심 2: 두 글자짜리(cs, ff 등)를 한 글자(f, e 등)보다 앞에 두어 최장 일치(Longest Match) 유도
        regex_pattern = r'\b(cs|df|ds|ef|fs|gf|gs|af|as|bf|es|ff|bs|cf|f|e|c|b)(?![sf])'
        
        # 1. 화음(z) 입력 모드인 경우
        if self.chord_cb.isChecked() and self.chord_buffer:
            last_note = self.chord_buffer[-1]
            new_name = re.sub(regex_pattern, lambda m: flip_map[m.group(1)], last_note['name'])
            
            if new_name != last_note['name']:
                self.chord_buffer[-1]['name'] = new_name
                chord_preview = " ".join([f"{n['name']}{n['mod']}" for n in self.chord_buffer])
                self.chord_cb.setText(f"화음: {chord_preview} (끄면 출력)")
            return

        # 2. 일반 입력 모드인 경우
        if not self.action_history: return
        last_action = self.action_history[-1]
        
        if last_action.get('type') == 'overlay': return 
        
        t = last_action.get('t', "")
        
        # 텍스트 치환
        new_t = re.sub(regex_pattern, lambda m: flip_map[m.group(1)], t)
        
        if new_t != t:
            self.action_history[-1]['t'] = new_t
            
            # 히스토리를 기반으로 텍스트 에디터 전체 재구성
            result_text = ""
            for x in self.action_history:
                if x.get('type') == 'overlay':
                    result_text = x.get('new_text', "")
                elif 't' in x:
                    result_text += x['t']
                    
            self.text_edit.setPlainText(result_text)
            
            # 커서를 다시 맨 끝으로
            cursor = self.text_edit.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.text_edit.setTextCursor(cursor)
            
            self.text_edit.clearFocus()
            self.setFocus()

    def toggle_always_on_top(self):
        win = self.window()
        flags = win.windowFlags()
        if self.top_hint_cb.isChecked(): flags |= Qt.WindowType.WindowStaysOnTopHint
        else: flags &= ~Qt.WindowType.WindowStaysOnTopHint
        win.setWindowFlags(flags); win.show()

    def toggle_grace_mode(self):
        if self.grace_btn.isChecked():
            self.grace_mode = True; self.grace_pitches = []
            idx = self.grace_combo.currentIndex()
            self.grace_target_count = 2 if idx == 2 else 1
            self.grace_btn.setText(f"입력 중 (0/{self.grace_target_count+1})")
            self.grace_btn.setStyleSheet("background-color: #ffcccc; font-weight: bold; color: black;")
        else: self.reset_grace_mode()

    def reset_grace_mode(self):
        self.grace_mode = False; self.grace_pitches = []
        self.grace_btn.setChecked(False); self.grace_btn.setText("🎵 꾸밈음 모드 시작"); self.grace_btn.setStyleSheet("")

    def reset_all(self):
        self.text_edit.clear()
        self.action_history.clear()
        
        self.octave_shift = 0
        self.current_beats = 0.0
        self.last_pitch_value = None 
        self.pending_duration = None
        self.tuplet_remaining = 0
        self.last_diatonic_index = None
        
        self.reset_grace_mode()
        
        self.mode_dotted.setChecked(False)      
        self.mode_compound.setChecked(False)    
        self.line_start_btn.setChecked(False)   
        self.tuplet_btn.setChecked(False)       
        self.chord_cb.setChecked(False)         
        self.pattern_mode_btn.setChecked(False) 
        self.continuous_cb.setChecked(False)    
        
        self.abs_pitch_btn.setChecked(True)     
        
        for btn in self.dur_btn_group.buttons():
            if btn.property("mtx_val") == '4':
                btn.setChecked(True)
                break

        self.text_edit.clearFocus()
        self.setFocus()

    def show_help_dialog(self):
        help_t = """
        <h3>🎹 M-Tx Visual Helper v2.2.3</h3>
        <hr>
        <p><b>[ 음표 및 길이 ]</b><br>
        &nbsp;&nbsp;• <b>0, 2, 4, 8, 1, 3, 6</b> (+Shift) 또는 <b>q, w, e</b> : 길이 선택<br>
        &nbsp;&nbsp;• <b>r</b> : 쉼표<br>
        &nbsp;&nbsp;• <b>z</b> : 화음 추가</p>

        <p><b>[ 임시표 및 기호 ]</b><br>
        &nbsp;&nbsp;• <b>s / f / n</b> : 일회성 샵 / 플랫 / 내추럴<br>
        &nbsp;&nbsp;• <b>Ctrl + /</b> : 이명동음 전환 (예: cs ↔ df)<br>  <!-- 이 줄 추가 -->
        &nbsp;&nbsp;• <b>=</b> : 강제 옥타브 표시<br>
        &nbsp;&nbsp;• <b>x</b> : 타이 패턴 토글</p>

        <p><b>[ 선 제어 ]</b><br>
        &nbsp;&nbsp;• <b>Alt + L</b> : 슬러(sLur) 선택<br>
        &nbsp;&nbsp;• <b>Alt + T</b> : 타이(Tie) 선택<br>
        &nbsp;&nbsp;• <b>[</b> / <b>]</b> (또는 <b>b</b> / <b>v</b>) : 선 시작 / 위치 종료</p>

        <p><b>[ 실행 및 취소 ]</b><br>
        &nbsp;&nbsp;• <b>Ctrl + Enter</b>: 전송<br>
        &nbsp;&nbsp;• <b>Ctrl + Z</b> : 마지막 입력 취소<br>
        &nbsp;&nbsp;• <b>Ctrl + Backspace</b> : 새로 시작 (초기화)<br>
        &nbsp;&nbsp;• <b>Alt + Z</b> : 마지막 전송 복구
        </p>
        """
        QMessageBox.information(self, "도움말", help_t)

class StickyMainWindow(QWidget):
    def __init__(self, initial_key=0):
        super().__init__()
        self.setWindowTitle("M-Tx Visual Helper v2.2.3")

        icon_path = self.resource_path("mtxvisualhelper.png")
        self.setWindowIcon(QIcon(icon_path))

        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowOpacity(0.96)
        layout = QVBoxLayout(); layout.setContentsMargins(0, 0, 0, 0)
        self.helper = MTxVisualInputWidget(initial_key); layout.addWidget(self.helper)
        self.setLayout(layout); self.setFixedWidth(450)

    def resource_path(self, relative_path):
        try:
            base_path = sys._MEIPASS
        except Exception:
            base_path = os.path.abspath(".")
        return os.path.join(base_path, relative_path)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--key', type=int, default=0, help='Initial key signature (-7 to +7)')
    args, unknown = parser.parse_known_args()

    app = QApplication(sys.argv); app.setStyle('Fusion')
    win = StickyMainWindow(initial_key=args.key); win.show()
    sys.exit(app.exec())