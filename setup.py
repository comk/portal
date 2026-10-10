# -*- coding: utf-8 -*-
"""py2app 打包脚本，用于将 challenge_app.py 打包为 macOS 应用。

使用方法（在 macOS 上执行）：
    pip install py2app
    python setup.py py2app

生成的应用在 dist/ChallengeApp.app
"""

from setuptools import setup

APP = ["challenge_app.py"]
OPTIONS = {
    "argv_emulation": False,
    "iconfile": "",           # 如有 .icns 图标文件，填写路径
    "plist": {
        "CFBundleName": "挑战答题",
        "CFBundleDisplayName": "挑战答题",
        "CFBundleIdentifier": "com.challenge.app",
        "CFBundleVersion": "1.0.0",
        "CFBundleShortVersionString": "1.0.0",
        "NSHumanReadableCopyright": "2026 Challenge App",
        "LSRequiresCarbon": False,
        "NSHighResolutionCapable": True,
    },
    "packages": [],
    "includes": ["wx", "win32com.client"],  # win32com 仅 Windows 用，macOS 会自动忽略
    "excludes": [
        "tkinter",
        "pytest",
        "unittest",
        "pydoc",
        "doctest",
        "argparse",
    ],
}

setup(
    name="ChallengeApp",
    app=APP,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
