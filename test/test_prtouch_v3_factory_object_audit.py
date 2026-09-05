import importlib.util
import pathlib
import unittest


SCRIPT = pathlib.Path(__file__).parents[1] / "scripts" / \
    "audit_prtouch_v3_factory_object_T113.py"
SPEC = importlib.util.spec_from_file_location("prtouch_object_audit", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


SYMBOLS = """
 1: 00000000 4900 OBJECT GLOBAL DEFAULT 10 pr_pres
 2: 00000000 540 OBJECT GLOBAL DEFAULT 11 pr_step
 3: 00000000 1944 OBJECT GLOBAL DEFAULT 12 pr_apax
 4: 00000001 320 FUNC GLOBAL DEFAULT 13 pres_get_datas
 5: 00000001 624 FUNC GLOBAL DEFAULT 14 pres_tri_check
 6: 00000001 160 FUNC GLOBAL DEFAULT 15 command_config_prtouch_pres
 7: 00000001 452 FUNC GLOBAL DEFAULT 16 prtouch_pres_task
 8: 00000000 0 NOTYPE GLOBAL DEFAULT UND gpio_adc_setup
 9: 00000000 0 NOTYPE GLOBAL DEFAULT UND gpio_adc_sample
10: 00000000 0 NOTYPE GLOBAL DEFAULT UND gpio_adc_read
"""

DISASSEMBLY = """
Disassembly of section .text.command_config_prtouch_pres:
ldrd\tr0, r3, [r4, #12]
cmp\tr0, r3
R_ARM_THM_CALL\tgpio_adc_setup
R_ARM_THM_CALL\tgpio_out_setup
R_ARM_THM_CALL\tgpio_in_setup
Disassembly of section .text.pres_get_datas:
mov.w\tr6, #502
R_ARM_THM_CALL\tgpio_adc_sample
R_ARM_THM_CALL\tgpio_adc_sample
R_ARM_THM_CALL\tgpio_adc_read
movs\tr5, #27
R_ARM_THM_CALL\tgpio_in_read
R_ARM_THM_CALL\tgpio_out_write
Disassembly of section .text.pres_tri_check:
negs\tr2, r2
adds\tr4, #240
ldr.w\tr3, [r5, #4]!
cmp\tr2, r3
Disassembly of section .text.prtouch_pres_task:
ldr\tr3, [r6, #4]
R_ARM_THM_CALL\tpres_get_datas
R_ARM_THM_CALL\tpres_csx_w_cfg
R_ARM_THM_CALL\tpres_csx_r_cfg
"""


class PRTouchV3FactoryObjectAuditTest(unittest.TestCase):
    def test_dual_path_fixture_passes(self):
        symbols = MODULE.verify(SYMBOLS, DISASSEMBLY)
        self.assertEqual(symbols["pr_pres"]["size"], 4900)

    def test_missing_adc_import_fails(self):
        with self.assertRaisesRegex(ValueError, "ADC 导入"):
            MODULE.verify(SYMBOLS.replace("gpio_adc_read", "missing_adc"),
                          DISASSEMBLY)

    def test_poll_limit_drift_fails(self):
        with self.assertRaisesRegex(ValueError, "#502"):
            MODULE.verify(SYMBOLS, DISASSEMBLY.replace("#502", "#501"))

    def test_layout_drift_fails(self):
        with self.assertRaisesRegex(ValueError, "pr_pres"):
            MODULE.verify(SYMBOLS.replace("4900 OBJECT", "4896 OBJECT"),
                          DISASSEMBLY)

    def test_falling_edge_history_must_keep_original_sign(self):
        changed = DISASSEMBLY.replace(
            "adds\tr4, #240\nldr.w\tr3, [r5, #4]!",
            "adds\tr4, #240\nnegs\tr2, r2\nldr.w\tr3, [r5, #4]!")
        with self.assertRaisesRegex(ValueError, "取负次数"):
            MODULE.verify(SYMBOLS, changed)

    def test_history_direct_load_is_required(self):
        changed = DISASSEMBLY.replace(
            "ldr.w\tr3, [r5, #4]!", "ldr.w\tr0, [r5, #4]!")
        with self.assertRaisesRegex(ValueError, "substring not found"):
            MODULE.verify(SYMBOLS, changed)


if __name__ == "__main__":
    unittest.main()
