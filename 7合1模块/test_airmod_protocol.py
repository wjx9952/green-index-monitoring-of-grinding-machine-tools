import unittest

from airmod_protocol import FrameParser, decode_frame


def frame(payload: list[int]) -> bytes:
    data = bytes([0x3C, 0x02, *payload])
    return data + bytes([sum(data) & 0xFF])


class ProtocolTest(unittest.TestCase):
    def test_specification_example(self):
        data = frame([0x03, 0x04, 0x00, 0x1B, 0x00, 0x2E, 0x00, 0x26, 0x00, 0x40, 0x1B, 0x05, 0x46, 0x01])
        reading = decode_frame(data)
        self.assertEqual(reading.co2, 772)
        self.assertEqual(reading.formaldehyde, 27)
        self.assertEqual(reading.voc, 46)
        self.assertEqual(reading.pm25, 38)
        self.assertEqual(reading.pm10, 64)
        self.assertEqual(reading.temperature, 27.5)
        self.assertEqual(reading.humidity, 70.1)

    def test_negative_temperature_and_stream_recovery(self):
        data = frame([0, 1, 0, 2, 0, 3, 0, 4, 0, 5, 0x85, 4, 60, 8])
        parser = FrameParser()
        self.assertEqual(parser.feed(b"noise" + data[:8]), [])
        readings = parser.feed(data[8:])
        self.assertEqual(readings[0].temperature, -5.4)

    def test_checksum_rejected(self):
        data = bytearray(frame([0] * 14))
        data[-1] ^= 1
        with self.assertRaises(ValueError):
            decode_frame(bytes(data))


if __name__ == "__main__":
    unittest.main()
