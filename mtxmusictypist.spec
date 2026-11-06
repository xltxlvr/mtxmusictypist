# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 빌드 명세.  사용:  pyinstaller --noconfirm mtxmusictypist.spec
#   macOS: dist/mtxMusicTypist.app    Windows/Linux: dist/mtxMusicTypist/ (onedir)
# 데이터 파일(패치된 .tex, 아이콘, 헬퍼)을 반드시 포함해야 LuaTeX+한글 컴파일이 동작한다.
import sys

datas = [
    ("musixtex_patched.tex", "."),
    ("pmx_patched.tex", "."),
    ("mtxvisualhelper.py", "."),          # 동적으로 import 되므로 파일도 함께 넣는다
    ("assets/icon.png", "assets"),
    ("assets/icon.ico", "assets"),
]
hiddenimports = [
    "mtxvisualhelper", "mtxheader", "mtxmap", "mtxpos", "mtxplayer", "mtxmidi", "mtxbeats", "mtxwidgets", "pmxpatch",
    "mido", "pygame", "pyautogui",
    "PyQt6.QtPdf", "PyQt6.QtMultimedia",
]

a = Analysis(
    ["mtxmusictypist.py"],
    pathex=["."],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
icon = "assets/icon.ico" if sys.platform == "win32" else ("assets/icon.png" if sys.platform != "darwin" else None)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="mtxMusicTypist",
    console=False,
    icon=icon,
)
coll = COLLECT(exe, a.binaries, a.datas, name="mtxMusicTypist")
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="mtxMusicTypist.app",
        icon="assets/icon.png",            # macOS 는 .icns 가 정석: iconutil 로 만들어 바꿔도 된다
        bundle_identifier="org.ktug.mtxmusictypist",
        info_plist={"CFBundleShortVersionString": "1.44", "NSHighResolutionCapable": True,
                    "CFBundleDocumentTypes": [{"CFBundleTypeName": "M-Tx source", "CFBundleTypeExtensions": ["mtx"],
                                               "CFBundleTypeRole": "Editor", "LSHandlerRank": "Alternate"}]},
    )
