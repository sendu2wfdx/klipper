#!/usr/bin/env python3
"""Non-moving smoke test for the official PRTouch V2 MCU ADC path."""

import argparse
import sys
import time

import serial

sys.path.insert(0, "klippy")
import msgproto

from gd32_prtouch_v3_probe_windows import (
    send, transact, wait_match, wait_name,
)


def collect_pressure(port, parser, sequence, count, timeout):
    rows = []
    deadline = time.monotonic() + timeout
    while len(rows) < count and time.monotonic() < deadline:
        sequence = send(port, parser, sequence, "get_clock")
        try:
            rows.append(wait_name(
                port, parser, "result_read_pres_prtouch", timeout=.12))
        except TimeoutError:
            pass
    if len(rows) != count:
        raise TimeoutError("pressure stream %d/%d" % (len(rows), count))
    return sequence, rows


def collect_trigger_chunks(port, parser, sequence, timeout):
    chunks = {}
    deadline = time.monotonic() + timeout
    while len(chunks) < 16 and time.monotonic() < deadline:
        sequence = send(port, parser, sequence, "get_clock")
        try:
            response = wait_name(
                port, parser, "result_run_pres_prtouch", timeout=.12)
            chunks[response["index"]] = response
        except TimeoutError:
            pass
    expected = list(range(0, 32, 2))
    if sorted(chunks) != expected:
        raise RuntimeError("trigger chunk indices: %r" % sorted(chunks))
    return sequence, [chunks[index] for index in expected]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port")
    parser.add_argument("--dictionary", required=True)
    parser.add_argument("--baud", type=int, default=230400)
    parser.add_argument("--adc-pin", default="PA0")
    parser.add_argument("--swap-out-pin", default="PB3")
    parser.add_argument("--swap-in-pin", default="PB2")
    parser.add_argument("--samples", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=4.0)
    args = parser.parse_args(argv)
    if not 1 <= args.samples <= 255:
        parser.error("--samples must be in 1..255")

    message_parser = msgproto.MessageParser()
    with open(args.dictionary, "rb") as dictionary_file:
        message_parser.process_identify(
            dictionary_file.read(), decompress=False)
    required = (
        "config_step_prtouch", "config_pres_prtouch",
        "add_pres_prtouch", "read_pres_prtouch",
        "start_pres_prtouch", "manual_get_pres",
        "result_read_pres_prtouch", "result_run_pres_prtouch",
    )
    missing = [name for name in required
               if name not in message_parser.messages_by_name]
    if missing:
        raise RuntimeError("firmware missing: %s" % ", ".join(missing))

    sequence = 0
    with serial.Serial(args.port, args.baud, timeout=.05) as port:
        time.sleep(.25)
        port.reset_input_buffer()
        sequence, _clock, resyncs, ambiguous = transact(
            port, message_parser, sequence, "get_clock", "clock", timeout=.5)
        print("transport_sync resyncs=%d ambiguous_retries=%d seq=%d" %
              (resyncs, ambiguous, sequence), flush=True)

        sequence = send(port, message_parser, sequence,
                        "allocate_oids count=2")
        sequence = send(
            port, message_parser, sequence,
            "config_step_prtouch oid=0 step_cnt=0 swap_pin=%s sys_time_duty=100" %
            args.swap_in_pin)
        sequence = send(
            port, message_parser, sequence,
            "config_pres_prtouch oid=1 use_adc=1 pres_cnt=1 swap_pin=%s sys_time_duty=100" %
            args.swap_out_pin)
        sequence = send(
            port, message_parser, sequence,
            "add_pres_prtouch oid=1 index=0 clk_pin=%s sda_pin=%s" %
            (args.adc_pin, args.adc_pin))
        sequence = send(port, message_parser, sequence,
                        "finalize_config crc=0")
        sequence = send(port, message_parser, sequence, "get_clock")
        for _ in range(3):
            wait_name(port, message_parser, "debug_prtouch", timeout=2.0)

        sequence = send(
            port, message_parser, sequence,
            "read_pres_prtouch oid=1 acq_ms=1 cnt=%d" % args.samples)
        sequence, samples = collect_pressure(
            port, message_parser, sequence, args.samples, args.timeout)
        values = [row["ch0"] for row in samples]
        ticks = [row["tick"] for row in samples]
        if any(value < 0 or value > 4095 for value in values):
            raise RuntimeError("ADC value outside 12-bit range: %r" % values)
        if any(right <= left for left, right in zip(ticks, ticks[1:])):
            raise RuntimeError("pressure ticks are not strictly increasing: %r" %
                               ticks)
        print("fixed_read samples=%d values=%s ticks=%s" %
              (len(samples), ",".join(map(str, values)),
               ",".join(map(str, ticks))), flush=True)

        sequence = send(
            port, message_parser, sequence,
            "deal_avgs_prtouch oid=1 base_cnt=8")
        averages = wait_name(
            port, message_parser, "result_deal_avgs_prtouch", timeout=2.0)
        print("adc_averages=%d,%d,%d,%d" %
              (averages["ch0"], averages["ch1"],
               averages["ch2"], averages["ch3"]), flush=True)

        sequence = send(
            port, message_parser, sequence,
            "start_pres_prtouch oid=1 tri_dir=0 acq_ms=1 send_ms=1 need_cnt=1 tri_hftr_cut=2000 tri_lftr_k1=500 min_hold=1 max_hold=0")
        sequence, chunks = collect_trigger_chunks(
            port, message_parser, sequence, args.timeout)
        if any(chunk["tri_chs"] != 1 for chunk in chunks):
            raise RuntimeError("unexpected trigger channel mask")
        sequence = send(port, message_parser, sequence,
                        "manual_get_pres oid=1 index=0")
        manual = wait_name(
            port, message_parser, "resault_manual_get_pres", timeout=2.0)
        sequence = send(
            port, message_parser, sequence,
            "start_pres_prtouch oid=1 tri_dir=0 acq_ms=1 send_ms=1 need_cnt=0 tri_hftr_cut=0 tri_lftr_k1=0 min_hold=0 max_hold=0")
        sequence = send(port, message_parser, sequence, "get_clock")
        print("trigger chunks=%d indices=%s tri_chs=%d manual=%d,%d" %
              (len(chunks), ",".join(str(row["index"]) for row in chunks),
               chunks[0]["tri_chs"], manual["ch0_0"], manual["ch0_1"]),
              flush=True)

    print("port=%s version=%s PASS" %
          (args.port, message_parser.version))
    return 0


if __name__ == "__main__":
    sys.exit(main())
