import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "creality_factory_bl_reference.py"
SPEC = importlib.util.spec_from_file_location("creality_factory_bl_reference", SCRIPT)
bl = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(bl)


def test_commands_are_self_checking():
    assert bl.HANDSHAKE == b"\x75"
    assert bl.command("get_version") == bytes.fromhex("00 ff")
    assert bl.command("update_request") == bytes.fromhex("01 fe")
    assert bl.command("start_app") == bytes.fromhex("02 fd")
    assert bl.command("get_sector_size") == bytes.fromhex("03 fc")
    assert bl.command("enter_transparent") == bytes.fromhex("04 fb")
    assert bl.command("exit_transparent") == bytes.fromhex("05 fa")
    assert set(bl.BOOTLOADER_COMMANDS.values()) == {
        bytes.fromhex("00 ff"),
        bytes.fromhex("01 fe"),
        bytes.fromhex("02 fd"),
        bytes.fromhex("03 fc"),
    }
    assert set(bl.TRANSPARENT_COMMANDS.values()) == {
        bytes.fromhex("04 fb"),
        bytes.fromhex("05 fa"),
    }


def test_ack_is_data_plus_checksum():
    assert bl.packet(b"\x75") == bytes.fromhex("75 8a")
    bl.parse_ack(bytes.fromhex("75 8a"))
    with pytest.raises(ValueError):
        bl.parse_ack(bytes.fromhex("75 89"))


def test_version_is_exactly_25_payload_bytes():
    version = b"mcu0_140_G31-mcu0_022_000"
    assert len(version) == 25
    assert bl.parse_version(bl.packet(version)) == version.decode()
    with pytest.raises(ValueError):
        bl.parse_version(bl.packet(version[:-1]))


@pytest.mark.parametrize(
    ("code", "size"),
    [(1, 1024), (4, 4096), (0xFF, 4), (0xFE, 8), (0x80, 512)],
)
def test_sector_size_signed_encoding(code, size):
    assert bl.decode_sector_size(code) == size


def test_zero_sector_size_is_invalid():
    with pytest.raises(ValueError):
        bl.decode_sector_size(0)


def test_application_length_is_little_endian_and_checked():
    frame = bl.encode_application_length(0x12345678)
    assert frame[:4] == bytes.fromhex("78 56 34 12")
    assert frame[-1] == bl.checksum(frame[:-1])


def test_block_statuses_are_checked_packets():
    assert bl.parse_block_result(bl.packet(b"\x75")).name == "continue"
    assert bl.parse_block_result(bl.packet(b"\x20")).name == "ok"
    assert bl.parse_block_result(bl.packet(b"\x1f")).retry
    assert bl.parse_block_result(bl.packet(b"\x21")).fatal


def test_data_block_limit():
    assert len(bl.encode_data_block(bytes(0x4400))) == 0x4401
    with pytest.raises(ValueError):
        bl.encode_data_block(bytes(0x4401))


def test_factory_sector_profiles_match_bootloaders():
    assert bl.FACTORY_PROFILES["CR1FN240306C13"]["sector_code"] == 2
    assert bl.FACTORY_PROFILES["CR4NU200360C23"]["block_size"] == 2048
    assert bl.FACTORY_PROFILES["CR0NN200360C10"]["sector_code"] == 1
    assert bl.FACTORY_PROFILES["CR0NN200360C10"]["block_size"] == 1024


def test_finalize_and_verify_factory_application():
    raw = bytes((index * 17) & 0xFF for index in range(0x500))
    image = bl.finalize_application(raw, "mcu0_999_000")
    valid, version, length, expected_crc = bl.verify_application(image)
    assert valid
    assert version == "mcu0_999_000"
    assert length == len(raw)
    assert expected_crc == int.from_bytes(
        image[bl.APP_CHECK_OFFSET:bl.APP_CHECK_OFFSET + 2], "little"
    )
    damaged = bytearray(image)
    damaged[0x300] ^= 1
    assert not bl.verify_application(damaged)[0]


def test_factory_application_requires_exact_version_length():
    with pytest.raises(ValueError):
        bl.finalize_application(bytes(0x300), "short")


def test_profile_size_limits_and_block_generation():
    assert bl.validate_application_size(0x400, "CR0NN200360C10") == 0xD000
    with pytest.raises(ValueError):
        bl.validate_application_size(0xD001, "CR0NN200360C10")
    image = bytes(4097)
    blocks = list(bl.iter_update_blocks(image, "CR4NU200360C23"))
    assert [len(frame) for frame in blocks] == [2049, 2049, 2]
    assert all(frame[-1] == bl.checksum(frame[:-1]) for frame in blocks)
