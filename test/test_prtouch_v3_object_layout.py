import importlib.util
import pathlib
import unittest


SCRIPT = pathlib.Path(__file__).parents[1] / "scripts" / \
    "check_prtouch_v3_object_layout.py"
SPEC = importlib.util.spec_from_file_location("prtouch_layout", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PRTouchV3ObjectLayoutTest(unittest.TestCase):
    def test_factory_shape_is_accepted(self):
        output = """
00000000 000000a0 b zip_data.5581
00000000 000000a0 b zip_data.5707
00000000 000000a0 b zip_tick.5580
00000000 000000a0 b zip_tick.5706
00000000 0000021c B pr_step
00000000 00000798 B pr_apax
00000000 00001324 B pr_pres
00000000 00000010 T command_config_prtouch_apax
00000000 00000012 T prtouch_task
00000000 00000038 T command_stop_prtouch_step
00000000 00000040 T command_stop_prtouch_pres
00000000 00000074 T prtouch_step_task
"""
        workspaces = MODULE.verify(MODULE.parse_nm(output), "fixture")
        self.assertEqual(len(workspaces), 4)

    def test_layout_drift_is_rejected(self):
        output = """
00000000 000000a0 b zip_data.1
00000000 000000a0 b zip_data.2
00000000 000000a0 b zip_tick.1
00000000 000000a0 b zip_tick.2
00000000 0000021c B pr_step
00000000 00000798 B pr_apax
00000000 00001325 B pr_pres
00000000 00000010 T command_config_prtouch_apax
00000000 00000012 T prtouch_task
00000000 00000038 T command_stop_prtouch_step
00000000 00000040 T command_stop_prtouch_pres
00000000 00000074 T prtouch_step_task
"""
        with self.assertRaisesRegex(ValueError, "pr_pres"):
            MODULE.verify(MODULE.parse_nm(output), "fixture")

    def test_step_control_flow_size_drift_is_rejected(self):
        output = """
00000000 000000a0 b zip_data.1
00000000 000000a0 b zip_data.2
00000000 000000a0 b zip_tick.1
00000000 000000a0 b zip_tick.2
00000000 0000021c B pr_step
00000000 00000798 B pr_apax
00000000 00001324 B pr_pres
00000000 00000010 T command_config_prtouch_apax
00000000 00000012 T prtouch_task
00000000 00000038 T command_stop_prtouch_step
00000000 00000040 T command_stop_prtouch_pres
00000000 00000070 T prtouch_step_task
"""
        with self.assertRaisesRegex(ValueError, "prtouch_step_task"):
            MODULE.verify(MODULE.parse_nm(output), "fixture")

    def test_lto_elf_layout_ignores_optimised_code_sizes(self):
        output = """
20000000 000000a0 b pr_read_pres_zip_data
200000a0 000000a0 b pr_read_pres_zip_tick
20000140 000000a0 b pr_read_step_zip_data
200001e0 000000a0 b pr_read_step_zip_tick
08002000 00000010 T command_config_prtouch_apax.lto_priv.0
08002010 00000030 T command_stop_prtouch_step.lto_priv.0
20000280 0000021c B pr_step.lto_priv.0
2000049c 00000798 B pr_apax.lto_priv.0
20000c34 00001324 B pr_pres.lto_priv.0
"""
        symbols = MODULE.parse_nm(output)
        self.assertIn(("pr_step", 0x21c, "B"), symbols)
        workspaces = MODULE.verify(
            symbols, "LTO fixture", check_code_sizes=False)
        self.assertEqual(len(workspaces), 4)


if __name__ == "__main__":
    unittest.main()
