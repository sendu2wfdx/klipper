#!/usr/bin/env python3
"""Validate an integrated reconstructed Creality-compatible bootloader."""

import argparse
import hashlib
import struct
from pathlib import Path


PROFILES = {
    "main": (0x2000C000, b"mcu0_140_G31"),
    "nozzle": (0x20008000, b"noz0_110_G30"),
    "bed": (0x20002000, b"bed0_110_G21"),
}


def validate(path, profile):
    image = path.read_bytes()
    expected_stack, expected_id = PROFILES[profile]
    if not 0x2F8C <= len(image) <= 0x3000:
        raise SystemExit(
            "%s: reconstructed bootloader size %d is outside the 12 KiB "
            "factory-compatible region" % (path, len(image)))
    stack, reset = struct.unpack_from("<II", image)
    if stack != expected_stack:
        raise SystemExit(
            "%s: stack 0x%08x does not match %s (0x%08x)"
            % (path, stack, profile, expected_stack))
    if not reset & 1 or not 0x08000000 <= (reset & ~1) < 0x08003000:
        raise SystemExit("%s: invalid Thumb reset vector 0x%08x"
                         % (path, reset))
    board_id = image[0x2F80:0x2F8C]
    if board_id != expected_id:
        raise SystemExit(
            "%s: board id %r does not match %r"
            % (path, board_id, expected_id))
    digest = hashlib.sha256(image).hexdigest()
    print(
        "%s reconstructed_bl size=%d stack=0x%08x reset=0x%08x "
        "board_id=%s sha256=%s"
        % (profile, len(image), stack, reset, board_id.decode("ascii"), digest))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=tuple(PROFILES))
    parser.add_argument("image", type=Path)
    args = parser.parse_args()
    validate(args.image, args.profile)


if __name__ == "__main__":
    main()
