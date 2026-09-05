#!/bin/sh
# Reproducible GD32 build entry point. Run inside the dedicated Klipper WSL.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
JOBS=${JOBS:-$(nproc)}
BUILD_ROOT=${BUILD_ROOT:-$ROOT/build-gd32}
if [ -z "${KLIPPER_BUILD_VERSION:-}" ]; then
    KLIPPER_BUILD_VERSION=$(git -C "$ROOT" describe --always --tags --long --dirty 2>/dev/null || printf '%s' 'gd32-source-rebuild')
fi
export KLIPPER_BUILD_VERSION

build_one() {
    name=$1
    config=$2
    out="$BUILD_ROOT/$name/"
    echo "==> $name ($config)"
    make -C "$ROOT" KCONFIG_CONFIG="$ROOT/$config" OUT="$out" clean
    make -C "$ROOT" KCONFIG_CONFIG="$ROOT/$config" OUT="$out" olddefconfig
    make -C "$ROOT" KCONFIG_CONFIG="$ROOT/$config" OUT="$out" -j"$JOBS"
    arm-none-eabi-size "$out/klipper.elf"
    sha256sum "$out/klipper.bin"
    if ! grep -q '^CONFIG_GD32_FLASH_START_2000=y' "$ROOT/$config"; then
        "${PYTHON:-python3}" \
            "$ROOT/scripts/creality_factory_bl_reference.py" verify \
            "$out/klipper.bin"
    fi
    if [ "$name" = "f009-toolhead" ] || [ "$name" = "f009-toolhead-factory" ]; then
        "${PYTHON:-python3}" "$ROOT/scripts/check_prtouch_v3_object_layout.py" \
            --rebuilt-elf "$out/klipper.elf"
    fi
    if [ "$name" = "k1-leveling-v2" ]; then
        "${PYTHON:-python3}" \
            "$ROOT/scripts/check_prtouch_v2_dictionary.py" \
            "$out/klipper.dict"
    fi
}

build_linuxprocess() {
    name=linuxprocess
    out="$BUILD_ROOT/$name/"
    # Keep KCONFIG_CONFIG outside OUT: the Makefile clean target removes OUT.
    # It must also not point at the checked-in minimal config because
    # olddefconfig expands the file in place.
    config="$BUILD_ROOT/.linuxprocess.config"
    echo "==> $name (test/configs/linuxprocess.config)"
    mkdir -p "$BUILD_ROOT"
    cp "$ROOT/test/configs/linuxprocess.config" "$config"
    make -C "$ROOT" KCONFIG_CONFIG="$config" OUT="$out" clean
    make -C "$ROOT" KCONFIG_CONFIG="$config" OUT="$out" olddefconfig
    make -C "$ROOT" KCONFIG_CONFIG="$config" OUT="$out" -j"$JOBS"
    sha256sum "$out/klipper.elf" "$out/klipper.dict"
}

arm-none-eabi-gcc --version | head -n 1
make --version | head -n 1
echo "MCU protocol version: $KLIPPER_BUILD_VERSION"

targets=${*:-"f009-main f009-toolhead e230-pa23 e230-pa910 test-cc-serial test-cc-usb"}
for target in $targets; do
    case "$target" in
        f009-main) build_one "$target" config/f009_gd32f303_serial.config ;;
        f009-main-factory) build_one "$target" config/f009_gd32f303_serial_factory12k.config ;;
        f009-toolhead) build_one "$target" config/f009_toolhead_gd32f303cb_katapult8k.config ;;
        f009-toolhead-factory) build_one "$target" config/f009_toolhead_gd32f303cb_factory12k.config ;;
        f009-serial) build_one "$target" config/f009_gd32f303_serial.config ;;
        f009-usb) build_one "$target" config/f009_gd32f303_usb.config ;;
        f009-can) build_one "$target" config/f009_gd32f303_can_pb8_pb9.config ;;
        e230-pa23) build_one "$target" config/gd32e230f8_serial_pa2_pa3.config ;;
        e230-pa910) build_one "$target" config/gd32e230f8_serial_pa9_pa10.config ;;
        e230-pa910-factory) build_one "$target" config/gd32e230f8_serial_pa9_pa10_factory12k.config ;;
        k1-leveling-v2) build_one "$target" config/k1_leveling_gd32e230x8_factory12k_prtouch_v2.config ;;
        test-cc-serial) build_one "$target" config/gd32f303cc_test_serial_pa9_pa10_noboot.config ;;
        test-cc-usb) build_one "$target" config/gd32f303cc_test_usb_pa11_pa12_noboot.config ;;
        test-cc-usb-dbuf) build_one "$target" config/gd32f303cc_test_usb_pa11_pa12_noboot_dbuf.config ;;
        test-cc-usb-dbuf-prtouch-v2-adc) build_one "$target" config/gd32f303cc_test_usb_pa11_pa12_noboot_dbuf_prtouch_v2_adc.config ;;
        linuxprocess) build_linuxprocess ;;
        *) echo "Unknown GD32 build target: $target" >&2; exit 2 ;;
    esac
done
