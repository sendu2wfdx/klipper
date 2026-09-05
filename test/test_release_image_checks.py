import sys
import struct
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_creality_bootloader import validate as validate_bootloader  # noqa: E402
from check_gd32_application import PROFILES, validate as validate_app  # noqa: E402
from creality_factory_bl_reference import finalize_application  # noqa: E402


def make_application(profile, layout="factory"):
    base = 0x08003000 if layout == "factory" else 0x08002000
    image = bytearray(0x300)
    image[0:4] = PROFILES[profile]["stack"].to_bytes(4, "little")
    image[4:8] = (base + 0x101).to_bytes(4, "little")
    version = PROFILES[profile]["version"]
    if layout == "factory":
        return finalize_application(image, version)
    image[0x200:0x20C] = version.encode("ascii")
    return bytes(image)


def make_elf(image, entry, text_vma=None, text_lma=None, text_flags=0x6,
             segment_flags=5):
    """Create the smallest ELF32 needed by the application release gate."""
    if text_vma is None:
        text_vma = entry
    if text_lma is None:
        text_lma = text_vma
    text = image[:0x20]
    text_offset = 0x100
    names = b"\0.text\0.shstrtab\0"
    names_offset = text_offset + len(text)
    section_offset = 0x140
    data = bytearray(section_offset + 3 * 40)
    ident = b"\x7fELF\x01\x01\x01" + b"\0" * 9
    struct.pack_into(
        "<16sHHIIIIIHHHHHH", data, 0,
        ident, 2, 40, 1, entry, 52, section_offset, 0,
        52, 32, 1, 40, 3, 2)
    struct.pack_into(
        "<IIIIIIII", data, 52,
        1, text_offset, text_vma, text_lma, len(text), len(text),
        segment_flags, 4)
    data[text_offset:text_offset + len(text)] = text
    data[names_offset:names_offset + len(names)] = names
    struct.pack_into(
        "<IIIIIIIIII", data, section_offset + 40,
        1, 1, text_flags, text_vma, text_offset, len(text), 0, 0, 4, 0)
    struct.pack_into(
        "<IIIIIIIIII", data, section_offset + 80,
        7, 3, 0, 0, names_offset, len(names), 0, 0, 1, 0)
    return bytes(data)


def write_application_pair(tmp_path, profile, layout="factory", **elf_layout):
    image = make_application(profile, layout)
    image_path = tmp_path / "klipper.bin"
    elf_path = tmp_path / "klipper.elf"
    image_path.write_bytes(image)
    base = 0x08003000 if layout == "factory" else 0x08002000
    elf_path.write_bytes(make_elf(image, base, **elf_layout))
    return image_path, elf_path


@pytest.mark.parametrize("profile", ("main", "nozzle", "bed"))
def test_factory_application_gate_checks_all_profiles(tmp_path, profile):
    path, elf_path = write_application_pair(tmp_path, profile)
    validate_app(path, profile, "factory", elf_path)


def test_factory_application_gate_rejects_trailing_bytes(tmp_path):
    path, elf_path = write_application_pair(tmp_path, "main")
    path.write_bytes(path.read_bytes() + b"\xff")
    with pytest.raises(SystemExit, match="does not equal file size"):
        validate_app(path, "main", "factory", elf_path)


def test_katapult_application_gate_checks_its_actual_base(tmp_path):
    path, elf_path = write_application_pair(tmp_path, "nozzle", "katapult")
    validate_app(path, "nozzle", "katapult", elf_path)
    with pytest.raises(SystemExit, match="outside factory application range"):
        validate_app(path, "nozzle", "factory", elf_path)


def test_gate_accepts_vector_table_text_without_exec_flag(tmp_path):
    # Ubuntu 22.04's ARM GNU ld marks the vector-only .text as allocatable but
    # not executable; its containing PT_LOAD remains executable.
    image = make_application("main")
    image_path = tmp_path / "klipper.bin"
    elf_path = tmp_path / "klipper.elf"
    image_path.write_bytes(image)
    elf_path.write_bytes(make_elf(image, 0x08003000, text_flags=0x2))
    validate_app(image_path, "main", "factory", elf_path)


def test_gate_rejects_text_without_an_executable_load_segment(tmp_path):
    image = make_application("main")
    image_path = tmp_path / "klipper.bin"
    elf_path = tmp_path / "klipper.elf"
    image_path.write_bytes(image)
    elf_path.write_bytes(make_elf(
        image, 0x08003000, text_flags=0x2, segment_flags=4))
    with pytest.raises(SystemExit, match="not mapped by a loadable segment"):
        validate_app(image_path, "main", "factory", elf_path)


@pytest.mark.parametrize(
    "field,elf_layout,error",
    (
        ("entry", {"entry": 0x08002000}, "ELF entry"),
        ("VMA", {"text_vma": 0x08002000}, r"ELF \.text VMA"),
        ("LMA", {"text_lma": 0x08002000}, r"ELF \.text LMA"),
    ),
)
def test_factory_gate_rejects_each_wrong_elf_address(
        tmp_path, field, elf_layout, error):
    image = make_application("main")
    image_path = tmp_path / (field + ".bin")
    elf_path = tmp_path / (field + ".elf")
    image_path.write_bytes(image)
    entry = elf_layout.pop("entry", 0x08003000)
    elf_path.write_bytes(make_elf(image, entry, **elf_layout))
    with pytest.raises(SystemExit, match=error):
        validate_app(image_path, "main", "factory", elf_path)


def test_gate_checks_binary_and_elf_in_both_directions(tmp_path):
    factory_image = make_application("bed", "factory")
    katapult_image = make_application("bed", "katapult")
    image_path = tmp_path / "klipper.bin"
    elf_path = tmp_path / "klipper.elf"

    # A correct factory binary cannot hide a Katapult-linked ELF.
    image_path.write_bytes(factory_image)
    elf_path.write_bytes(make_elf(factory_image, 0x08002000))
    with pytest.raises(SystemExit, match="ELF entry"):
        validate_app(image_path, "bed", "factory", elf_path)

    # A correct factory-linked ELF cannot hide a Katapult-linked binary.
    image_path.write_bytes(katapult_image)
    elf_path.write_bytes(make_elf(katapult_image, 0x08003000))
    with pytest.raises(SystemExit, match="outside factory application range"):
        validate_app(image_path, "bed", "factory", elf_path)


def test_gate_rejects_an_elf_from_a_different_binary(tmp_path):
    path, elf_path = write_application_pair(tmp_path, "main")
    elf = bytearray(elf_path.read_bytes())
    elf[0x108] ^= 0x01
    elf_path.write_bytes(elf)
    with pytest.raises(SystemExit, match="contents do not match"):
        validate_app(path, "main", "factory", elf_path)


@pytest.mark.parametrize(
    "profile,stack,board_id",
    (
        ("main", 0x2000C000, b"mcu0_140_G31"),
        ("nozzle", 0x20008000, b"noz0_110_G30"),
        ("bed", 0x20002000, b"bed0_110_G21"),
    ),
)
def test_reconstructed_bootloader_gate_checks_profiles(
        tmp_path, profile, stack, board_id):
    image = bytearray(0x2F8C)
    image[0:4] = stack.to_bytes(4, "little")
    image[4:8] = (0x08000101).to_bytes(4, "little")
    image[0x2F80:0x2F8C] = board_id
    path = tmp_path / "bootloader.bin"
    path.write_bytes(image)
    validate_bootloader(path, profile)
