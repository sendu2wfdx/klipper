#!/bin/sh
# Reproducible Ender-3 V4 MCU build entry point.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
JOBS=${JOBS:-$(nproc)}
BUILD_ROOT=${BUILD_ROOT:-$ROOT/build-gd32}
APP_LAYOUT=${APP_LAYOUT:-factory}
REPORT_DIR=${REPORT_DIR:-$BUILD_ROOT/reports}
case "$APP_LAYOUT" in
    factory)
        APP_BUILD_ROOT=$BUILD_ROOT
        DEFAULT_REPORT=$REPORT_DIR/build-report.txt
        ;;
    katapult)
        APP_BUILD_ROOT=$BUILD_ROOT/katapult
        DEFAULT_REPORT=$REPORT_DIR/build-report-katapult.txt
        ;;
    *) echo "APP_LAYOUT must be 'factory' or 'katapult'" >&2; exit 2 ;;
esac
REPORT=${REPORT:-$DEFAULT_REPORT}
if [ -z "${KLIPPER_BUILD_VERSION:-}" ]; then
    KLIPPER_BUILD_VERSION=$(git -C "$ROOT" describe --always --tags --long --dirty 2>/dev/null || printf '%s' 'gd32-source-rebuild')
fi
export KLIPPER_BUILD_VERSION
mkdir -p "$REPORT_DIR"
{
    echo "Ender-3 V4 source build report"
    echo "application_layout=$APP_LAYOUT"
    echo "The bootloader entries below describe an independent compatible reconstruction, not Creality's original binary."
} > "$REPORT"

build_one() {
    name=$1
    source_config=$2
    out="$APP_BUILD_ROOT/$name/"
    config="$BUILD_ROOT/.$APP_LAYOUT-$name.config"
    echo "==> $name ($source_config)"
    mkdir -p "$BUILD_ROOT"
    # olddefconfig expands KCONFIG_CONFIG in place.  Build from a disposable
    # copy so a validation run never rewrites the checked-in configuration.
    cp "$ROOT/$source_config" "$config"
    case "$APP_LAYOUT" in
        factory)
            expected_address=0x08003000
            ;;
        katapult)
            expected_address=0x08002000
            sed -i \
                -e 's/^CONFIG_FLASH_APPLICATION_ADDRESS=.*/CONFIG_FLASH_APPLICATION_ADDRESS=0x08002000/' \
                -e 's/^# CONFIG_GD32_FLASH_START_2000 is not set$/CONFIG_GD32_FLASH_START_2000=y/' \
                -e 's/^CONFIG_GD32_FLASH_START_3000=y$/# CONFIG_GD32_FLASH_START_3000 is not set/' \
                -e 's/^CONFIG_GD32_CREALITY_BOOTLOADER=y$/# CONFIG_GD32_CREALITY_BOOTLOADER is not set/' \
                "$config"
            ;;
    esac
    make -C "$ROOT" KCONFIG_CONFIG="$config" OUT="$out" clean
    make -C "$ROOT" KCONFIG_CONFIG="$config" OUT="$out" olddefconfig
    grep -q "^CONFIG_FLASH_APPLICATION_ADDRESS=$expected_address$" "$config"
    make -C "$ROOT" KCONFIG_CONFIG="$config" OUT="$out" -j"$JOBS"
    arm-none-eabi-size "$out/klipper.elf"
    sha256sum "$out/klipper.bin"
    app_report=$("${PYTHON:-python3}" \
        "$ROOT/scripts/check_gd32_application.py" \
        "$name" "$APP_LAYOUT" "$out/klipper.bin" "$out/klipper.elf")
    echo "$app_report"
    echo "$app_report" >> "$REPORT"
    if [ "$APP_LAYOUT" = factory ]; then
        grep -q '^CONFIG_GD32_CREALITY_BOOTLOADER=y' "$config"
        test -s "$out/bootloader.bin"
        bootloader_report=$("${PYTHON:-python3}" \
            "$ROOT/scripts/check_creality_bootloader.py" \
            "$name" "$out/bootloader.bin")
        echo "$bootloader_report"
        echo "$bootloader_report" >> "$REPORT"
    else
        if grep -q '^CONFIG_GD32_CREALITY_BOOTLOADER=y' "$config"; then
            echo "Katapult layout unexpectedly kept the 12 KiB bootloader" >&2
            exit 1
        fi
        if [ -e "$out/bootloader.bin" ] || [ -e "$out/bootloader.elf" ]; then
            echo "Katapult layout unexpectedly produced a bootloader artifact" >&2
            exit 1
        fi
    fi
    if [ "$name" = "nozzle" ]; then
        "${PYTHON:-python3}" "$ROOT/scripts/check_prtouch_v3_object_layout.py" \
            --rebuilt-elf "$out/klipper.elf"
    fi
    (
        cd "$APP_BUILD_ROOT"
        sha256sum "$name/klipper.bin" "$name/klipper.dict"
    ) >> "$REPORT"
}

build_linux_mcu() {
    name=linux
    out="$BUILD_ROOT/$name/"
    # Keep KCONFIG_CONFIG outside OUT: the Makefile clean target removes OUT.
    # It must also not point at the checked-in minimal config because
    # olddefconfig expands the file in place.
    config="$BUILD_ROOT/.$name.config"
    echo "==> $name (config/f009_linux.config)"
    mkdir -p "$BUILD_ROOT"
    if [ -n "${LINUX_CROSS_PREFIX:-}" ]; then
        if [ -z "${LINUX_STAGING_DIR:-}" ]; then
            echo "LINUX_STAGING_DIR is required with LINUX_CROSS_PREFIX" >&2
            exit 2
        fi
        if [ ! -d "$LINUX_STAGING_DIR" ]; then
            echo "LINUX_STAGING_DIR is not a directory: $LINUX_STAGING_DIR" >&2
            exit 2
        fi
        STAGING_DIR=$LINUX_STAGING_DIR
        export STAGING_DIR
    fi
    cp "$ROOT/config/f009_linux.config" "$config"
    make -C "$ROOT" KCONFIG_CONFIG="$config" OUT="$out" clean
    make -C "$ROOT" KCONFIG_CONFIG="$config" OUT="$out" olddefconfig
    make -C "$ROOT" CROSS_PREFIX="${LINUX_CROSS_PREFIX:-}" \
        KCONFIG_CONFIG="$config" OUT="$out" -j"$JOBS"
    sha256sum "$out/klipper.elf" "$out/klipper.dict"
    if [ -n "${LINUX_CROSS_PREFIX:-}" ]; then
        linux_kind=t113_cross_build
    else
        linux_kind=host_smoke_only
    fi
    printf 'linux %s elf=%s\n' "$linux_kind" "$(file -b "$out/klipper.elf")" \
        >> "$REPORT"
}

arm-none-eabi-gcc --version | head -n 1
make --version | head -n 1
echo "MCU protocol version: $KLIPPER_BUILD_VERSION"
echo "Application layout: $APP_LAYOUT"

targets=${*:-"main nozzle bed linux"}
for target in $targets; do
    case "$target" in
        main) build_one "$target" config/f009_main.config ;;
        nozzle) build_one "$target" config/f009_nozzle.config ;;
        bed) build_one "$target" config/f009_bed.config ;;
        linux) build_linux_mcu ;;
        *) echo "Unknown Ender-3 V4 build target: $target" >&2; exit 2 ;;
    esac
done
