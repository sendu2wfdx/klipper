#!/usr/bin/env python3
"""核对 PRTouch V3 MCU 对象的原厂 BSS 状态布局。"""

import argparse
import re
import subprocess
from pathlib import Path


EXPECTED_GLOBALS = {
    "pr_step": 0x21C,
    "pr_apax": 0x798,
    "pr_pres": 0x1324,
}
EXPECTED_CODE_SYMBOLS = {
    "command_config_prtouch_apax": 0x10,
    "prtouch_task": 0x12,
    "command_stop_prtouch_step": 0x38,
    "command_stop_prtouch_pres": 0x40,
    "prtouch_step_task": 0x74,
}
EXPECTED_QUERY_WORKSPACES = 4
QUERY_WORKSPACE_SIZE = 0xA0


def parse_nm(text):
    symbols = []
    pattern = re.compile(
        r"^[0-9a-fA-F]+\s+([0-9a-fA-F]+)\s+([bBtT])\s+(\S+)$")
    for line in text.splitlines():
        match = pattern.match(line.strip())
        if match:
            # GCC LTO renames file-local symbols in the linked ELF.  Strip the
            # linker-only suffix so the persistent workspace layout can still
            # be checked after LTO has materialised the final program.
            name = re.sub(r"\.lto_priv\.\d+$", "", match.group(3))
            symbols.append((name, int(match.group(1), 16),
                            match.group(2)))
    return symbols


def read_symbols(nm, path):
    result = subprocess.run(
        [nm, "-S", "--size-sort", str(path)], check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return parse_nm(result.stdout)


def verify(symbols, label, check_code_sizes=True):
    sizes = {name: size for name, size, _kind in symbols}
    errors = []
    for name, expected in EXPECTED_GLOBALS.items():
        actual = sizes.get(name)
        if actual != expected:
            errors.append(
                "%s: %s 尺寸为 %s，期望 0x%x" %
                (label, name, "缺失" if actual is None else hex(actual),
                 expected))
    if check_code_sizes:
        for name, expected in EXPECTED_CODE_SYMBOLS.items():
            actual = sizes.get(name)
            if actual != expected:
                errors.append(
                    "%s: %s 代码尺寸为 %s，期望 0x%x" %
                    (label, name, "缺失" if actual is None else hex(actual),
                     expected))
    workspaces = [
        (name, size) for name, size, _kind in symbols
        if size == QUERY_WORKSPACE_SIZE
        and ("zip_tick" in name or "zip_data" in name)
    ]
    if len(workspaces) != EXPECTED_QUERY_WORKSPACES:
        errors.append(
            "%s: 160 字节查询压缩工作区为 %d 个，期望 %d 个" %
            (label, len(workspaces), EXPECTED_QUERY_WORKSPACES))
    if errors:
        raise ValueError("\n".join(errors))
    return workspaces


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    rebuilt = parser.add_mutually_exclusive_group(required=True)
    rebuilt.add_argument("--rebuilt", type=Path,
                         help="重建的非 LTO prtouch_v3.o")
    rebuilt.add_argument("--rebuilt-elf", type=Path,
                         help="重建的 LTO 最终 ELF；只核对持久状态布局")
    parser.add_argument("--factory", type=Path,
                        help="可选的原厂 prtouch_v3.o")
    parser.add_argument("--nm", default="arm-none-eabi-nm",
                        help="ARM nm 程序（默认 arm-none-eabi-nm）")
    args = parser.parse_args()

    targets = []
    if args.factory:
        targets.append(("原厂对象", args.factory, True))
    if args.rebuilt:
        targets.append(("重建对象", args.rebuilt, True))
    else:
        targets.append(("重建 LTO ELF", args.rebuilt_elf, False))
    for label, path, check_code_sizes in targets:
        workspaces = verify(read_symbols(args.nm, path), label,
                            check_code_sizes=check_code_sizes)
        if check_code_sizes:
            print("%s：3 个主状态块、%d 个查询压缩工作区和 %d 个关键函数尺寸一致。" %
                  (label, len(workspaces), len(EXPECTED_CODE_SYMBOLS)))
        else:
            print("%s：3 个主状态块和 %d 个查询压缩工作区一致；函数尺寸因 LTO 不作对象级比较。" %
                  (label, len(workspaces)))


if __name__ == "__main__":
    main()
