#!/usr/bin/env python3
"""核对 V57 PRTouch V3 主机源码与已构建 MCU 字典的完整 ABI。"""

import argparse
import ast
import json
import re
from pathlib import Path


COMMANDS = (
    "config_prtouch_pres oid=%c idx=%c swp_pin=%u clk_pin=%u sdo_pin=%u",
    "start_prtouch_pres oid=%c cfg_regs=%c acq_tick=%u ned_tftr=%c "
    "ned_hftr=%c ned_lftr=%u min_hold=%i max_hold=%i add_hold=%i "
    "lmt_dead=%u",
    "stop_prtouch_pres oid=%c sta_swap=%c",
    "read_prtouch_pres oid=%c is_src=%c ch=%c idx=%c len=%c",
    "config_prtouch_step oid=%c oid_xstp=%u oid_ystp=%u oid_zstp=%u "
    "swp_pin=%u",
    "start_prtouch_step oid=%c aqc_tick=%u",
    "stop_prtouch_step oid=%c",
    "read_prtouch_step oid=%c idx=%c len=%c",
    "cont_prtouch_step oid=%c",
    "config_prtouch_apax oid=%c oid_estp=%c",
    "start_prtouch_apax oid=%c cfg_regs=%c acq_tick=%u",
    "stop_prtouch_apax oid=%c",
)

RESPONSES = (
    "ack_prtouch oid=%c err=%c expar0=%u expar1=%u",
    "resault_prtouch_pres oid=%c tri_chxs=%c buf_len=%c ch=%c idx=%c "
    "len=%c ticks=%.*s datas=%.*s",
    "resault_prtouch_step oid=%c buf_len=%c idx=%c len=%c ticks=%.*s "
    "datas=%.*s",
    "resault_prtouch_step_cnt oid=%c cnt_x=%i cnt_y=%i cnt_z=%i",
    "resault_prtouch_apax oid=%c ch=%c len=%c ticks=%.*s datas=%.*s "
    "espds=%.*s",
)


def python_string_constants(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return tuple(node.value for node in ast.walk(tree)
                 if isinstance(node, ast.Constant)
                 and isinstance(node.value, str))


def host_has_contract(strings, signature):
    """配置命令的 Python 模板类型不同，但字段名和顺序必须完全相同。"""
    name = signature.split(" ", 1)[0]
    fields = re.findall(r"\b([A-Za-z0-9_]+)=", signature)
    for value in strings:
        if value == signature:
            return True
        if value.startswith(name + " "):
            if re.findall(r"\b([A-Za-z0-9_]+)=", value) == fields:
                return True
    # APAX/连续压力数据由 MCU 主动上报，主机只按消息名注册回调。
    return name.startswith("resault_") and name in strings


def verify(host_path, dictionary_path):
    host_strings = python_string_constants(host_path)
    dictionary = json.loads(dictionary_path.read_text(encoding="utf-8"))
    mcu_commands = dictionary.get("commands", {})
    mcu_responses = dictionary.get("responses", {})
    errors = []
    for kind, signatures, mcu_table in (
            ("命令", COMMANDS, mcu_commands),
            ("响应", RESPONSES, mcu_responses)):
        for signature in signatures:
            if not host_has_contract(host_strings, signature):
                errors.append("主机缺少%s：%s" % (kind, signature))
            if signature not in mcu_table:
                errors.append("MCU 字典缺少%s：%s" % (kind, signature))
    if errors:
        raise SystemExit("\n".join(errors))
    return len(COMMANDS), len(RESPONSES)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", type=Path,
                        help="PRTouch V3 主机模块路径")
    parser.add_argument("dictionary", type=Path,
                        help="已构建的 klipper.dict 路径")
    args = parser.parse_args()
    commands, responses = verify(args.host, args.dictionary)
    print("PRTouch V3 主机/MCU ABI 一致：%d 条命令，%d 条响应。" %
          (commands, responses))


if __name__ == "__main__":
    main()
