import unittest

from lora_radio import LoRaReceiver


class PacketParserTests(unittest.TestCase):
    def test_split_packet_and_source_header(self):
        first, rest = LoRaReceiver.extract_packets(b"\x00\x00\x12GRE")
        self.assertEqual(first, [])
        packets, rest = LoRaReceiver.extract_packets(
            rest + b'EN1:{"v":1,"ts":123,"noise_db":66.5}\n'
        )
        self.assertEqual(len(packets), 1)
        self.assertEqual(packets[0]["noise_db"], 66.5)
        self.assertEqual(rest, b"")

    def test_ignores_bad_packet_then_recovers(self):
        packets, rest = LoRaReceiver.extract_packets(
            b"GREEN1:not-json\nGREEN1:{\"v\":1,\"ts\":9}\n"
        )
        self.assertEqual([packet["ts"] for packet in packets], [9])
        self.assertEqual(rest, b"")


if __name__ == "__main__":
    unittest.main()
