#!/usr/bin/env python3
"""Generate a GPIO edge train on an already-running Klipper MCU.

This helper deliberately avoids a full identify transfer.  It loads the matching
local dictionary and sweeps the four-bit Klipper sequence space so a probe can
recover from an unknown host sequence without resetting or reflashing the MCU.
"""

import argparse
import pathlib
import sys
import time
import zlib

import serial


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "klippy"))
import msgproto


def encode_packet(parser, sequence, command):
    framed = parser.encode_msgblock(sequence, command)
    return bytes(framed[:-2] + framed[-2] + framed[-1:])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port")
    ap.add_argument("--dictionary", type=pathlib.Path, required=True)
    ap.add_argument("--pin", default="PB13")
    ap.add_argument("--baud", type=int, default=230400)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--interval", type=float, default=0.02)
    args = ap.parse_args()
    if args.rounds < 1:
        ap.error("--rounds must be at least 1")
    if args.interval <= 0:
        ap.error("--interval must be positive")

    parser = msgproto.MessageParser()
    parser.process_identify(zlib.compress(args.dictionary.read_bytes()))
    sent = 0
    with serial.Serial(args.port, args.baud, timeout=0.02) as port:
        port.reset_input_buffer()
        for _round in range(args.rounds):
            for sequence in range(16):
                value = sequence & 1
                command = parser.create_command(
                    "set_digital_out pin=%s value=%d" % (args.pin, value)
                )
                port.write(encode_packet(parser, sequence, command))
                port.flush()
                sent += 1
                time.sleep(args.interval)
    print(
        "port=%s pin=%s packets=%d sequence_sweeps=%d interval=%.6f"
        % (args.port, args.pin, sent, args.rounds, args.interval)
    )


if __name__ == "__main__":
    main()
