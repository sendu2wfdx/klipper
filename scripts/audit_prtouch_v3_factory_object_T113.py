#!/usr/bin/env python3
"""只读核验原厂 PRTouch V3 对象的采样分支与下降沿触发语义。"""

import argparse
import hashlib
import re
import subprocess
from pathlib import Path


EXPECTED_SYMBOL_SIZES = {
    "pr_pres": 4900,
    "pr_step": 540,
    "pr_apax": 1944,
    "pres_get_datas": 320,
    "pres_tri_check": 624,
    "command_config_prtouch_pres": 160,
    "prtouch_pres_task": 452,
}
REQUIRED_ADC_IMPORTS = {
    "gpio_adc_setup", "gpio_adc_sample", "gpio_adc_read",
}


def parse_symbols(text):
    symbols = {}
    pattern = re.compile(
        r"^\s*\d+:\s+[0-9a-fA-F]+\s+(\d+)\s+(\S+)\s+"
        r"\S+\s+\S+\s+(\S+)\s+(\S+)\s*$")
    for line in text.splitlines():
        match = pattern.match(line)
        if match:
            size, kind, section, name = match.groups()
            symbols[name] = {
                "size": int(size), "kind": kind, "section": section,
            }
    return symbols


def extract_section(text, name):
    marker = "Disassembly of section %s:" % name
    start = text.find(marker)
    if start < 0:
        raise ValueError("反汇编缺少节：%s" % name)
    next_section = text.find("Disassembly of section ", start + len(marker))
    return text[start:] if next_section < 0 else text[start:next_section]


def require(text, fragment, label):
    if fragment not in text:
        raise ValueError("%s 缺少证据：%s" % (label, fragment))


def verify(symbol_text, disassembly):
    symbols = parse_symbols(symbol_text)
    for name, expected in EXPECTED_SYMBOL_SIZES.items():
        actual = symbols.get(name, {}).get("size")
        if actual != expected:
            raise ValueError("%s 尺寸为 %r，期望 %d" %
                             (name, actual, expected))
    missing = sorted(REQUIRED_ADC_IMPORTS - set(symbols))
    if missing:
        raise ValueError("原厂对象缺少片上 ADC 导入：%s" % ", ".join(missing))

    config = extract_section(disassembly, ".text.command_config_prtouch_pres")
    acquire = extract_section(disassembly, ".text.pres_get_datas")
    task = extract_section(disassembly, ".text.prtouch_pres_task")
    trigger = extract_section(disassembly, ".text.pres_tri_check")

    # args[3] 与 args[4] 相等时进入 gpio_adc_setup；不等时建立 CLK/DOUT。
    require(config, "ldrd\tr0, r3, [r4, #12]", "压力通道配置")
    require(config, "cmp\tr0, r3", "压力通道配置")
    require(config, "R_ARM_THM_CALL\tgpio_adc_setup", "压力通道配置")
    require(config, "R_ARM_THM_CALL\tgpio_out_setup", "压力通道配置")
    require(config, "R_ARM_THM_CALL\tgpio_in_setup", "压力通道配置")

    # use_adcx 非零时最多轮询 502 次并读取片上 ADC；为零时走 27 时钟数字 ADC。
    require(acquire, "#502", "压力采样")
    if acquire.count("R_ARM_THM_CALL\tgpio_adc_sample") != 2:
        raise ValueError("片上 ADC 完成轮询调用数不等于 2")
    if acquire.count("R_ARM_THM_CALL\tgpio_adc_read") != 1:
        raise ValueError("片上 ADC 结果读取调用数不等于 1")
    require(acquire, "#27", "CS1237 数据读取")
    require(acquire, "R_ARM_THM_CALL\tgpio_in_read", "CS1237 数据读取")
    require(acquire, "R_ARM_THM_CALL\tgpio_out_write", "CS1237 数据读取")

    # 任务在片上 ADC 模式使用 acq_tick 节流，数字 ADC 模式按 DRDY 轮询。
    require(task, "ldr\tr3, [r6, #4]", "压力任务模式分支")
    require(task, "R_ARM_THM_CALL\tpres_get_datas", "压力任务采样")
    require(task, "R_ARM_THM_CALL\tpres_csx_w_cfg", "CS1237 配置写")
    require(task, "R_ARM_THM_CALL\tpres_csx_r_cfg", "CS1237 配置读")

    # 下降沿只把栈上的五点 edge[] 取负一次。后续历史保护直接载入
    # filtered[0..60] 与 edge[2] 比较，不能再次按下降沿改变历史符号。
    if trigger.count("negs\tr2, r2") != 1:
        raise ValueError("下降沿五点窗口取负次数不等于 1")
    edge_neg = trigger.index("negs\tr2, r2")
    history_begin = trigger.index("adds\tr4, #240")
    history_load = trigger.index("ldr.w\tr3, [r5, #4]!")
    history_cmp = trigger.index("cmp\tr2, r3", history_load)
    if not edge_neg < history_begin < history_load < history_cmp:
        raise ValueError("下降沿历史保护的原始符号比较顺序异常")
    return symbols


def run(command):
    return subprocess.run(command, check=True, text=True,
                          stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE).stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    default_object = Path(__file__).resolve().parents[2] / \
        "Hi_Klipper" / "src" / "prtouch_v3.o"
    parser.add_argument("--factory", type=Path, default=default_object)
    parser.add_argument("--readelf", default="arm-none-eabi-readelf")
    parser.add_argument("--objdump", default="arm-none-eabi-objdump")
    args = parser.parse_args()

    factory = args.factory.resolve()
    symbol_text = run([args.readelf, "-sW", str(factory)])
    disassembly = run([
        args.objdump, "-dr",
        "-j", ".text.command_config_prtouch_pres",
        "-j", ".text.pres_get_datas",
        "-j", ".text.pres_tri_check",
        "-j", ".text.prtouch_pres_task",
        str(factory),
    ])
    verify(symbol_text, disassembly)
    digest = hashlib.sha256(factory.read_bytes()).hexdigest()
    print("factory_object=%s" % factory)
    print("factory_size=%d" % factory.stat().st_size)
    print("factory_sha256=%s" % digest)
    print("state_layout=pr_pres:4900 pr_step:540 pr_apax:1944")
    print("adc_mode=pin3_equals_pin4 gpio_adc_setup/sample/read poll_limit:502")
    print("cs1237_mode=pin3_differs_pin4 gpio_clk/dout read_bits:27")
    print("falling_edge=five_edge_points_negated history_0_60_keeps_signed_values")
    print("result=PASS 原厂 PRTouch V3 的双采样与下降沿触发证据闭合")


if __name__ == "__main__":
    main()
