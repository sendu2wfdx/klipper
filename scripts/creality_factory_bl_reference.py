#!/usr/bin/env python3
"""创想三维工具板原厂 Bootloader 串口协议参考实现。

该协议与运行期 F7/CRC8 协议相互独立。除最初的 0x75 裸握手外，
命令、应答及数据块均使用 8 位累加和的一补数作为末尾校验字节。
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import NamedTuple


HANDSHAKE = b"\x75"

BOOTLOADER_COMMANDS = {
    "get_version": bytes.fromhex("00 ff"),
    "update_request": bytes.fromhex("01 fe"),
    "start_app": bytes.fromhex("02 fd"),
    "get_sector_size": bytes.fromhex("03 fc"),
}

# 由桥接设备处理，不属于三份目标 MCU Bootloader 的 0..3 分派表。
TRANSPARENT_COMMANDS = {
    "enter_transparent": bytes.fromhex("04 fb"),
    "exit_transparent": bytes.fromhex("05 fa"),
}

COMMANDS = {**BOOTLOADER_COMMANDS, **TRANSPARENT_COMMANDS}

ACK = 0x75
BLOCK_OK = 0x20
BLOCK_CHECKSUM_ERROR = 0x1F
BLOCK_FLASH_ERROR = 0x21

FACTORY_PROFILES = {
    "CR1FN240306C13": {
        "mcu": "gd32f303",
        "sector_code": 2,
        "block_size": 2048,
        "flash_size": 128 * 1024,
    },
    "CR4NU200360C23": {
        "mcu": "gd32f303",
        "sector_code": 2,
        "block_size": 2048,
        "flash_size": 256 * 1024,
    },
    "CR0NN200360C10": {
        "mcu": "gd32e230",
        "sector_code": 1,
        "block_size": 1024,
        "flash_size": 64 * 1024,
    },
}

APP_FLASH_OFFSET = 0x3000
APP_VERSION_OFFSET = 0x200
APP_CHECK_OFFSET = 0x20C
APP_METADATA_END = 0x212


def checksum(data: bytes) -> int:
    """返回原厂协议校验：0xff - (sum(data) & 0xff)。"""
    return 0xFF - (sum(data) & 0xFF)


def crc16_ccitt(data: bytes) -> int:
    """原厂 BL 镜像校验：CRC-16/CCITT，poly=0x1021，init=0。"""
    crc = 0
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def finalize_application(image: bytes, version: str) -> bytes:
    """写入原厂 12 字节版本、总长度和 CRC16，生成 BL 可验证的应用。"""
    try:
        version_raw = version.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("应用版本必须是 ASCII") from exc
    if len(version_raw) != 12:
        raise ValueError("应用版本必须恰好为 12 字节")
    if len(image) < APP_METADATA_END:
        raise ValueError("应用镜像不足以容纳原厂元数据")
    if len(image) > 0xFFFFFFFF:
        raise ValueError("应用镜像长度超出 uint32 范围")

    result = bytearray(image)
    result[APP_VERSION_OFFSET:APP_VERSION_OFFSET + 12] = version_raw
    result[APP_CHECK_OFFSET:APP_METADATA_END] = bytes(6)
    result[APP_CHECK_OFFSET + 2:APP_METADATA_END] = len(result).to_bytes(4, "little")
    # BL 校验时 CRC 和长度共 6 字节均按零处理，而不是保留长度参与 CRC。
    crc_input = bytearray(result)
    crc_input[APP_CHECK_OFFSET:APP_METADATA_END] = bytes(6)
    result[APP_CHECK_OFFSET:APP_CHECK_OFFSET + 2] = crc16_ccitt(crc_input).to_bytes(2, "little")
    return bytes(result)


def verify_application(image: bytes) -> tuple[bool, str, int, int]:
    """返回（校验是否通过、版本、声明长度、保存的 CRC16）。"""
    if len(image) < APP_METADATA_END:
        raise ValueError("应用镜像不足以容纳原厂元数据")
    expected = int.from_bytes(image[APP_CHECK_OFFSET:APP_CHECK_OFFSET + 2], "little")
    length = int.from_bytes(image[APP_CHECK_OFFSET + 2:APP_METADATA_END], "little")
    if length < APP_METADATA_END or length > len(image):
        return False, "", length, expected
    crc_input = bytearray(image[:length])
    crc_input[APP_CHECK_OFFSET:APP_METADATA_END] = bytes(6)
    valid = crc16_ccitt(crc_input) == expected
    version = image[APP_VERSION_OFFSET:APP_VERSION_OFFSET + 12].decode("ascii", "replace")
    return valid, version, length, expected


def packet(data: bytes) -> bytes:
    """给任意载荷追加原厂校验字节。"""
    return data + bytes((checksum(data),))


def verify_packet(frame: bytes, payload_length: int | None = None) -> bytes:
    """校验并返回载荷；payload_length 可用于严格检查固定长度应答。"""
    if len(frame) < 1:
        raise ValueError("数据包缺少校验字节")
    payload = frame[:-1]
    if payload_length is not None and len(payload) != payload_length:
        raise ValueError(
            f"载荷长度应为 {payload_length}，实际为 {len(payload)}"
        )
    if frame[-1] != checksum(payload):
        raise ValueError("校验错误")
    return payload


def command(name: str) -> bytes:
    """取得原厂两字节命令。"""
    try:
        return COMMANDS[name]
    except KeyError as exc:
        raise ValueError(f"未知命令：{name}") from exc


def parse_single_byte_response(frame: bytes) -> int:
    """解析 ACK、扇区参数或数据块状态等一字节应答。"""
    return verify_packet(frame, 1)[0]


def parse_ack(frame: bytes) -> None:
    """解析数据字节为 0x75 的确认包（线缆字节为 75 8a）。"""
    value = parse_single_byte_response(frame)
    if value != ACK:
        raise ValueError(f"期待 ACK 0x75，实际为 0x{value:02x}")


def parse_version(frame: bytes) -> str:
    """解析固定 25 字节的原厂版本字段。"""
    raw = verify_packet(frame, 25)
    # mcu_util 只保留字母、数字、'-'、'_'，其他字符显示/保存为 '0'。
    normalized = bytes(
        value
        if chr(value).isalnum() and value < 0x80 or value in (0x2D, 0x5F)
        else 0x30
        for value in raw
    )
    return normalized.decode("ascii")


def decode_sector_size(code: int) -> int:
    """按 mcu_util 的 int8 规则将扇区参数换算为字节数。"""
    if not 0 <= code <= 0xFF:
        raise ValueError("扇区参数必须是一个字节")
    signed = code if code < 0x80 else code - 0x100
    if signed > 0:
        return signed * 1024
    if signed < 0:
        return -signed * 4
    raise ValueError("扇区参数 0 无效")


def encode_application_length(length: int) -> bytes:
    """编码小端 32 位应用长度和校验。"""
    if not 0 <= length <= 0xFFFFFFFF:
        raise ValueError("应用长度超出 uint32 范围")
    return packet(length.to_bytes(4, "little"))


def encode_data_block(data: bytes) -> bytes:
    """编码一个固件数据块。"""
    if not data:
        raise ValueError("固件数据块不可为空")
    if len(data) > 0x4400:
        raise ValueError("超过原厂主机单块缓冲上限 0x4400")
    return packet(data)


def validate_application_size(length: int, profile: str) -> int:
    """按备份芯片容量限制应用长度，返回该板型允许的最大长度。"""
    try:
        flash_size = FACTORY_PROFILES[profile]["flash_size"]
    except KeyError as exc:
        raise ValueError(f"未知原厂板型：{profile}") from exc
    maximum = flash_size - APP_FLASH_OFFSET
    if length < APP_METADATA_END:
        raise ValueError("应用长度不足以包含原厂元数据")
    if length > maximum:
        raise ValueError(
            f"应用长度 {length} 超过 {profile} 上限 {maximum}"
        )
    return maximum


def iter_update_blocks(image: bytes, profile: str):
    """按目标 BL 的原厂块长生成带校验的升级数据块。"""
    validate_application_size(len(image), profile)
    block_size = FACTORY_PROFILES[profile]["block_size"]
    for offset in range(0, len(image), block_size):
        yield encode_data_block(image[offset:offset + block_size])


class BlockResult(NamedTuple):
    value: int
    name: str
    retry: bool
    fatal: bool


def parse_block_result(frame: bytes) -> BlockResult:
    """解释原厂 BL 对固件块的应答。"""
    value = parse_single_byte_response(frame)
    if value == ACK:
        return BlockResult(value, "continue", retry=False, fatal=False)
    if value == BLOCK_OK:
        return BlockResult(value, "ok", retry=False, fatal=False)
    if value == BLOCK_CHECKSUM_ERROR:
        return BlockResult(value, "checksum_error", retry=True, fatal=False)
    if value == BLOCK_FLASH_ERROR:
        return BlockResult(value, "flash_error", retry=False, fatal=True)
    return BlockResult(value, "unknown", retry=False, fatal=True)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="创想三维原厂 UART Bootloader 镜像工具")
    commands = parser.add_subparsers(dest="action", required=True)

    verify = commands.add_parser("verify", help="验证并显示原厂应用镜像元数据")
    verify.add_argument("image", type=Path)

    finalize = commands.add_parser("finalize", help="写入版本、长度和 CRC16")
    finalize.add_argument("input", type=Path)
    finalize.add_argument("output", type=Path)
    finalize.add_argument("--version", required=True, help="恰好 12 个 ASCII 字节")
    finalize.add_argument("--profile", required=True, choices=sorted(FACTORY_PROFILES))

    split = commands.add_parser("split", help="生成原厂 BL 数据块文件")
    split.add_argument("image", type=Path)
    split.add_argument("output_dir", type=Path)
    split.add_argument("--profile", required=True, choices=sorted(FACTORY_PROFILES))
    return parser


def main(argv=None) -> int:
    args = _build_arg_parser().parse_args(argv)
    if args.action == "verify":
        valid, version, length, crc = verify_application(args.image.read_bytes())
        print(
            f"valid={str(valid).lower()} version={version} "
            f"length={length} crc16={crc:04X}"
        )
        return 0 if valid else 2

    if args.action == "finalize":
        raw = args.input.read_bytes()
        validate_application_size(len(raw), args.profile)
        result = finalize_application(raw, args.version)
        args.output.write_bytes(result)
        print(f"已生成 {args.output}，{len(result)} 字节，目标 {args.profile}")
        return 0

    image = args.image.read_bytes()
    blocks = list(iter_update_blocks(image, args.profile))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for index, frame in enumerate(blocks):
        (args.output_dir / f"block_{index:04d}.bin").write_bytes(frame)
    print(f"已生成 {len(blocks)} 个升级块到 {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
