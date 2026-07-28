import time

try:
    import lgpio
except ImportError:
    lgpio = None

try:
    import serial
except ImportError:
    serial = None


class LoRaHat:
    M0 = 22
    M1 = 27

    def __init__(
        self,
        port="/dev/ttyAMA0",
        baudrate=9600,
        frequency=868,
        address=0,
        power=22,
        air_speed=2400,
    ):
        if lgpio is None:
            raise RuntimeError("未安装 python3-lgpio")
        if serial is None:
            raise RuntimeError("未安装 pyserial")
        if not 850 <= frequency <= 930:
            raise ValueError("当前模块频率必须在 850-930 MHz")

        self.frequency = frequency
        self.channel = frequency - 850
        self.address = address
        self.serial = None
        self.gpio_handle = None

        try:
            self.gpio_handle = lgpio.gpiochip_open(0)
            lgpio.gpio_claim_output(self.gpio_handle, self.M0, 0)
            lgpio.gpio_claim_output(self.gpio_handle, self.M1, 1)
            self.serial = serial.Serial(
                port=port,
                baudrate=baudrate,
                timeout=0.3,
                write_timeout=1,
                exclusive=True,
            )
            self.serial.reset_input_buffer()
            self._configure(power, air_speed)
        except Exception:
            self.close()
            raise

    def _set_mode(self, m0, m1):
        if self.gpio_handle is None:
            raise RuntimeError("LoRa GPIO 尚未初始化")
        lgpio.gpio_write(self.gpio_handle, self.M0, m0)
        lgpio.gpio_write(self.gpio_handle, self.M1, m1)

    def _configure(self, power, air_speed):
        air_speed_values = {
            1200: 0x01,
            2400: 0x02,
            4800: 0x03,
            9600: 0x04,
            19200: 0x05,
            38400: 0x06,
            62500: 0x07,
        }
        power_values = {22: 0x00, 17: 0x01, 13: 0x02, 10: 0x03}
        if air_speed not in air_speed_values:
            raise ValueError("不支持的 LoRa 空中速率")
        if power not in power_values:
            raise ValueError("不支持的 LoRa 发射功率")

        self._set_mode(0, 1)
        time.sleep(0.1)

        config = bytes([
            0xC2,
            0x00,
            0x09,
            (self.address >> 8) & 0xFF,
            self.address & 0xFF,
            0x00,
            0x60 + air_speed_values[air_speed],
            power_values[power] + 0x20,
            self.channel,
            0x43,
            0x00,
            0x00,
        ])

        response = b""
        for _ in range(2):
            self.serial.reset_input_buffer()
            self.serial.write(config)
            self.serial.flush()
            time.sleep(0.3)
            response = self.serial.read(self.serial.in_waiting or 12)
            if response[:1] == b"\xC1":
                break

        self._set_mode(0, 0)
        time.sleep(0.1)

        if response[:1] != b"\xC1":
            raise RuntimeError(
                "LoRa 参数配置无应答；请确认用 sudo 运行、串口已启用且 HAT 的 M0/M1 跳帽已拔除"
            )

    def send(self, payload, destination=65535, frequency=None):
        target_frequency = self.frequency if frequency is None else frequency
        target_channel = target_frequency - 850
        header = bytes([
            (destination >> 8) & 0xFF,
            destination & 0xFF,
            target_channel,
            (self.address >> 8) & 0xFF,
            self.address & 0xFF,
            self.channel,
        ])
        data = header + payload

        self._set_mode(0, 0)
        time.sleep(0.1)
        written = self.serial.write(data)
        self.serial.flush()
        time.sleep(0.1)
        if written != len(data):
            raise RuntimeError(f"LoRa 串口只写入 {written}/{len(data)} 字节")
        return written

    def close(self):
        if self.serial is not None:
            try:
                self.serial.close()
            finally:
                self.serial = None
        if lgpio is not None and self.gpio_handle is not None:
            try:
                for pin in (self.M0, self.M1):
                    try:
                        lgpio.gpio_free(self.gpio_handle, pin)
                    except Exception:
                        pass
                lgpio.gpiochip_close(self.gpio_handle)
            finally:
                self.gpio_handle = None
