#!/usr/bin/env python3
"""Stress Klipper USB CDC reopen/resync behavior on a Windows COM port.

This probe is intentionally read-only after the port is opened.  It sends only
get_clock/get_config and never allocates OIDs, toggles pins, resets, or flashes
the MCU.  By default each reopen starts at transport sequence zero to model a
fresh host process attaching to an MCU whose transport state survived close.
"""

import argparse
import sys
import time

import serial

sys.path.insert(0, "klippy")
import msgproto

from gd32_prtouch_compat_probe_windows import transact


def clock_forward(previous, current):
    if previous is None:
        return True
    delta = (current - previous) & 0xffffffff
    return 0 < delta < 0x80000000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port")
    ap.add_argument("--dictionary", required=True,
                    help="matching uncompressed klipper.dict")
    ap.add_argument("--baud", type=int, default=230400)
    ap.add_argument("--cycles", type=int, default=100)
    ap.add_argument("--requests-per-cycle", type=int, default=16)
    ap.add_argument("--open-settle", type=float, default=0.10)
    ap.add_argument(
        "--close-settle", type=float, default=0.0,
        help="seconds to keep the handle open after its final response",
    )
    ap.add_argument("--progress-every", type=int, default=10)
    ap.add_argument(
        "--preserve-sequence", action="store_true",
        help="preserve host sequence across reopen instead of restarting at zero",
    )
    args = ap.parse_args()
    if args.cycles <= 0:
        ap.error("--cycles must be positive")
    if args.requests_per_cycle <= 0:
        ap.error("--requests-per-cycle must be positive")
    if args.open_settle < 0:
        ap.error("--open-settle must not be negative")
    if args.close_settle < 0:
        ap.error("--close-settle must not be negative")
    if args.progress_every < 0:
        ap.error("--progress-every must not be negative")

    parser = msgproto.MessageParser()
    with open(args.dictionary, "rb") as dictionary_file:
        parser.process_identify(dictionary_file.read(), decompress=False)
    for required in ("get_clock", "clock", "get_config", "config"):
        if required not in parser.messages_by_name:
            raise RuntimeError("dictionary missing %s" % required)

    sequence = 0
    previous_clock = None
    total_requests = 0
    total_resyncs = 0
    total_ambiguous_retries = 0
    clock_nonforward = 0
    started = time.monotonic()
    for cycle in range(1, args.cycles + 1):
        cycle_sequence = sequence if args.preserve_sequence else 0
        try:
            with serial.Serial(args.port, args.baud, timeout=0.05) as port:
                time.sleep(args.open_settle)
                port.reset_input_buffer()
                for request_index in range(args.requests_per_cycle):
                    (cycle_sequence, response, resyncs,
                     ambiguous_retries) = transact(
                        port, parser, cycle_sequence, "get_clock", "clock")
                    total_resyncs += resyncs
                    total_ambiguous_retries += ambiguous_retries
                    total_requests += 1
                    current_clock = response["clock"]
                    if not clock_forward(previous_clock, current_clock):
                        clock_nonforward += 1
                    previous_clock = current_clock
                    # Mix a second response shape into every reopen without
                    # modifying MCU configuration or allocating OIDs.
                    if request_index == 0:
                        (cycle_sequence, _response, resyncs,
                         ambiguous_retries) = transact(
                            port, parser, cycle_sequence,
                            "get_config", "config")
                        total_resyncs += resyncs
                        total_ambiguous_retries += ambiguous_retries
                        total_requests += 1
                if args.close_settle:
                    time.sleep(args.close_settle)
        except Exception as exc:
            print("FAIL cycle=%d requests=%d error=%s" %
                  (cycle, total_requests, exc), flush=True)
            return 1
        sequence = cycle_sequence
        if (args.progress_every and
                (cycle % args.progress_every == 0 or cycle == args.cycles)):
            print("progress cycles=%d requests=%d resyncs=%d "
                  "ambiguous_retries=%d clock_nonforward=%d" %
                  (cycle, total_requests, total_resyncs,
                   total_ambiguous_retries, clock_nonforward),
                  flush=True)

    elapsed = time.monotonic() - started
    print("SUMMARY cycles=%d requests=%d resyncs=%d ambiguous_retries=%d "
          "clock_nonforward=%d errors=0 elapsed_s=%.3f" %
          (args.cycles, total_requests, total_resyncs,
           total_ambiguous_retries, clock_nonforward, elapsed))
    return 1 if clock_nonforward else 0


if __name__ == "__main__":
    sys.exit(main())
