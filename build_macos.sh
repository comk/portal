#!/bin/bash
# macOS 打包脚本
# 使用方法: chmod +x build_macos.sh && ./build_macos.sh

set -e

echo "=== 检查 py2app ==="
if ! python3 -c "import py2app" 2>/dev/null; then
    echo "正在安装 py2app ..."
    pip3 install py2app
fi

echo "=== 清理旧构建 ==="
rm -rf build dist

echo "=== 开始打包 ==="
python3 setup.py py2app

echo "=== 打包完成 ==="
echo "应用位置: $(pwd)/dist/ChallengeApp.app"
echo "双击即可运行，或执行: open dist/ChallengeApp.app"
