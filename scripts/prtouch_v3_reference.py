#!/usr/bin/env python3
"""可离线回放的 PRTouch V3 原厂算法参考模型。

只实现已经由反汇编直接确认的行为，用于对 MCU 兼容层做离线向量验证。
"""


def signed32(value):
    value &= 0xffffffff
    return value - 0x100000000 if value & 0x80000000 else value


def nearest_delta_sample(previous, samples):
    """复现原厂 pres_hftr_cal()：选择与前值差最小的候选样本。"""
    if not samples:
        return previous
    best_index = 0
    best_delta = 0x00ffffff
    for index, value in enumerate(samples):
        delta = signed32(value - previous)
        magnitude = signed32(-delta) if delta < 0 else delta
        if magnitude < best_delta:
            best_delta = magnitude
            best_index = index
    return samples[best_index]


def low_pass(previous, current, coefficient_thousandths):
    """复现 ned_lftr 的千分系数一阶低通，结果按 C 语言向零取整。"""
    alpha = coefficient_thousandths / 1000.0
    return int(previous * (1.0 - alpha) + current * alpha)


def high_pass_step(raw_sample_20, high_pass_history, accumulator):
    """复现原厂 21 点延迟线及 16 点滑动和的一次更新。"""
    if len(high_pass_history) != 21:
        raise ValueError("high_pass_history 必须正好包含 21 点")
    oldest = high_pass_history[0]
    history = list(high_pass_history[1:]) + [raw_sample_20]
    # 反汇编显示累计值在整段移位之后才读 hftr[15]，
    # 因此加入的是旧 hftr[16]，不是旧 hftr[15]。
    accumulator = signed32(accumulator - oldest + history[15])
    history[16] = nearest_delta_sample(history[15], history[16:21])
    return history, accumulator


def zip_data(values):
    """复现 prtouch_write_zip/prtouch_read_zip 的最终线格式。"""
    values = [value for value in values if value != -0x80000000]
    count = len(values)
    groups = (count + 3) // 4
    selectors = [0] * groups
    payload = bytearray()
    previous = 0
    for index, value in enumerate(values):
        value &= 0xffffffff
        delta = (value - previous) & 0xffffffff
        signed_delta = delta - 0x100000000 if delta & 0x80000000 else delta
        if -128 <= signed_delta <= 127:
            width = 1
        elif -32768 <= signed_delta <= 32767:
            width = 2
        elif -8388608 <= signed_delta <= 8388607:
            width = 3
        else:
            width = 4
        selector = groups - 1 - index // 4
        selectors[selector] |= (width - 1) << ((index & 3) * 2)
        payload.extend(delta.to_bytes(4, "little")[:width])
        previous = value
    encoded = bytes([count] + selectors) + bytes(payload)
    # prtouch_read_zip() 对空流仍生成 {0, 0}；当样本数恰好是四的倍数
    # 时还会复制一个未发送的尾随零字节。这两项均由逐指令反汇编确认。
    if count == 0 or count % 4 == 0:
        encoded += b"\x00"
    return encoded


def adc_hold_trigger(samples, min_hold, max_hold, sample_count, lmt_dead):
    """复现 use_adcx 分支的连续保持触发规则。"""
    if sample_count < lmt_dead or min_hold < 0:
        return False
    if min_hold == 0:
        return True
    window = samples[-min_hold:]
    return len(window) == min_hold and all(value >= max_hold
                                            for value in window)


def read_response_count(encoded_count, hit_limit):
    """复现查询命令的一过界循环计数；0 点查询也返回 len=1。"""
    return encoded_count if hit_limit else encoded_count + 1


def step_capture_decision(period_due, first_swap_state=None,
                          second_swap_state=1):
    """复现 STEP 任务是否采样、是否停止及第一次 GPIO 读取次数。"""
    first_read = not period_due
    if first_read and first_swap_state:
        return False, False, first_read
    return True, not bool(second_swap_state), first_read


def cs1237_trigger_reference(filtered, min_hold, add_hold,
                             sample_count=64, lmt_dead=64):
    """复现数字通道三级递增阈值、方向归一化和 61 点历史保护。"""
    if len(filtered) != 64 or sample_count < max(64, lmt_dead):
        return False
    edge = list(filtered[59:64])
    edge[1] = nearest_delta_sample(edge[0], edge[1:4])
    edge[2] = nearest_delta_sample(edge[1], edge[2:5])
    inverted = filtered[0] > filtered[-1]
    if inverted:
        edge = [signed32(-value) for value in edge]
    if not (edge[2] <= edge[3] <= edge[4]):
        return False
    if edge[4] < signed32(min_hold + 2 * add_hold):
        return False
    if edge[3] < signed32(min_hold + add_hold) or edge[2] < min_hold:
        return False
    # The factory normalizes only edge[0..4].  Historical filtered values keep
    # their original sign even when the current edge is falling.
    return all(value <= edge[2] for value in filtered[:61])
