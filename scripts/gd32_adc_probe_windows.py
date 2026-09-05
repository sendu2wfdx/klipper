#!/usr/bin/env python3
"""Read a finite batch from a GD32 ADC pin through Klipper's public ABI."""

import argparse
import sys
import time

import serial

sys.path.insert(0, "klippy")
import msgproto

from gd32_prtouch_v3_probe_windows import identify, send, wait_name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port")
    ap.add_argument("--baud", type=int, default=230400)
    ap.add_argument("--pin", default="PA0")
    ap.add_argument("--sample-count", type=int, default=16)
    ap.add_argument("--clock-frequency", type=int, default=120_000_000)
    args = ap.parse_args()
    if not 1 <= args.sample_count <= 24:
        ap.error("--sample-count must be in 1..24")

    parser = msgproto.MessageParser()
    sequence = 0
    with serial.Serial(args.port, args.baud, timeout=0.05) as port:
        time.sleep(0.25)
        port.reset_input_buffer()
        sequence = identify(port, parser, sequence)
        sequence = send(port, parser, sequence, "allocate_oids count=1")
        sequence = send(
            port, parser, sequence,
            "config_analog_in oid=0 pin=%s" % args.pin)
        sequence = send(port, parser, sequence, "finalize_config crc=0")
        sequence = send(port, parser, sequence, "get_clock")
        clock = wait_name(port, parser, "clock")["clock"]
        # Leave ample headroom for Windows CDC scheduling and the conservative
        # GD32 USB OUT path before arming the first MCU timer event.
        start_clock = (clock + args.clock_frequency // 2) & 0xffffffff
        sequence = send(
            port, parser, sequence,
            "query_analog_in oid=0 clock=%d sample_ticks=1200 sample_count=1 "
            "rest_ticks=120000 bytes_per_report=%d min_value=0 "
            "max_value=65535 range_check_count=0" %
            (start_clock, args.sample_count * 2))
        response = wait_name(port, parser, "analog_in_state", timeout=3.0)

    payload = bytes(response["values"])
    values = [int.from_bytes(payload[index:index + 2], "little")
              for index in range(0, len(payload), 2)]
    print("port=%s pin=%s samples=%d next_clock=%d" %
          (args.port, args.pin, len(values), response["next_clock"]))
    print("adc_values=" + ",".join(map(str, values)))


if __name__ == "__main__":
    main()
