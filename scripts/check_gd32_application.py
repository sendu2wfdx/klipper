#!/usr/bin/env python3
"""Validate an Ender-3 V4 GD32 application image and its load address."""

import argparse
import hashlib
import struct
from pathlib import Path

from creality_factory_bl_reference import (
    APP_CHECK_OFFSET,
    APP_METADATA_END,
    APP_VERSION_OFFSET,
    verify_application,
)


PROFILES = {
    "main": {
        "stack": 0x2000C000,
        "flash_size": 0x40000,
        "version": "mcu0_022_000",
    },
    "nozzle": {
        "stack": 0x20008000,
        "flash_size": 0x20000,
        "version": "noz0_019_000",
    },
    "bed": {
        "stack": 0x20002000,
        "flash_size": 0x10000,
        "version": "bed0_017_000",
    },
}

BASES = {"factory": 0x08003000, "katapult": 0x08002000}


def _checked_range(data, offset, size, description):
    if offset < 0 or size < 0 or offset + size > len(data):
        raise SystemExit("ELF %s is outside the file" % description)
    return data[offset:offset + size]


def _elf_layout(path):
    """Return (entry, .text VMA, .text LMA, .text bytes) from an ELF32.

    This deliberately avoids pyelftools so the release gate behaves the same
    in GitHub Actions, T113 WSL, and the Windows fallback environment.
    """
    data = path.read_bytes()
    if len(data) < 52 or data[:4] != b"\x7fELF":
        raise SystemExit("%s: not an ELF file" % path)
    if data[4] != 1 or data[5] != 1:
        raise SystemExit("%s: expected a 32-bit little-endian ELF" % path)

    header = struct.unpack_from("<16sHHIIIIIHHHHHH", data)
    (_ident, elf_type, machine, version, entry, phoff, shoff, _flags,
     ehsize, phentsize, phnum, shentsize, shnum, shstrndx) = header
    if elf_type != 2 or machine != 40 or version != 1:
        raise SystemExit("%s: expected an ARM executable ELF" % path)
    if ehsize < 52 or phentsize < 32 or shentsize < 40:
        raise SystemExit("%s: malformed ELF header sizes" % path)
    if not shnum or shstrndx >= shnum:
        raise SystemExit("%s: missing ELF section-name table" % path)

    sections = []
    for index in range(shnum):
        offset = shoff + index * shentsize
        raw = _checked_range(data, offset, 40, "section header %d" % index)
        sections.append(struct.unpack_from("<IIIIIIIIII", raw))

    names_section = sections[shstrndx]
    names = _checked_range(
        data, names_section[4], names_section[5], "section-name table")

    def section_name(name_offset):
        if name_offset >= len(names):
            raise SystemExit("%s: invalid ELF section name offset" % path)
        end = names.find(b"\0", name_offset)
        if end < 0:
            raise SystemExit("%s: unterminated ELF section name" % path)
        return names[name_offset:end].decode("ascii", "strict")

    text_sections = [
        section for section in sections if section_name(section[0]) == ".text"
    ]
    if len(text_sections) != 1:
        raise SystemExit("%s: expected exactly one .text section" % path)
    text = text_sections[0]
    _name, section_type, section_flags, text_vma, text_offset, text_size = text[:6]
    if section_type != 1 or not section_flags & 0x2:
        raise SystemExit("%s: .text is not an allocated program section" % path)
    text_data = _checked_range(data, text_offset, text_size, ".text data")

    load_addresses = []
    for index in range(phnum):
        offset = phoff + index * phentsize
        raw = _checked_range(data, offset, 32, "program header %d" % index)
        (segment_type, segment_offset, segment_vma, segment_lma,
         segment_file_size, _segment_memory_size, segment_flags,
         _segment_align) = struct.unpack_from("<IIIIIIII", raw)
        # Older GNU ARM linkers derive the output .text section flags from the
        # vector-table input and may omit SHF_EXECINSTR there.  The executable
        # property that matters at load time is PF_X on its PT_LOAD segment.
        if segment_type != 1 or not segment_flags & 0x1:
            continue
        delta = text_vma - segment_vma
        if (delta < 0 or delta + text_size > segment_file_size
                or segment_offset + delta != text_offset):
            continue
        load_addresses.append(segment_lma + delta)
    if not load_addresses:
        raise SystemExit("%s: .text is not mapped by a loadable segment" % path)
    if len(set(load_addresses)) != 1:
        raise SystemExit("%s: .text has ambiguous ELF load addresses" % path)
    return entry, text_vma, load_addresses[0], text_data


def validate(path, profile_name, layout, elf_path):
    profile = PROFILES[profile_name]
    image = path.read_bytes()
    if len(image) < APP_METADATA_END:
        raise SystemExit("%s: application is too short (%d bytes)"
                         % (path, len(image)))

    base = BASES[layout]
    maximum = profile["flash_size"] - (base - 0x08000000)
    if len(image) > maximum:
        raise SystemExit("%s: application size %d exceeds %s limit %d"
                         % (path, len(image), profile_name, maximum))

    stack, reset = struct.unpack_from("<II", image)
    if stack != profile["stack"]:
        raise SystemExit("%s: stack 0x%08x does not match 0x%08x"
                         % (path, stack, profile["stack"]))
    reset_code = reset & ~1
    if not reset & 1 or not base <= reset_code < base + len(image):
        raise SystemExit(
            "%s: reset vector 0x%08x is outside %s application range"
            % (path, reset, layout))

    version_raw = image[APP_VERSION_OFFSET:APP_VERSION_OFFSET + 12]
    try:
        version = version_raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise SystemExit("%s: application version is not ASCII" % path) from exc
    if version != profile["version"]:
        raise SystemExit("%s: version %r does not match %r"
                         % (path, version, profile["version"]))

    if layout == "factory":
        valid, _version, declared_length, _crc = verify_application(image)
        if declared_length != len(image):
            raise SystemExit(
                "%s: declared length %d does not equal file size %d"
                % (path, declared_length, len(image)))
        if not valid:
            raise SystemExit("%s: factory application CRC16 is invalid" % path)
    else:
        # Katapult owns application validation.  Ensure a stale factory length
        # was not accidentally retained when the image layout was converted.
        declared_length = int.from_bytes(
            image[APP_CHECK_OFFSET + 2:APP_METADATA_END], "little")
        if declared_length:
            raise SystemExit(
                "%s: Katapult image unexpectedly contains factory length %d"
                % (path, declared_length))

    entry, text_vma, text_lma, text_data = _elf_layout(elf_path)
    for label, actual in (
            ("entry", entry), (".text VMA", text_vma),
            (".text LMA", text_lma)):
        if actual != base:
            raise SystemExit(
                "%s: ELF %s 0x%08x does not match %s base 0x%08x"
                % (elf_path, label, actual, layout, base))
    if image[:len(text_data)] != text_data:
        raise SystemExit(
            "%s: ELF .text contents do not match the application binary"
            % elf_path)

    digest = hashlib.sha256(image).hexdigest()
    print(
        "%s app layout=%s base=0x%08x size=%d stack=0x%08x "
        "reset=0x%08x elf_entry=0x%08x text_vma=0x%08x "
        "text_lma=0x%08x version=%s sha256=%s"
        % (profile_name, layout, base, len(image), stack, reset, entry,
           text_vma, text_lma, version, digest))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=tuple(PROFILES))
    parser.add_argument("layout", choices=tuple(BASES))
    parser.add_argument("image", type=Path)
    parser.add_argument("elf", type=Path)
    args = parser.parse_args()
    validate(args.image, args.profile, args.layout, args.elf)


if __name__ == "__main__":
    main()
