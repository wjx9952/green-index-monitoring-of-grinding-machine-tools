# 磨抛机床绿色性能指标统一监测系统

这是从树莓派 `192.168.3.202` 上现有程序整理出的单进程桌面软件，统一采集并展示：

- MLX90640 32×24 热成像（I²C）
- AIR-MOD-001 七合一空气质量（`/dev/ttyAMA1`，9600 8N1）
- HH_07.06 噪声分贝（`/dev/ttyAMA4`，默认 115200，自动识别协议）
- LoRa 广播（`/dev/ttyAMA0`，868 MHz、地址 0、22 dBm、2400 bps）

界面采用单页布局：上方左侧显示实时热成像，右侧显示最高、中心和最低温度；
下方左侧显示 AIR-MOD 全部七项指标，右侧显示噪声统计与趋势。
界面针对 `1024×600` 小屏优化并默认全屏；按 `F11` 切换全屏，按 `Esc`
退出全屏。热成像保持 MLX90640 原始 `4:3` 全画幅等比例显示，不裁切、不拉伸；
七项空气质量指标按 `4+3` 两行等宽铺满，不保留空卡位。

LoRa 每秒发送一个以 `GREEN1:` 开头的 JSON 数据包，包含时间戳、最高/最低/
中心温度、AIR-MOD 全部七项读数和当前噪声。

## 目录

- `app.py`：统一界面与采集调度
- `vendor/`：从原程序整理出的 AIR-MOD、噪声和 LoRa 驱动
- `data/noise/`：从树莓派复制的原始历史噪声 CSV
- `docs/AIR-MOD-001.pdf`：传感器协议资料
- `desktop/`：桌面快捷方式
- `systemd/`：可选服务模板
- `tests/`：无需硬件即可运行的协议测试
- `../imported/`：三套原程序的原样快照
- `../monitor-sources.tgz`：树莓派源码原始归档

## 在树莓派安装

将整个 `unified_monitor` 目录复制到树莓派后：

```bash
cd ~/unified_monitor
chmod +x install_on_pi.sh run.sh
./install_on_pi.sh
```

安装脚本会在确认后停用旧的 `noise-monitor.service`，因为两个进程不能同时占用
`/dev/ttyAMA4`。启动整合版前还应关闭当前单独运行的 AIR-MOD、热成像和噪声桌面
窗口，否则它们会继续占用 `/dev/ttyAMA1`、`/dev/ttyAMA0` 或 I²C。

直接运行：

```bash
~/unified_monitor/run.sh
```

不使用 LoRa：

```bash
~/unified_monitor/run.sh --no-lora
```

热成像默认由原来的 2 Hz 提高为 4 Hz。也可以指定刷新率：

```bash
~/unified_monitor/run.sh --thermal-rate 8
```

支持 `2`、`4`、`8` Hz。默认 I²C 速率下推荐使用稳定的 `4 Hz`；如果 `8 Hz`
频繁显示“热成像重试”，应退回 `4 Hz`，或提高树莓派 I²C 总线速率后再使用。

串口发生变化时可覆盖默认值：

```bash
~/unified_monitor/run.sh \
  --air-port /dev/ttyAMA1 \
  --noise-port /dev/ttyAMA4 \
  --lora-port /dev/ttyAMA0
```

## 测试

在项目目录中运行：

```bash
PYTHONPATH=. python3 -m unittest discover -s tests -v
python3 -m py_compile app.py vendor/*.py
```

协议测试不访问 GPIO、串口或 I²C。完整界面和硬件采集必须在树莓派上验证。

## 注意

- `w52` 用户需属于 `dialout`、`gpio`、`i2c` 和 `spi` 用户组。
- LoRa 驱动使用树莓派系统的 `python3-lgpio`；安装环境必须允许读取系统
  site-packages（安装脚本已经这样创建虚拟环境）。
- LoRa HAT 的 M0/M1 跳帽配置须与透明传输模式一致。
- 历史 CSV 会继续写入 `data/noise/YYYY-MM-DD.csv`。
- `systemd/` 中的服务模板仅作为无人值守方案；桌面环境优先使用 autostart。
