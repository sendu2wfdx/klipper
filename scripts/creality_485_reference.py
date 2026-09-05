#!/usr/bin/env python3
"""Ender-3 V4 原厂 RS-485 帧格式的可执行参考模型。"""

HEAD = 0xF7

# mcu_util_485 中的设备类别广播地址。
DEVICE_GROUPS = {
    "cfs": 0xFE,
    "rfid": 0xFB,
    "belt": 0xFC,
    "motor": 0xFD,
}

# 原厂扫描后为各类设备分配地址时使用的起始值。
DEVICE_ADDRESS_BASES = {
    "cfs": 0x01,
    "rfid": 0x11,
    "belt": 0x23,
    "motor": 0x85,
}

FUNC_SET_SLAVE_ADDRESS = 0xA0
FUNC_DISCOVER = 0xA1
FUNC_ONLINE_CHECK = 0xA2
FUNC_GET_ADDRESS_TABLE = 0xA3
FUNC_LOADER_TO_APP = 0x0B
FUNC_UPGRADE = 0xF0

# HiKlipper auto_addr_wrapper.py 中明确公开的运行期设备类型。
DEVICE_TYPE_BOX = 1
DEVICE_TYPE_CLOSED_LOOP_MOTOR = 2
DEVICE_TYPE_BELT_MOTOR = 3
DEVICE_MODES = {"app": 0, "loader": 1}

UPGRADE_GET_VERSION = 0x00
UPGRADE_REQUEST = 0x01
UPGRADE_START_APP = 0x02
UPGRADE_GET_SECTOR_SIZE = 0x03
UPGRADE_ERASE_FLASH = 0x06

UPGRADE_ACK_CONTINUE = 0x75
UPGRADE_ACK_FINISHED = 0x20


def crc8(data):
    crc = 0
    for value in data:
        crc ^= value
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 \
                else (crc << 1) & 0xFF
    return crc


def encode(address, state, function, payload=b""):
    body_len = len(payload) + 3
    if body_len > 255:
        raise ValueError("payload too long")
    frame = bytearray((HEAD, address, body_len, state, function))
    frame.extend(payload)
    frame.append(crc8(frame[2:]))
    return bytes(frame)


def decode(frame):
    if len(frame) < 6 or frame[0] != HEAD:
        raise ValueError("invalid frame header")
    expected = frame[2] + 3
    if frame[2] < 3 or len(frame) != expected:
        raise ValueError("invalid frame length")
    if crc8(frame[2:-1]) != frame[-1]:
        raise ValueError("invalid frame crc")
    return frame[1], frame[3], frame[4], bytes(frame[5:-1])


def discovery_frame(device_class):
    """生成原厂分类发现广播：类别地址在载荷中重复两次。"""
    group = DEVICE_GROUPS[device_class.lower()]
    return encode(group, 0, FUNC_DISCOVER, bytes((group, group)))


def set_slave_address_frame(device_class, new_address, uuid):
    """按原厂 A0 命令，以 1 字节新地址加 12 字节 UUID 分配地址。"""
    uuid = bytes(uuid)
    if len(uuid) != 12:
        raise ValueError("uuid must be exactly 12 bytes")
    if not 0 <= new_address <= 0xFF:
        raise ValueError("invalid slave address")
    group = DEVICE_GROUPS[device_class.lower()]
    return encode(group, 0, FUNC_SET_SLAVE_ADDRESS,
                  bytes((new_address,)) + uuid)


def _parse_address_record(frame, accepted_functions):
    """解析自动寻址应答共有的 dev_type/mode/UniID 布局。"""
    address, state, function, payload = decode(frame)
    if function not in accepted_functions:
        raise ValueError("not an address-management frame")
    if len(payload) < 14:
        raise ValueError("address-management payload too short")
    return {
        "address": address,
        "state": state,
        "function": function,
        "device_type": payload[0],
        "mode": payload[1],
        "uuid": payload[2:14],
    }


def parse_slave_info(frame):
    """解析 A1 发现应答的设备类型、app/loader 模式和 12 字节 UniID。"""
    return _parse_address_record(frame, {FUNC_DISCOVER})


def parse_online_reply(frame):
    """解析 A2 在线检查应答；字段布局与 A1 相同。"""
    return _parse_address_record(frame, {FUNC_ONLINE_CHECK})


def parse_address_table_reply(frame):
    """解析 A3 地址表应答；主机用帧地址覆盖该槽位保存的 UniID。"""
    return _parse_address_record(frame, {FUNC_GET_ADDRESS_TABLE})


def parse_set_address_reply(frame):
    """提取 A0 应答用于匹配原记录的地址与 12 字节 UUID。"""
    address, state, function, payload = decode(frame)
    if function != FUNC_SET_SLAVE_ADDRESS:
        raise ValueError("not a set-address reply")
    if len(payload) < 14:
        raise ValueError("set-address reply payload too short")
    return address, state, payload[2:14]


def online_check_frame(address):
    """生成 A2 单播在线检查；原厂请求载荷为空。"""
    return encode(address, 0, FUNC_ONLINE_CHECK)


def get_address_table_frame(address):
    """生成 A3 单播地址表查询；原厂请求载荷为空。"""
    return encode(address, 0, FUNC_GET_ADDRESS_TABLE)


def loader_to_app_frame():
    """生成 0B 广播命令；载荷 01 要求处于 loader 的从机启动 app。"""
    return encode(0xFF, 0, FUNC_LOADER_TO_APP, b"\x01")


def upgrade_command_frame(address, command):
    """生成 F0 升级控制命令帧。"""
    if command not in {
            UPGRADE_GET_VERSION, UPGRADE_REQUEST, UPGRADE_START_APP,
            UPGRADE_GET_SECTOR_SIZE, UPGRADE_ERASE_FLASH}:
        raise ValueError("unknown factory upgrade command")
    return encode(address, 0, FUNC_UPGRADE, bytes((command,)))


def upgrade_length_frame(address, application_length):
    """生成原厂 app_len 阶段的 4 字节小端长度帧。"""
    if not 0 <= application_length <= 0xFFFFFFFF:
        raise ValueError("invalid application length")
    return encode(address, 0, FUNC_UPGRADE,
                  application_length.to_bytes(4, "little"))


def upgrade_data_frame(address, data):
    """生成安全可解析的数据帧；F7 的单字节 LEN 将载荷限制为 252 字节。"""
    return encode(address, 0, FUNC_UPGRADE, bytes(data))


def interpret_upgrade_reply(stage, payload):
    """按原厂 F0 接收状态机解释载荷，不混入 UART BL 的补码校验。"""
    payload = bytes(payload)
    if stage == "get_version":
        if len(payload) != 25:
            raise ValueError("version reply must be 25 bytes")
        return payload
    if stage == "get_sector_size":
        if len(payload) != 1:
            raise ValueError("sector-size reply must be one byte")
        value = payload[0]
        signed = value if value < 0x80 else value - 0x100
        return signed * 1024 if signed > 0 else -signed * 4
    if stage in {"erase_flash", "update_request", "app_len", "start_app"}:
        if len(payload) != 1:
            raise ValueError("control reply must be one byte")
        return payload[0] == UPGRADE_ACK_CONTINUE
    if stage == "app_data":
        if len(payload) != 1:
            raise ValueError("data reply must be one byte")
        if payload[0] == UPGRADE_ACK_CONTINUE:
            return "continue"
        if payload[0] == UPGRADE_ACK_FINISHED:
            return "finished"
        return "error"
    raise ValueError("unknown upgrade stage")
