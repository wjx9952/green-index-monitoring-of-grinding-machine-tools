# AIR-MOD-001 空气质量监测

树莓派桌面程序，实时显示 CO2、甲醛、VOC、PM2.5、PM10、温度和湿度。

## 启动

```sh
./run.sh
```

程序会自动列出 `/dev/serial*`、`/dev/ttyUSB*`、`/dev/ttyACM*`、`/dev/ttyAMA*` 和 `/dev/ttyS*`，使用规格书规定的 `9600 8N1` 读取数据。

若使用树莓派 GPIO UART，请注意传感器 TX 为 5V TTL，不能直接接入树莓派 3.3V RX，必须使用电平转换器。连接方向为传感器 TX 到树莓派 RX、传感器 RX 到树莓派 TX，并共地。

安装桌面快捷方式：

```sh
cp AIR-MOD.desktop ~/Desktop/
chmod +x ~/Desktop/AIR-MOD.desktop
```
