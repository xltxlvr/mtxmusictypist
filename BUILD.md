# 패키징 (PyInstaller)

```
pip install pyinstaller PyQt6 mido pygame
pyinstaller --noconfirm mtxmusictypist.spec
```

spec 파일이 아래 데이터 파일을 번들에 넣는다. **직접 만든 spec/명령을 쓴다면 반드시 포함:**

| 파일 | 용도 | 없으면 |
|---|---|---|
| `musixtex_patched.tex`, `pmx_patched.tex` | LuaTeX + 한글 컴파일 | 컴파일 로그에 ‘패치된 TeX 파일을 찾을 수 없음’ + 찾아 본 폴더 목록 |
| `mtxvisualhelper.py` | 음표 입력 도크 | 도크에 오류 표시 |
| `assets/icon.*` | 창 아이콘 | 기본 아이콘 |

`--add-data` 로 만들 때(구분자: macOS/Linux `:`, Windows `;`):
```
--add-data "musixtex_patched.tex:."  --add-data "pmx_patched.tex:."  --add-data "mtxvisualhelper.py:."  --add-data "assets:assets"
```

## 점검
도움말 ▸ **진단 정보**: 패치된 .tex 가 어디서 찾아졌는지(✔/✘), TeX 프로그램들이 PATH 에서 보이는지 표시한다.
LuaTeX+한글 컴파일 로그 맨 위에 `[패치된 TeX] musixtex.tex <- 경로` 줄이 나와야 한다.

## 알아 둘 것
- macOS .app 은 터미널과 달리 최소한의 PATH 로 실행된다. 앱이 `/Library/TeX/texbin` 등을 자동으로 더하지만,
  그래도 안 보이면 설정 ▸ **TeX 경로…** 에 bin 폴더를 지정한다.
- PyInstaller 로 만든 앱은 라이브러리 경로(LD_LIBRARY_PATH / DYLD_*)를 바꾼다. 앱이 외부 프로그램(luatex 등)을
  띄울 때는 원래 값으로 되돌려 준다(1.4 에서 추가).
