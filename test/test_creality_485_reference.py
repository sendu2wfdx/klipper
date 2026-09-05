import importlib.util
import pathlib
import unittest


SCRIPT = pathlib.Path(__file__).parents[1] / "scripts" / \
    "creality_485_reference.py"
SPEC = importlib.util.spec_from_file_location("creality_485_reference", SCRIPT)
MODEL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODEL)


class Creality485ReferenceTest(unittest.TestCase):
    def test_factory_stop_frame(self):
        frame = bytes.fromhex("f7 eb 03 ff 56 cf")
        self.assertEqual(MODEL.crc8(frame[2:-1]), frame[-1])
        self.assertEqual(MODEL.encode(0xEB, 0xFF, 0x56), frame)
        self.assertEqual(MODEL.decode(frame), (0xEB, 0xFF, 0x56, b""))

    def test_payload_round_trip(self):
        frame = MODEL.encode(7, 1, 2, b"\x11\x22\x33")
        self.assertEqual(MODEL.decode(frame), (7, 1, 2, b"\x11\x22\x33"))

    def test_bad_crc_is_rejected(self):
        frame = bytearray(MODEL.encode(1, 2, 3))
        frame[-1] ^= 1
        with self.assertRaisesRegex(ValueError, "crc"):
            MODEL.decode(frame)

    def test_factory_discovery_frames(self):
        for name, group in MODEL.DEVICE_GROUPS.items():
            frame = MODEL.discovery_frame(name)
            self.assertEqual(MODEL.decode(frame),
                             (group, 0, MODEL.FUNC_DISCOVER,
                              bytes((group, group))))

    def test_set_slave_address(self):
        uuid = bytes(range(12))
        frame = MODEL.set_slave_address_frame("motor", 0x85, uuid)
        self.assertEqual(MODEL.decode(frame),
                         (0xFD, 0, MODEL.FUNC_SET_SLAVE_ADDRESS,
                          b"\x85" + uuid))

    def test_parse_slave_info_and_set_address_reply(self):
        uuid = bytes.fromhex("00112233445566778899aabb")
        frame = MODEL.encode(0xFD, 0, MODEL.FUNC_DISCOVER,
                             b"\x02\x01" + uuid)
        info = MODEL.parse_slave_info(frame)
        self.assertEqual(info["address"], 0xFD)
        self.assertEqual(info["device_type"], 2)
        self.assertEqual(info["mode"], 1)
        self.assertEqual(info["uuid"], uuid)
        reply = MODEL.encode(0x85, 0, MODEL.FUNC_SET_SLAVE_ADDRESS,
                             b"\x75\x00" + uuid)
        self.assertEqual(MODEL.parse_set_address_reply(reply),
                         (0x85, 0, uuid))

    def test_online_address_table_and_loader_frames(self):
        uuid = bytes.fromhex("00112233445566778899aabb")
        self.assertEqual(MODEL.decode(MODEL.online_check_frame(1)),
                         (1, 0, MODEL.FUNC_ONLINE_CHECK, b""))
        self.assertEqual(MODEL.decode(MODEL.get_address_table_frame(2)),
                         (2, 0, MODEL.FUNC_GET_ADDRESS_TABLE, b""))
        self.assertEqual(MODEL.decode(MODEL.loader_to_app_frame()),
                         (0xFF, 0, MODEL.FUNC_LOADER_TO_APP, b"\x01"))

        online = MODEL.encode(1, 0, MODEL.FUNC_ONLINE_CHECK,
                              b"\x01\x00" + uuid)
        self.assertEqual(MODEL.parse_online_reply(online)["uuid"], uuid)
        table = MODEL.encode(2, 0, MODEL.FUNC_GET_ADDRESS_TABLE,
                             b"\x01\x01" + uuid)
        self.assertEqual(MODEL.parse_address_table_reply(table)["mode"], 1)

    def test_address_reply_parsers_reject_wrong_function(self):
        payload = b"\x01\x00" + bytes(12)
        with self.assertRaisesRegex(ValueError, "address-management"):
            MODEL.parse_online_reply(
                MODEL.encode(1, 0, MODEL.FUNC_DISCOVER, payload))

    def test_upgrade_control_and_length(self):
        frame = MODEL.upgrade_command_frame(
            0x85, MODEL.UPGRADE_GET_SECTOR_SIZE)
        self.assertEqual(MODEL.decode(frame),
                         (0x85, 0, MODEL.FUNC_UPGRADE, b"\x03"))
        frame = MODEL.upgrade_length_frame(0x85, 0x12345678)
        self.assertEqual(MODEL.decode(frame),
                         (0x85, 0, MODEL.FUNC_UPGRADE,
                          bytes.fromhex("78 56 34 12")))

    def test_safe_upgrade_payload_limit(self):
        MODEL.upgrade_data_frame(1, bytes(252))
        with self.assertRaisesRegex(ValueError, "payload too long"):
            MODEL.upgrade_data_frame(1, bytes(253))

    def test_unknown_upgrade_command_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown"):
            MODEL.upgrade_command_frame(1, 0x05)

    def test_upgrade_reply_state_machine(self):
        version = b"motor_001_00-motor_002_00"
        self.assertEqual(len(version), 25)
        self.assertEqual(MODEL.interpret_upgrade_reply(
            "get_version", version), version)
        self.assertEqual(MODEL.interpret_upgrade_reply(
            "get_sector_size", b"\x02"), 2048)
        self.assertEqual(MODEL.interpret_upgrade_reply(
            "get_sector_size", b"\xff"), 4)
        self.assertTrue(MODEL.interpret_upgrade_reply(
            "update_request", b"\x75"))
        self.assertEqual(MODEL.interpret_upgrade_reply(
            "app_data", b"\x75"), "continue")
        self.assertEqual(MODEL.interpret_upgrade_reply(
            "app_data", b"\x20"), "finished")
        self.assertEqual(MODEL.interpret_upgrade_reply(
            "app_data", b"\x21"), "error")


if __name__ == "__main__":
    unittest.main()
