#!/usr/bin/env python3
"""Stress GD32 USB CDC with periodic ADC reports and clock queries.

The probe configures one public Klipper analog_in object, verifies every
48-byte asynchronous report, and issues only one synchronous get_clock request
at a time.  Asynchronous reports remain checked while transport sequence
resynchronization is in progress.  The selected ADC pin is never driven.
"""

import argparse
import sys
import time

import serial

sys.path.insert(0, "klippy")
import msgproto

from gd32_prtouch_compat_probe_windows import read_packet, send, transact


def clock_forward(previous, current):
    if previous is None:
        return True
    delta = (current - previous) & 0xffffffff
    return 0 < delta < 0x80000000


def transact_with_async(port, parser, sequence, command, response_name,
                        on_async, timeout=1.0, trace=False):
    """Run one reliable request without discarding asynchronous responses."""
    resyncs = 0
    ambiguous_retries = 0
    for attempt in range(20):
        sent_next = send(port, parser, sequence, command)
        if trace:
            print("transport attempt=%d sent=%d next=%d" %
                  (attempt + 1, sequence, sent_next), flush=True)
        deadline = time.monotonic() + timeout
        retry = False
        try:
            while time.monotonic() < deadline:
                response = read_packet(port, parser, deadline,
                                       include_transport=True)
                if response["#name"] == "#transport_ack":
                    ack_next = response["sequence"]
                    if trace:
                        print("transport ack_next=%d" % ack_next, flush=True)
                    if ack_next in (sequence, sent_next):
                        continue
                    sequence = ack_next
                    resyncs += 1
                    retry = True
                    break
                if response.get("#name") == response_name:
                    if trace:
                        print("transport response=%s" % response_name,
                              flush=True)
                    return (sent_next, response, resyncs,
                            ambiguous_retries)
                on_async(response)
        except TimeoutError:
            # ACK for an accepted sequence and NAK for the immediately prior
            # sequence are wire-identical.  Advance once and retry.
            sequence = sent_next
            ambiguous_retries += 1
            continue
        if retry:
            continue
        sequence = sent_next
        ambiguous_retries += 1
    raise RuntimeError("transport sequence did not converge")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port")
    ap.add_argument("--dictionary", required=True)
    ap.add_argument("--baud", type=int, default=230400)
    ap.add_argument("--pin", default="PA0")
    ap.add_argument("--clock-frequency", type=int, default=120_000_000)
    ap.add_argument("--duration", type=float, default=60.0)
    ap.add_argument("--rest-ticks", type=int, default=12_000)
    ap.add_argument("--sample-ticks", type=int, default=1_200)
    ap.add_argument("--bytes-per-report", type=int, default=48)
    ap.add_argument("--clock-query-interval", type=float, default=0.10,
                    help="seconds between reliable get_clock queries; 0 disables")
    ap.add_argument("--progress-interval", type=float, default=10.0)
    ap.add_argument("--trace-transport", action="store_true")
    args = ap.parse_args()
    if args.duration <= 0:
        ap.error("--duration must be positive")
    if args.rest_ticks <= 0 or args.sample_ticks <= 0:
        ap.error("sample timing must be positive")
    if not 2 <= args.bytes_per_report <= 48 or args.bytes_per_report & 1:
        ap.error("--bytes-per-report must be an even value in 2..48")
    if args.clock_query_interval < 0 or args.progress_interval <= 0:
        ap.error("query interval must be non-negative and progress positive")

    parser = msgproto.MessageParser()
    with open(args.dictionary, "rb") as dictionary_file:
        parser.process_identify(dictionary_file.read(), decompress=False)
    required = ("config_analog_in", "query_analog_in", "analog_in_state",
                "get_clock", "clock")
    missing = [name for name in required
               if name not in parser.messages_by_name]
    if missing:
        raise RuntimeError("dictionary missing: %s" % ", ".join(missing))

    expected_report_delta = (
        args.bytes_per_report // 2 * args.rest_ticks) & 0xffffffff
    state = {
        "reports": 0,
        "samples": 0,
        "payload_errors": 0,
        "report_clock_errors": 0,
        "adc_min": None,
        "adc_max": None,
        "previous_report_clock": None,
    }

    def handle_async(item):
        name = item.get("#name")
        if name in ("shutdown", "is_shutdown"):
            raise RuntimeError("MCU shutdown: %r" % item)
        if name != "analog_in_state":
            return
        payload = bytes(item["values"])
        state["reports"] += 1
        if len(payload) != args.bytes_per_report:
            state["payload_errors"] += 1
        values = [int.from_bytes(payload[pos:pos + 2], "little")
                  for pos in range(0, len(payload), 2)]
        state["samples"] += len(values)
        if values:
            current_min = min(values)
            current_max = max(values)
            old_min = state["adc_min"]
            old_max = state["adc_max"]
            state["adc_min"] = (current_min if old_min is None
                                else min(old_min, current_min))
            state["adc_max"] = (current_max if old_max is None
                                else max(old_max, current_max))
        current_clock = item["next_clock"]
        previous_clock = state["previous_report_clock"]
        if previous_clock is not None:
            delta = (current_clock - previous_clock) & 0xffffffff
            if delta != expected_report_delta:
                state["report_clock_errors"] += 1
        state["previous_report_clock"] = current_clock

    sequence = 0
    clock_queries = 0
    clock_responses = 0
    explicit_clock_errors = 0
    total_resyncs = 0
    total_ambiguous_retries = 0
    with serial.Serial(args.port, args.baud, timeout=0.05) as port:
        time.sleep(0.25)
        port.reset_input_buffer()
        sequence, response, resyncs, ambiguous_retries = transact(
            port, parser, sequence, "get_clock", "clock", timeout=0.5)
        total_resyncs += resyncs
        total_ambiguous_retries += ambiguous_retries
        start_clock = (response["clock"]
                       + args.clock_frequency // 2) & 0xffffffff
        previous_explicit_clock = response["clock"]
        sequence = send(port, parser, sequence, "allocate_oids count=1")
        sequence = send(port, parser, sequence,
                        "config_analog_in oid=0 pin=%s" % args.pin)
        sequence = send(port, parser, sequence, "finalize_config crc=0")
        sequence = send(
            port, parser, sequence,
            "query_analog_in oid=0 clock=%d sample_ticks=%d sample_count=1 "
            "rest_ticks=%d bytes_per_report=%d min_value=0 "
            "max_value=65535 range_check_count=0" %
            (start_clock, args.sample_ticks, args.rest_ticks,
             args.bytes_per_report))

        started = time.monotonic()
        deadline = started + args.duration
        next_query = (started if args.clock_query_interval
                      else float("inf"))
        next_progress = started + args.progress_interval
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_query:
                sequence, response, resyncs, ambiguous_retries = (
                    transact_with_async(
                        port, parser, sequence, "get_clock", "clock",
                        handle_async, timeout=0.5,
                        trace=args.trace_transport))
                total_resyncs += resyncs
                total_ambiguous_retries += ambiguous_retries
                clock_queries += 1
                clock_responses += 1
                current_clock = response["clock"]
                if not clock_forward(previous_explicit_clock, current_clock):
                    explicit_clock_errors += 1
                previous_explicit_clock = current_clock
                next_query = max(next_query + args.clock_query_interval,
                                 time.monotonic())
            else:
                read_deadline = min(deadline, next_query,
                                    time.monotonic() + 0.20)
                try:
                    handle_async(read_packet(port, parser, read_deadline))
                except TimeoutError:
                    pass

            if time.monotonic() >= next_progress:
                print("progress elapsed_s=%.1f reports=%d samples=%d "
                      "clock=%d/%d report_clock_errors=%d resyncs=%d" %
                      (time.monotonic() - started, state["reports"],
                       state["samples"], clock_responses, clock_queries,
                       state["report_clock_errors"], total_resyncs),
                      flush=True)
                next_progress += args.progress_interval

        # Stop the producer and put one reliable response behind all queued
        # ADC reports.  Receiving it proves the preceding queue was drained.
        sequence = send(
            port, parser, sequence,
            "query_analog_in oid=0 clock=0 sample_ticks=0 sample_count=0 "
            "rest_ticks=0 bytes_per_report=2 min_value=0 max_value=65535 "
            "range_check_count=0")
        # The stop command itself has no business response.  Give the USB
        # task time to drain already queued ADC frames before asking for the
        # final marker, otherwise a full 192-byte response queue may discard
        # both that marker and its ACK.
        quiet_deadline = time.monotonic() + 1.0
        while time.monotonic() < quiet_deadline:
            try:
                handle_async(read_packet(
                    port, parser, min(quiet_deadline,
                                      time.monotonic() + 0.10)))
            except TimeoutError:
                pass
        sequence, response, resyncs, ambiguous_retries = transact_with_async(
            port, parser, sequence, "get_clock", "clock", handle_async,
            timeout=1.0, trace=args.trace_transport)
        total_resyncs += resyncs
        total_ambiguous_retries += ambiguous_retries
        stop_clock_forward = clock_forward(
            previous_explicit_clock, response["clock"])
        if not stop_clock_forward:
            explicit_clock_errors += 1

    elapsed = time.monotonic() - started
    errors = (state["payload_errors"] + state["report_clock_errors"]
              + explicit_clock_errors + (clock_queries - clock_responses))
    print("SUMMARY elapsed_s=%.3f reports=%d samples=%d bytes=%d "
          "adc_min=%s adc_max=%s clock=%d/%d payload_errors=%d "
          "report_clock_errors=%d explicit_clock_errors=%d resyncs=%d "
          "ambiguous_retries=%d stop_clock_forward=%d errors=%d" %
          (elapsed, state["reports"], state["samples"],
           state["reports"] * args.bytes_per_report,
           state["adc_min"], state["adc_max"], clock_responses,
           clock_queries, state["payload_errors"],
           state["report_clock_errors"], explicit_clock_errors,
           total_resyncs, total_ambiguous_retries,
           stop_clock_forward, errors))
    return 1 if errors or not state["reports"] else 0


if __name__ == "__main__":
    sys.exit(main())
