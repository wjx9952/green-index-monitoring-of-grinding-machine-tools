import unittest

from vendor.airmod_protocol import AirReading, FrameParser, decode_frame
from vendor.noise_monitor import SerialMonitor, crc16


class AirModProtocolTests(unittest.TestCase):
    def test_valid_frame(self):
        payload = bytearray(
            [0x3C, 0x02, 0x01, 0xF4, 0x00, 0x12, 0x00, 0x34,
             0x00, 0x23, 0x00, 0x45, 0x19, 0x05, 0x32, 0x02]
        )
        payload.append(sum(payload) & 0xFF)
        self.assertEqual(
            decode_frame(bytes(payload)),
            AirReading(500, 18, 52, 35, 69, 25.5, 50.2),
        )

    def test_parser_recovers_after_junk(self):
        payload = bytearray(
            [0x3C, 0x02, 0, 1, 0, 2, 0, 3, 0, 4, 0, 5, 20, 0, 40, 0]
        )
        payload.append(sum(payload) & 0xFF)
        parser = FrameParser()
        self.assertEqual(parser.feed(b"junk" + bytes(payload))[0].co2, 1)


class NoiseProtocolTests(unittest.TestCase):
    def test_modbus_frame(self):
        monitor = SerialMonitor(None, "/dev/null", 115200, "auto", 1)
        body = bytes((1, 3, 2, 0x02, 0x8A))
        frame = body + crc16(body).to_bytes(2, "little")
        self.assertEqual(monitor.parse(bytearray(frame)), [(65.0, "modbus")])


if __name__ == "__main__":
    unittest.main()
