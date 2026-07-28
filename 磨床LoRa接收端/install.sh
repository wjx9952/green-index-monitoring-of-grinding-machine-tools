#!/bin/sh
set -eu

APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if [ "$(id -u)" -eq 0 ]; then
    echo "请用树莓派的普通桌面用户运行，不要直接用 root。" >&2
    exit 1
fi

echo "正在安装系统组件（会要求输入 sudo 密码）…"
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-tk python3-lgpio

python3 -m venv --system-site-packages "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
chmod +x "$APP_DIR/run.sh" "$APP_DIR/install.sh"

mkdir -p "$HOME/Desktop"
DESKTOP_FILE="$HOME/Desktop/磨床LoRa接收端.desktop"
sed "s|__APP_DIR__|$APP_DIR|g" "$APP_DIR/磨床LoRa接收端.desktop.in" > "$DESKTOP_FILE"
chmod +x "$DESKTOP_FILE"

if command -v raspi-config >/dev/null 2>&1; then
    echo
    echo "如尚未启用串口，请运行 sudo raspi-config："
    echo "Interface Options → Serial Port → 登录 shell 选 No → 串口硬件选 Yes。"
fi

echo
echo "安装完成。重启树莓派后，双击桌面的“磨床LoRa接收端”即可运行。"
echo "也可直接运行：$APP_DIR/run.sh"
