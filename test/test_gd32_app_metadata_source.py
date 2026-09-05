from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LINKER = (ROOT / "src" / "generic" / "armcm_link.lds.S").read_text(
    encoding="utf-8"
)
METADATA = (ROOT / "src" / "gd32" / "app_metadata.c").read_text(
    encoding="utf-8"
)
CRC_TOOL = (ROOT / "tool" / "host_crc16.c").read_text(encoding="utf-8")
BUILD_TARGETS = (ROOT / "scripts" / "build-ender3-v4.sh").read_text(
    encoding="utf-8"
)
GD32_KCONFIG = (ROOT / "src" / "gd32" / "Kconfig").read_text(
    encoding="utf-8"
)
GD32_MAKEFILE = (ROOT / "src" / "gd32" / "Makefile").read_text(
    encoding="utf-8"
)
BOOTLOADER_MAKEFILE = (
    ROOT / "src" / "gd32" / "creality_bootloader.mk"
).read_text(encoding="utf-8")
WORKFLOW = (
    ROOT / ".github" / "workflows" / "gd32-build-test.yaml"
).read_text(encoding="utf-8")
WSL_ENTRY = (ROOT / "build-ender3-v4.ps1").read_text(encoding="utf-8")


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
        "f009_main.config": "mcu0_022_000",
        "f009_gd32f303_serial.config": "mcu0_022_000",
        "f009_gd32f303_usb.config": "mcu0_022_000",
        "f009_gd32f303_can_pb8_pb9.config": "mcu0_022_000",
        "f009_toolhead_gd32f303cb_katapult8k.config": "noz0_019_000",
        "f009_nozzle.config": "noz0_019_000",
        "gd32e230f8_serial_pa2_pa3.config": "bed0_017_000",
        "gd32e230f8_serial_pa9_pa10.config": "bed0_017_000",
        "f009_bed.config": "bed0_017_000",
        "gd32f303cc_test_usb_pa11_pa12_noboot.config": "gd32_000_000",
    }
    for filename, version in expected.items():
        config = (ROOT / "config" / filename).read_text(encoding="utf-8")
        assert f'CONFIG_GD32_APP_VERSION="{version}"' in config


def test_f009_build_targets_are_the_three_factory_boards_and_linux():
    assert (
        'targets=${*:-"main nozzle bed linux"}' in BUILD_TARGETS
    )
    assert (
        'main) build_one "$target" config/f009_main.config'
        in BUILD_TARGETS
    )
    assert 'nozzle) build_one "$target" config/f009_nozzle.config' \
        in BUILD_TARGETS
    assert 'bed) build_one "$target" config/f009_bed.config' \
        in BUILD_TARGETS
    assert 'linux) build_linux_mcu' in BUILD_TARGETS
    assert "f009_mainboard_gd32f303re_" not in BUILD_TARGETS

    active_main_configs = (
        "f009_gd32f303_serial.config",
        "f009_main.config",
        "f009_gd32f303_usb.config",
        "f009_gd32f303_can_pb8_pb9.config",
    )
    for filename in active_main_configs:
        config = (ROOT / "config" / filename).read_text(encoding="utf-8")
        assert 'CONFIG_MCU="gd32f303xc"' in config
        assert "CONFIG_FLASH_SIZE=0x00040000" in config
        assert "CONFIG_MACH_GD32F303XC=y" in config
        assert "CONFIG_MACH_GD32F303XE=y" not in config


def test_formal_profiles_build_the_reconstructed_12k_bootloader():
    for filename in ("f009_main.config", "f009_nozzle.config",
                     "f009_bed.config"):
        config = (ROOT / "config" / filename).read_text(encoding="utf-8")
        assert "CONFIG_GD32_CREALITY_BOOTLOADER=y" in config
        assert "CONFIG_GD32_FLASH_START_3000=y" in config
    assert "config GD32_CREALITY_BOOTLOADER" in GD32_KCONFIG
    assert "it is not Creality's original binary" in GD32_KCONFIG
    assert "include src/gd32/creality_bootloader.mk" in GD32_MAKEFILE
    for role in ("CONFIG_MAIN_MCU_BOARD", "CONFIG_NOZZLE_MCU_BOARD",
                 "CONFIG_BED_MCU_BOARD"):
        assert role in BOOTLOADER_MAKEFILE
    assert "include src/bootloader/klipper.mk" in BOOTLOADER_MAKEFILE
    assert "submodules: recursive" in WORKFLOW


def test_release_workflow_excludes_native_linux_smoke_artifacts():
    upload = WORKFLOW[WORKFLOW.index("- name: Upload firmware artifacts"):]
    for profile in ("main", "nozzle", "bed"):
        assert f"gd32-build/{profile}/klipper.bin" in upload
        assert f"gd32-build/{profile}/klipper.dict" in upload
        assert f"gd32-build/{profile}/bootloader.bin" in upload
    assert "gd32-build/linux/" not in upload
    assert "gd32-build/**" not in upload


def test_factory_e230_target_matches_recovered_prtouch_board_layout():
    config = (
        ROOT / "config" / "f009_bed.config"
    ).read_text(encoding="utf-8")
    assert 'CONFIG_MCU="gd32e230x8"' in config
    assert "CONFIG_FLASH_SIZE=0x00010000" in config
    assert "CONFIG_FLASH_APPLICATION_ADDRESS=0x08003000" in config
    assert "CONFIG_GD32_FLASH_START_3000=y" in config
    assert "CONFIG_GD32_SERIAL_USART0_PA9_PA10=y" in config
    assert "CONFIG_SERIAL_BAUD=230400" in config
    assert (
        'bed) build_one "$target" config/f009_bed.config'
        in BUILD_TARGETS
    )


def test_factory_toolhead_target_matches_recovered_prtouch_layout():
    config = (
        ROOT / "config" / "f009_nozzle.config"
    ).read_text(encoding="utf-8")
    assert 'CONFIG_MCU="gd32f303xb"' in config
    assert "CONFIG_FLASH_SIZE=0x00020000" in config
    assert "CONFIG_FLASH_APPLICATION_ADDRESS=0x08003000" in config
    assert "CONFIG_GD32_FLASH_START_3000=y" in config
    assert "CONFIG_GD32_SERIAL_USART1_PA2_PA3=y" in config
    assert "CONFIG_HAVE_PRTOUCH_V3=y" in config
    assert (
        'nozzle) build_one "$target" config/f009_nozzle.config'
        in BUILD_TARGETS
    )
    assert '[ "$name" = "nozzle" ]' in BUILD_TARGETS


def test_f009_targets_can_switch_to_the_katapult_offset():
    assert "APP_LAYOUT=${APP_LAYOUT:-factory}" in BUILD_TARGETS
    assert "expected_address=0x08002000" in BUILD_TARGETS
    assert "CONFIG_GD32_FLASH_START_2000=y" in BUILD_TARGETS
    assert "CONFIG_GD32_FLASH_START_3000 is not set" in BUILD_TARGETS
    assert 'cd "$APP_BUILD_ROOT"' in BUILD_TARGETS
    assert "check_gd32_application.py" in BUILD_TARGETS
    assert '"$out/klipper.bin" "$out/klipper.elf"' in BUILD_TARGETS
    assert '[ -e "$out/bootloader.bin" ]' in BUILD_TARGETS
    assert '[ -e "$out/bootloader.elf" ]' in BUILD_TARGETS
    assert "build-report-katapult.txt" in BUILD_TARGETS
    assert "APP_LAYOUT: katapult" in WORKFLOW
    katapult_gate = WORKFLOW[
        WORKFLOW.index("- name: Build the same three applications"):
        WORKFLOW.index("- name: Load Ender-3 V4 configurations")
    ]
    assert "build-ender3-v4.sh main nozzle bed" in katapult_gate
    assert "linux" not in katapult_gate.lower()


def test_t113_linux_cross_build_requires_explicit_staging_dir():
    assert 'if [ -z "${LINUX_STAGING_DIR:-}" ]' in BUILD_TARGETS
    assert "STAGING_DIR=$LINUX_STAGING_DIR" in BUILD_TARGETS
    assert "export STAGING_DIR" in BUILD_TARGETS
    assert '"LINUX_CROSS_PREFIX=$LinuxPrefix"' in WSL_ENTRY
    assert '"LINUX_STAGING_DIR=$LinuxToolchain"' in WSL_ENTRY
    assert "Resolve-Path -LiteralPath $PSScriptRoot" in WSL_ENTRY
    assert "$WindowsRepo.Replace('\\', '/')" in WSL_ENTRY
    assert "wslpath -a -u $WindowsRepoForWsl" in WSL_ENTRY
    assert "Documents/ChatGPT" not in WSL_ENTRY
