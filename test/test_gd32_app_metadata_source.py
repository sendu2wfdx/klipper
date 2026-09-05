from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LINKER = (ROOT / "src" / "generic" / "armcm_link.lds.S").read_text(
    encoding="utf-8"
)
METADATA = (ROOT / "src" / "gd32" / "app_metadata.c").read_text(
    encoding="utf-8"
)
CRC_TOOL = (ROOT / "tool" / "host_crc16.c").read_text(encoding="utf-8")
BUILD_MATRIX = (ROOT / "scripts" / "build-gd32-matrix.sh").read_text(
    encoding="utf-8"
)


def test_factory_metadata_has_a_linker_owned_fixed_region():
    assert ".gd32_app_metadata ORIGIN(rom) + 0x200" in LINKER
    assert "KEEP(*(.gd32_app_metadata))" in LINKER
    assert "SIZEOF(.gd32_app_metadata) == 0x12" in LINKER
    assert "_text_vectortable_end <= ORIGIN(rom) + 0x200" in LINKER
    assert ".text_body" in LINKER


def test_metadata_object_matches_factory_layout_exactly():
    assert "char version[12]" in METADATA
    assert "uint16_t crc16" in METADATA
    assert "uint32_t image_length" in METADATA
    assert "sizeof(CONFIG_GD32_APP_VERSION) == 13" in METADATA
    assert "sizeof(Gd32AppMetadata) == 0x12" in METADATA


def test_crc_postprocessor_is_bounded_and_idempotent():
    assert "flen < 0x212" in CRC_TOOL
    assert "fseek(f, 0x20C, SEEK_SET)" in CRC_TOOL
    assert "fwrite(fbuff, 1, 6, f) != 6" in CRC_TOOL
    assert "fwrite(&crc16Rtn, 1, 2, f) != 2" in CRC_TOOL
    assert "fwrite(&flen, 1, 4, f) != 4" in CRC_TOOL


def test_board_configs_carry_the_recovered_factory_versions():
    expected = {
        "f009_gd32f303_serial_factory12k.config": "mcu0_022_000",
        "f009_gd32f303_serial.config": "mcu0_022_000",
        "f009_gd32f303_usb.config": "mcu0_022_000",
        "f009_gd32f303_can_pb8_pb9.config": "mcu0_022_000",
        "f009_toolhead_gd32f303cb_katapult8k.config": "noz0_019_000",
        "f009_toolhead_gd32f303cb_factory12k.config": "noz0_019_000",
        "gd32e230f8_serial_pa2_pa3.config": "bed0_017_000",
        "gd32e230f8_serial_pa9_pa10.config": "bed0_017_000",
        "gd32e230f8_serial_pa9_pa10_factory12k.config": "bed0_017_000",
        "gd32f303cc_test_usb_pa11_pa12_noboot.config": "gd32_000_000",
    }
    for filename, version in expected.items():
        config = (ROOT / "config" / filename).read_text(encoding="utf-8")
        assert f'CONFIG_GD32_APP_VERSION="{version}"' in config


def test_f009_build_targets_use_rct6_xc_capacity_configs():
    assert (
        'f009-main) build_one "$target" config/f009_gd32f303_serial.config'
        in BUILD_MATRIX
    )
    assert (
        'f009-main-factory) build_one "$target" '
        "config/f009_gd32f303_serial_factory12k.config"
        in BUILD_MATRIX
    )
    assert "f009_mainboard_gd32f303re_" not in BUILD_MATRIX

    active_main_configs = (
        "f009_gd32f303_serial.config",
        "f009_gd32f303_serial_factory12k.config",
        "f009_gd32f303_usb.config",
        "f009_gd32f303_can_pb8_pb9.config",
    )
    for filename in active_main_configs:
        config = (ROOT / "config" / filename).read_text(encoding="utf-8")
        assert 'CONFIG_MCU="gd32f303xc"' in config
        assert "CONFIG_FLASH_SIZE=0x00040000" in config
        assert "CONFIG_MACH_GD32F303XC=y" in config
        assert "CONFIG_MACH_GD32F303XE=y" not in config


def test_factory_e230_target_matches_recovered_prtouch_board_layout():
    config = (
        ROOT / "config" / "gd32e230f8_serial_pa9_pa10_factory12k.config"
    ).read_text(encoding="utf-8")
    assert 'CONFIG_MCU="gd32e230x8"' in config
    assert "CONFIG_FLASH_SIZE=0x00010000" in config
    assert "CONFIG_FLASH_APPLICATION_ADDRESS=0x08003000" in config
    assert "CONFIG_GD32_FLASH_START_3000=y" in config
    assert "CONFIG_GD32_SERIAL_USART0_PA9_PA10=y" in config
    assert "CONFIG_SERIAL_BAUD=230400" in config
    assert (
        'e230-pa910-factory) build_one "$target" '
        "config/gd32e230f8_serial_pa9_pa10_factory12k.config"
        in BUILD_MATRIX
    )


def test_factory_toolhead_target_matches_recovered_prtouch_layout():
    config = (
        ROOT / "config" / "f009_toolhead_gd32f303cb_factory12k.config"
    ).read_text(encoding="utf-8")
    assert 'CONFIG_MCU="gd32f303xb"' in config
    assert "CONFIG_FLASH_SIZE=0x00020000" in config
    assert "CONFIG_FLASH_APPLICATION_ADDRESS=0x08003000" in config
    assert "CONFIG_GD32_FLASH_START_3000=y" in config
    assert "CONFIG_GD32_SERIAL_USART1_PA2_PA3=y" in config
    assert "CONFIG_WANT_PRTOUCH_V3_COMPAT=y" in config
    assert (
        'f009-toolhead-factory) build_one "$target" '
        "config/f009_toolhead_gd32f303cb_factory12k.config"
        in BUILD_MATRIX
    )
    assert (
        '[ "$name" = "f009-toolhead-factory" ]' in BUILD_MATRIX
    )
