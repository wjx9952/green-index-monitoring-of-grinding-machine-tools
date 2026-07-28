# 磨床绿色性能指标 LoRa 接收端

本文件夹可直接复制到另一台树莓派。它对应发送端 `unified_monitor` 的
`GREEN1` 协议，接收并显示：

- MLX90640 最高、中心、最低温度
- AIR-MOD 的 CO₂、PM2.5、PM10、甲醛、VOC、温度、湿度
- 当前噪声

接收数据会自动保存到本文件夹的 `data/YYYY-MM-DD.csv`。

## 硬件

接收树莓派使用与发送端相同的 UART LoRa HAT：

- 频率：868 MHz
- 空中速率：2400 bps
- 串口：`/dev/ttyAMA0`，9600 baud
- 地址：0（接收广播）
- M0：GPIO 22，M1：GPIO 27

HAT 的 M0/M1 跳帽需拔除，由程序控制 GPIO。两端天线必须匹配 868 MHz，
并且先接好天线再上电。

## 首次安装

把整个文件夹复制到另一台树莓派（建议放在用户主目录），打开终端：

```bash
cd ~/lora_receiver
chmod +x install.sh run.sh
./install.sh
```

安装脚本会安装 Python、Tk、lgpio 和 pyserial，并在桌面生成启动图标。安装后
重启树莓派。若串口尚未启用，运行 `sudo raspi-config`，在
`Interface Options → Serial Port` 中关闭串口登录 shell、启用串口硬件。

## 启动与排查

双击桌面“磨床LoRa接收端”，或运行：

```bash
./run.sh
```

无硬件时测试界面：

```bash
./run.sh --demo --windowed
```

串口不是默认值时：

```bash
./run.sh --port /dev/ttyUSB0
```

如果显示“LoRa 配置无应答”，检查串口是否启用、HAT 型号与频段、M0/M1 跳帽、
GPIO 接线，以及是否有其他程序占用 `/dev/ttyAMA0`。

按 `Esc` 退出全屏，按 `F11` 切换全屏。
