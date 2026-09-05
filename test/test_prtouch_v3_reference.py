import importlib.util
import pathlib
import unittest


SCRIPT = pathlib.Path(__file__).parents[1] / "scripts" / \
    "prtouch_v3_reference.py"
SPEC = importlib.util.spec_from_file_location("prtouch_v3_reference", SCRIPT)
MODEL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODEL)


class PRTouchV3ReferenceTest(unittest.TestCase):
    def test_nearest_delta_sample(self):
        self.assertEqual(MODEL.nearest_delta_sample(100, [160, 103, 80]), 103)

    def test_nearest_delta_uses_factory_int32_wrap(self):
        self.assertEqual(
            MODEL.nearest_delta_sample(0, [0x01000000, -0x80000000]),
            -0x80000000)

    def test_low_pass_uses_thousandths(self):
        self.assertEqual(MODEL.low_pass(1000, 2000, 250), 1250)

    def test_high_pass_uses_post_shift_slot_15(self):
        history = list(range(21))
        updated, accumulator = MODEL.high_pass_step(100, history, 500)
        self.assertEqual(accumulator, 516)
        self.assertEqual(updated[:16], list(range(1, 17)))
        self.assertEqual(updated[16], 17)
        self.assertEqual(updated[-1], 100)

    def test_factory_zip_vectors(self):
        self.assertEqual(MODEL.zip_data([]), b"\x00\x00")
        self.assertEqual(MODEL.zip_data([1]), b"\x01\x00\x01")
        self.assertEqual(MODEL.zip_data([1, 3]), b"\x02\x00\x01\x02")
        self.assertEqual(MODEL.zip_data([-1, 1]), b"\x02\x00\xff\x02")
        self.assertEqual(
            MODEL.zip_data([1, 2, 3, 4, 5]),
            b"\x05\x00\x00\x01\x01\x01\x01\x01")
        self.assertEqual(MODEL.zip_data([1, -0x80000000, 3]),
                         b"\x02\x00\x01\x02")
        self.assertEqual(MODEL.zip_data([1, 2, 3, 4]),
                         b"\x04\x00\x01\x01\x01\x01\x00")

    def test_adc_hold_trigger(self):
        values = [1, 99, 101, 102, 103]
        self.assertTrue(MODEL.adc_hold_trigger(values, 3, 100, 64, 64))
        self.assertFalse(MODEL.adc_hold_trigger(values, 4, 100, 64, 64))
        self.assertFalse(MODEL.adc_hold_trigger(values, 3, 100, 63, 64))
        self.assertTrue(MODEL.adc_hold_trigger(values, 0, 100, 64, 64))

    def test_read_response_count_factory_loop_quirk(self):
        self.assertEqual(MODEL.read_response_count(0, False), 1)
        self.assertEqual(MODEL.read_response_count(5, False), 6)
        self.assertEqual(MODEL.read_response_count(5, True), 5)

    def test_step_periodic_and_forced_final_capture(self):
        self.assertEqual(MODEL.step_capture_decision(True, None, 1),
                         (True, False, False))
        self.assertEqual(MODEL.step_capture_decision(True, None, 0),
                         (True, True, False))
        self.assertEqual(MODEL.step_capture_decision(False, 1, 1),
                         (False, False, True))
        self.assertEqual(MODEL.step_capture_decision(False, 0, 0),
                         (True, True, True))

    def test_digital_three_level_and_history_guard(self):
        values = [0] * 61 + [3500, 3500, 4000]
        self.assertTrue(MODEL.cs1237_trigger_reference(
            values, min_hold=3000, add_hold=500))
        values[10] = 3600
        self.assertFalse(MODEL.cs1237_trigger_reference(
            values, min_hold=3000, add_hold=500))

    def test_digital_direction_normalization(self):
        values = [0] * 61 + [-3500, -3500, -4000]
        values[0] = 100
        # Original firmware does not negate historical samples on a falling
        # edge.  A previous negative excursion therefore remains below the
        # normalized positive threshold and must not veto the trigger.
        values[10] = -4000
        self.assertTrue(MODEL.cs1237_trigger_reference(
            values, min_hold=3000, add_hold=500))

    def test_digital_edge_first_point_is_not_rewritten(self):
        # 原实现曾误用 filtered[58] 重写 edge[0]；原厂只重写 edge[1]/edge[2]。
        values = [0] * 59 + [3000, 100, 3100, 3500, 4000]
        self.assertTrue(MODEL.cs1237_trigger_reference(
            values, min_hold=3000, add_hold=500))


if __name__ == "__main__":
    unittest.main()
