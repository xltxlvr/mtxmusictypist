#!/usr/bin/env python3
"""mtxMusicTypist 의 QSettings 를 초기화한다 (매뉴얼용 스크린샷을 처음 설치한 상태로 찍기 위해).

  python reset_settings.py            # 저장된 설정을 보여 주고, 확인 후 삭제
  python reset_settings.py -y         # 확인 없이 삭제
  python reset_settings.py --show     # 보기만 (삭제 안 함)
  python reset_settings.py --all      # 내장 Visual Helper(MTxVisualHelper) 설정까지 삭제

앱이 켜져 있으면 종료할 때 설정을 다시 쓰므로, 반드시 앱을 끈 상태에서 실행한다.
저장 위치: Windows=레지스트리 HKCU\\Software\\ktug.org\\MTxMusicTypist,
          macOS=~/Library/Preferences/org.ktug.MTxMusicTypist.plist, Linux=~/.config/ktug.org/MTxMusicTypist.conf
"""
import argparse
import sys

from PyQt6.QtCore import QSettings

ORG = "ktug.org"
TARGETS = {"MTxMusicTypist": "mtxMusicTypist", "MTxVisualHelper": "Visual Helper(내장)"}


def show(s: QSettings, label: str) -> int:
    keys = sorted(s.allKeys())
    print(f"[{label}] {s.fileName()}  — {len(keys)}개 항목")
    for k in keys:
        v = str(s.value(k)).replace("\n", "\\n")
        print(f"   {k} = {v[:70]}{'…' if len(v) > 70 else ''}")
    return len(keys)


def main() -> int:
    ap = argparse.ArgumentParser(description="mtxMusicTypist 설정 초기화")
    ap.add_argument("-y", "--yes", action="store_true", help="확인 없이 삭제")
    ap.add_argument("--show", action="store_true", help="저장된 설정만 보여 준다")
    ap.add_argument("--all", action="store_true", help="Visual Helper 설정도 함께 삭제")
    a = ap.parse_args()

    names = list(TARGETS) if a.all else ["MTxMusicTypist"]
    sets = [(n, QSettings(ORG, n)) for n in names]
    total = sum(show(s, TARGETS[n]) for n, s in sets)
    if a.show:
        return 0
    if total == 0:
        print("지울 설정이 없습니다 (이미 초기 상태).")
        return 0
    if not a.yes and input("\n위 설정을 모두 삭제할까요? 앱은 꺼져 있어야 합니다. [y/N] ").strip().lower() != "y":
        print("취소했습니다.")
        return 1
    for n, s in sets:
        s.clear(); s.sync()
    print("삭제했습니다. 이제 앱을 켜면 처음 설치한 상태로 시작합니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
