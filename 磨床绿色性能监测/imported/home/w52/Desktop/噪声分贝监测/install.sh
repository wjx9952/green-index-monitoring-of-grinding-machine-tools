#!/bin/bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo "请使用 sudo ./install.sh"; exit 1; fi
SRC="$(cd "$(dirname "$0")" && pwd)"
INSTALL=/opt/noise-monitor
USER_NAME="${SUDO_USER:-pi}"
if grep -q "Raspberry Pi 5" /proc/device-tree/model 2>/dev/null; then
  DEVICE=/dev/ttyAMA4
  OVERLAY=uart4-pi5
else
  DEVICE=/dev/ttyAMA5
  OVERLAY=uart5
fi

install -d "$INSTALL" "$INSTALL/web" "$INSTALL/data"
install -m 755 "$SRC/app.py" "$INSTALL/app.py"
install -m 644 "$SRC/web/index.html" "$INSTALL/web/index.html"
chown -R "$USER_NAME:$USER_NAME" "$INSTALL/data"

# GPIO12/13 are UART4 on Pi 5 and UART5 on earlier Raspberry Pi models.
CONFIG=/boot/firmware/config.txt
[[ -f "$CONFIG" ]] || CONFIG=/boot/config.txt
if ! grep -Eq "^dtoverlay=${OVERLAY}([,[:space:]]|$)" "$CONFIG"; then
  printf '\n# HH_07.06 noise sensor: GPIO12 TX, GPIO13 RX\ndtoverlay=%s\n' "$OVERLAY" >> "$CONFIG"
fi

cat > /etc/systemd/system/noise-monitor.service <<EOF
[Unit]
Description=HH_07.06 Noise Monitor
After=network-online.target
[Service]
Type=simple
User=$USER_NAME
SupplementaryGroups=dialout
WorkingDirectory=$INSTALL
ExecStart=/usr/bin/python3 $INSTALL/app.py --device $DEVICE --baud 115200 --protocol auto --port 8088
Restart=always
RestartSec=2
[Install]
WantedBy=multi-user.target
EOF
usermod -aG dialout "$USER_NAME"
systemctl daemon-reload
systemctl enable noise-monitor.service
echo "安装完成。请执行 sudo reboot；重启后访问 http://$(hostname -I | awk '{print $1}'):8088"
