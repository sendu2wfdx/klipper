#!/usr/bin/env python3
"""通过 ST-Link VCP 验证 GD32 独立 USART0 上的原厂 F7 从机协议。"""

import argparse
import pathlib
import sys
import time

import serial

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import creality_485_reference as protocol


def read_frame(port, timeout=1.0):
    deadline = time.monotonic() + timeout
    data = bytearray()
    expected = None
    while time.monotonic() < deadline:
        chunk = port.read(64)
        if chunk:
            data.extend(chunk)
        while data and data[0] != protocol.HEAD:
            del data[0]
        if len(data) >= 3:
            expected = data[2] + 3
        if expected is not None and len(data) >= expected:
            frame = bytes(data[:expected])
            protocol.decode(frame)
            return frame
    raise TimeoutError(f"等待 F7 应答超时，已收到：{data.hex(' ')}")


def exchange(port, request, timeout=1.0):
    port.write(request)
    port.flush()
    return read_frame(port, timeout)


def require_identity(frame, function, address, mode=0):
    got_address, state, got_function, payload = protocol.decode(frame)
    if isinstance(address, tuple):
        assert got_address in address
    else:
        assert got_address == address
    assert state == 0
    assert got_function == function
    assert payload == bytes((2, mode)) + b"GD32F303CCT6"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM11")
    parser.add_argument("--baud", type=int, default=230400)
    args = parser.parse_args()

    with serial.Serial(args.port, args.baud, timeout=0.03,
                       write_timeout=1.0) as port:
        port.reset_input_buffer()
        port.reset_output_buffer()

        # 噪声同步及分类广播发现。
        port.write(b"\x00\x55\xaa")
        frame = exchange(port, protocol.discovery_frame("motor"))
        # A freshly reset test image answers from its group address.  A second
        # probe without resetting the MCU retains the already assigned 0x85.
        require_identity(frame, protocol.FUNC_DISCOVER, (0xFD, 0x85))

        uuid = b"GD32F303CCT6"
        frame = exchange(port, protocol.set_slave_address_frame(
            "motor", 0x85, uuid))
        require_identity(frame, protocol.FUNC_SET_SLAVE_ADDRESS, 0x85)

        frame = exchange(port, protocol.online_check_frame(0x85))
        require_identity(frame, protocol.FUNC_ONLINE_CHECK, 0x85)
        frame = exchange(port, protocol.get_address_table_frame(0x85))
        require_identity(frame, protocol.FUNC_GET_ADDRESS_TABLE, 0x85)

        frame = exchange(port, protocol.upgrade_command_frame(
            0x85, protocol.UPGRADE_GET_VERSION))
        assert protocol.decode(frame)[3] == b"test_485_001-test_485_001"
        frame = exchange(port, protocol.upgrade_command_frame(
            0x85, protocol.UPGRADE_GET_SECTOR_SIZE))
        assert protocol.decode(frame)[3] == b"\x02"

        # 桌面测试固件没有 Flash 写回后端，必须明确拒绝擦写，而不是
        # 假装升级成功。
        frame = exchange(port, protocol.upgrade_command_frame(
            0x85, protocol.UPGRADE_REQUEST))
        assert protocol.decode(frame)[3] == b"\x21"

        # 坏 CRC 不应得到应答，随后一个好帧必须能重新同步。
        damaged = bytearray(protocol.online_check_frame(0x85))
        damaged[-1] ^= 1
        port.write(damaged)
        port.flush()
        time.sleep(0.08)
        assert not port.read(1)
        frame = exchange(port, protocol.online_check_frame(0x85))
        require_identity(frame, protocol.FUNC_ONLINE_CHECK, 0x85)

    print("GD32F303CCT6 独立 USART0 原厂 F7 从机协议实机验收通过")
    print("port=%s baud=%d address=0x85 uuid=GD32F303CCT6" %
          (args.port, args.baud))
    print("discovery=A1 address=A0 online=A2 table=A3 upgrade=F0 crc_recovery=ok")


if __name__ == "__main__":
    main()
