#!/usr/bin/env python3
"""회귀 테스트: 샘플 3종의 마디 대응과 헤더 왕복. 실행: python3 test_mapping.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mtxmap, mtxheader

here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples")
expect = {"hibike_euphoniam": 26, "adagio": 60, "lullaby_05": 24, "test_typist_2": 5, "hangul_norae": 17}
# test_typist_2 는 컴파일용 사본에 mtxpos 줄이 한 줄 끼워진 pmx (못갖춘마디 포함)
line_offset = {"test_typist_2": 1, "hangul_norae": 1}
bad = 0
for name, nbars in expect.items():
    t = open(f"{here}/{name}.mtx", encoding="utf-8").read().replace("\r\n", "\n")
    p = open(f"{here}/{name}.pmx", encoding="utf-8").read()
    bm = mtxmap.build_barmap(mtxmap.qt_safe(t), p, line_offset.get(name, 0))
    exact = all(b.exact for b in bm.bars.values())
    st, _ = mtxheader.parse_header(t)
    rt = mtxheader.apply_state(t, st) == t or mtxheader.split_header(mtxheader.apply_state(t, st))[0] == mtxheader.split_header(t)[0]
    ok = len(bm.bars) == nbars and exact and not bm.warnings and rt
    bad += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {name:18} bars={len(bm.bars)}/{nbars} exact={exact} header_roundtrip={rt}")
try:
    import mtxplayer
    for name, nb in expect.items():
        pmx = open(f"{here}/{name}.pmx", encoding="utf-8").read()
        info = mtxplayer.analyze(f"{here}/{name}.mid", nb, mtxplayer.parse_pickup_beats(pmx))
        ok = info.bars_ok
        bad += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:18} midi bar grid ok={ok} {info.message}")
except ImportError as e:
    print("(MIDI 테스트 건너뜀:", e, ")")
sys.exit(1 if bad else 0)
