import unittest

from codex_flipper.raspberry_pi.protocol import (
    DisplayState,
    MAX_WIRE_BYTES,
    decode,
    encode,
    remaining,
)


class ProtocolTest(unittest.TestCase):
    def test_round_trip_and_size(self):
        raw = encode({"op": "approval", "summary": "编译 " * 200})
        self.assertLessEqual(len(raw), MAX_WIRE_BYTES)
        self.assertEqual(decode(raw)["op"], "approval")

    def test_remaining(self):
        self.assertEqual(remaining({"usedPercent": 17}), 83)
        self.assertEqual(remaining({"usedPercent": 150}), 0)
        self.assertIsNone(remaining(None))

    def test_state(self):
        self.assertEqual(decode(DisplayState(status="idle").wire())["status"], "idle")


if __name__ == "__main__":
    unittest.main()
