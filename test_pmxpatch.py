#!/usr/bin/env python3
"""pmxpatch 회귀 테스트 (사용자 제공 adagio_pmx.patch 포함)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pmxpatch as P

here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples")
orig = open(f"{here}/adagio.pmx", encoding="utf-8").read()
patch = open(f"{here}/adagio_pmx.patch", encoding="utf-8").read()
bad = 0
def check(name, cond, extra=""):
    global bad
    bad += not cond
    print(("PASS " if cond else "FAIL ") + name + (" " + extra if extra else ""))

# 1) 사용자 패치(일반 diff)가 6줄 밀린 pmx 에도 적용된다
r = P.apply_patch(orig, patch)
check("normal diff 적용", r.ok and r.total == 4, f"{r.applied}/{r.total}")
check("패치 결과에 G2m2Wf 이동 반영", "oT5 G2m2Wf g /" in r.text and "\nG2m2Wf g (S f8" not in r.text)
# 2) 생성 -> 다시 적용 왕복
uni = P.make_patch(orig, r.text)
r2 = P.apply_patch(orig, uni)
check("unified 왕복", r2.ok and r2.text == r.text, f"{r2.applied}/{r2.total}")
# 3) 줄이 더 밀려도 (앞에 줄 추가) 적용
shifted = "\\input mtxpos\n% x\n% y\n" + orig
r3 = P.apply_patch(shifted, uni)
check("줄 번호 밀림 허용", r3.ok and "oT5 G2m2Wf g /" in r3.text)
r3b = P.apply_patch(shifted, patch)
check("normal diff 도 밀림 허용", r3b.ok)
# 4) 대상 줄이 바뀌면 그 항목만 건너뛰고 마디 번호와 함께 보고
broken = orig.replace("Gsm2g f4d Dp g4fd oT5 /", "Gsm2g f4d Dp g4fd oT5 X /")
r4 = P.apply_patch(broken, patch)
check("일부 실패 보고", r4.applied == 3 and any("마디" in m for m in r4.messages), "; ".join(r4.messages)[:100])
# 5) 순수 삽입 / 삭제 왕복
edited = orig.replace("%Bar 55\n", "%Bar 55\nXX inserted /\n").replace("% Paragraph 44", "% Paragraph 44X", 0)
lines = edited.split("\n"); lines.pop(lines.index("% Coded by M-Tx")) if "% Coded by M-Tx" in lines else None
edited = "\n".join(lines)
u = P.make_patch(orig, edited); r5 = P.apply_patch(orig, u)
check("삽입/삭제 왕복", r5.text == edited, f"{r5.applied}/{r5.total}")
top = "AAA\n" + orig
u6 = P.make_patch(orig, top)
check("맨 앞 삽입 왕복", P.apply_patch(orig, u6).text == top)
check("변경 없음 -> 빈 패치", P.make_patch(orig, orig) == "")
# 6) 마디 -> pmx 줄
ln = P.find_bar_line(orig, 55)
check("find_bar_line", ln is not None and orig.split("\n")[ln].startswith("%Bar 55"), str(ln))
sys.exit(1 if bad else 0)
