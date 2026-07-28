# HH_07.06 噪声分贝监测

适用于树莓派 GPIO12/13 上的 HH_07.06 TTL 噪声模块。网页显示实时分贝、最低/平均/最高值和趋势图，数据按天保存为 `data/YYYY-MM-DD.csv`。软件自动识别模块的主动、被动和 Modbus RTU 协议。

## 接线

> 一定要交叉接 TX/RX，并共地。

| HH_07.06 | 树莓派 |
|---|---|
| VCC（5–24V） | 5V（物理针 2 或 4） |
| GND | GND（如物理针 6） |
| TX | GPIO13 / RXD5（物理针 33） |
| RX | GPIO12 / TXD5（物理针 32） |

模块串口电平是 3.3V TTL，不要接 RS-232 或 RS-485 接口。

## 安装

```bash
cd ~/噪声分贝监测
chmod +x install.sh
sudo ./install.sh
sudo reboot
```

重启后访问 `http://192.168.3.202:8088`。检查状态：

```bash
systemctl status noise-monitor
journalctl -u noise-monitor -f
```

树莓派 5 上 GPIO12/13 是 `/dev/ttyAMA4`，早期型号上是 `/dev/ttyAMA5`，安装脚本会自动选择。默认为 115200 baud、地址 1。如模块曾被改为其他波特率或地址，在 `/etc/systemd/system/noise-monitor.service` 的 `ExecStart` 中修改 `--baud` 或添加 `--address N`，然后执行 `sudo systemctl daemon-reload && sudo systemctl restart noise-monitor`。
