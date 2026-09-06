#!/bin/sh
# Build generic GD32 platform examples without modifying checked-in configs.
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
JOBS=${JOBS:-$(nproc)}
BUILD_ROOT=${BUILD_ROOT:-$ROOT/build-gd32}

build_one() {
    name=$1
    source_config=$2
    out=$BUILD_ROOT/$name/
    config=$BUILD_ROOT/.$name.config

    echo "==> $name ($source_config)"
    mkdir -p "$BUILD_ROOT"
    cp "$ROOT/$source_config" "$config"
    make -C "$ROOT" KCONFIG_CONFIG="$config" OUT="$out" clean
    make -C "$ROOT" KCONFIG_CONFIG="$config" OUT="$out" olddefconfig
    make -C "$ROOT" KCONFIG_CONFIG="$config" OUT="$out" -j"$JOBS"
    arm-none-eabi-size "$out/klipper.elf"
}

targets=${*:-"f303-serial f303-usb f303-can f303-katapult e230-serial"}
for target in $targets; do
    case "$target" in
        f303-serial) build_one "$target" config/gd32f303cc_serial.config ;;
        f303-usb) build_one "$target" config/gd32f303cc_usb.config ;;
        f303-can) build_one "$target" config/gd32f303cc_can_pb8_pb9.config ;;
        f303-katapult) build_one "$target" config/gd32f303cc_serial_katapult.config ;;
        e230-serial) build_one "$target" config/gd32e230f8_serial.config ;;
        *) echo "Unknown GD32 build target: $target" >&2; exit 2 ;;
    esac
done
