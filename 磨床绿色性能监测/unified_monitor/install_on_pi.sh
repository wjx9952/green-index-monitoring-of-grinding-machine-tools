#!/bin/sh
set -eu

APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TARGET=/home/w52/unified_monitor

if [ "$(id -u)" -eq 0 ]; then
    echo "请使用 w52 用户运行此脚本，不要直接使用 root。" >&2
    exit 1
fi

echo "安装目录：$TARGET"
echo "安装时会停用旧 noise-monitor.service，避免 /dev/ttyAMA4 被两个程序同时占用。"
printf "继续安装？[y/N] "
read answer
case "$answer" in y|Y|yes|YES) ;; *) exit 0 ;; esac

mkdir -p "$TARGET"
cp -a "$APP_DIR/." "$TARGET/"
python3 -m venv --system-site-packages "$TARGET/.venv"
"$TARGET/.venv/bin/pip" install -r "$TARGET/requirements.txt"
chmod +x "$TARGET/run.sh"

sudo systemctl disable --now noise-monitor.service 2>/dev/null || true
mkdir -p /home/w52/.config/autostart
cp "$TARGET/desktop/磨抛机床绿色性能统一监测.desktop" \
   /home/w52/.config/autostart/磨抛机床绿色性能统一监测.desktop
cp "$TARGET/desktop/磨抛机床绿色性能统一监测.desktop" /home/w52/Desktop/

echo "安装完成。请关闭旧的热成像、AIR-MOD 和噪声窗口，然后运行："
echo "$TARGET/run.sh"
