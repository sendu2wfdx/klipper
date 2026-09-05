import pathlib
import struct
import sys
import unittest


if sys.platform == "win32":
    raise unittest.SkipTest("Klipper mcu.py requires POSIX termios")

KLIPPY = pathlib.Path(__file__).parents[1] / "klippy"
sys.path.insert(0, str(KLIPPY))
import mcu


class FakeMCU:
    def clock32_to_clock64(self, value):
        return value

    def clock_to_print_time(self, value):
        return value / 100.


class MCUADCBatchRuntimeTest(unittest.TestCase):
    def test_all_batched_u16_samples_are_delivered(self):
        adc = mcu.MCU_adc.__new__(mcu.MCU_adc)
        adc._unpack_from = struct.Struct("<H").unpack_from
        adc._report_clock = 10
        adc._inv_max_adc = .001
        adc._last_state = None
        delivered = []
        adc._callback = delivered.extend
        adc._mcu = FakeMCU()
        adc._handle_analog_in_state({
            "next_clock": 100,
            "values": struct.pack("<HHH", 100, 200, 300),
        })
        self.assertEqual(delivered, [
            (.7, .1), (.8, .2), (.9, .3),
        ])
        self.assertEqual(adc._last_state, (.9, .3))


if __name__ == "__main__":
    unittest.main()
