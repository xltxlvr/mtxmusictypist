#!/usr/bin/env python3
"""슬러·타이 짝 찾기 회귀 테스트. 실행: python3 test_pair.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mtxpair

ex = """f8 {1 b- { b2 }1
L: 이 로
b8 d f4 { b- // rb4 r8+6 b+8 d ( zf e zg

b2 } a8+ a
L: 맑 은
b8 } d f4 b- // f2 za ) c8- c
"""
p = mtxpair.find_pairs(ex)
pairs = sorted((p.brackets[i].line + 1, p.brackets[j].line + 1, p.brackets[i].char)
               for i, b in enumerate(p.brackets) if b.is_open and (j := b.partner) >= 0)
ok = pairs == [(1, 1, "{"), (1, 5, "{"), (3, 7, "("), (3, 7, "{")] and not p.unmatched()
ok2 = [mtxpair.parse_id(s) for s in ("S", "1", "+1", "S+1", "u1", "l", "u+2", '"')] == ["S", "1", "", "S", "1", "", "", ""]
q = mtxpair.find_pairs("a ( b\n")
ok3 = len(q.unmatched()) == 1
print("PASS" if ok else "FAIL", "pairs", pairs)
print("PASS" if ok2 else "FAIL", "parse_id")
print("PASS" if ok3 else "FAIL", "unmatched")
sys.exit(0 if ok and ok2 and ok3 else 1)
