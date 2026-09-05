#!/usr/bin/env python3
"""Non-moving PRTouch V3 compatibility smoke test on a Windows COM port."""

import argparse
import sys
import time

import serial

sys.path.insert(0, "klippy")
import msgproto


def frame(parser, seq, command):
    encoded = parser.encode_msgblock(seq, parser.create_command(command))
    return bytes(encoded[:-2] + encoded[-2] + encoded[-1:])


def read_packet(port, parser, deadline, include_transport=False):
    if not hasattr(port, "_klipper_rx_buffer"):
        port._klipper_rx_buffer = bytearray()
    data = port._klipper_rx_buffer
    while time.monotonic() < deadline:
        data.extend(port.read(256))
        while data:
            packet_len = parser.check_packet(data)
            if packet_len > 0:
                packet = bytes(data[:packet_len])
                del data[:packet_len]
                # A five-byte frame is the transport-level ACK/NAK and has no
                # message id or payload for MessageParser.parse().
                if packet_len <= msgproto.MESSAGE_MIN:
                    if include_transport:
                        return {
                            "#name": "#transport_ack",
                            "sequence": packet[msgproto.MESSAGE_POS_SEQ]
                                        & msgproto.MESSAGE_SEQ_MASK,
                        }
                    continue
                return parser.parse(packet)
            if packet_len < 0:
                del data[0]
                continue
            break
    raise TimeoutError("no valid Klipper response")


def send(port, parser, seq, command):
    port.write(frame(parser, seq, command))
    port.flush()
    # Keep enough spacing for the conservative bulk-OUT path during the
    # initial configuration burst.
    time.sleep(0.020)
    return (seq + 1) & 0x0f


def transact(port, parser, sequence, command, response_name, timeout=2.0):
    """Send one command, adopting a NAK-advertised transport sequence."""
    resyncs = 0
    ambiguous_retries = 0
    for _ in range(20):
        sent_next = send(port, parser, sequence, command)
        deadline = time.monotonic() + timeout
        retry = False
        try:
            while time.monotonic() < deadline:
                response = read_packet(port, parser, deadline,
                                       include_transport=True)
                if response["#name"] == "#transport_ack":
                    ack_next = response["sequence"]
                    if ack_next in (sequence, sent_next):
                        # When the MCU expects exactly sent_next, an ACK for
                        # an accepted command and a NAK for the duplicate
                        # prior sequence are wire-identical.  The timeout path
                        # below resolves that one-step ambiguity.
                        continue
                    sequence = ack_next
                    resyncs += 1
                    retry = True
                    break
                if response.get("#name") == response_name:
                    return (sent_next, response, resyncs,
                            ambiguous_retries)
                if response.get("#name") in ("shutdown", "is_shutdown"):
                    raise RuntimeError("MCU shutdown: %r" % response)
        except TimeoutError:
            sequence = sent_next
            ambiguous_retries += 1
            continue
        if retry:
            continue
        sequence = sent_next
        ambiguous_retries += 1
    raise RuntimeError("transport sequence did not converge")


def wait_name(port, parser, name, timeout=2.0):
    return wait_match(port, parser, name, lambda response: True, timeout)


def wait_match(port, parser, name, predicate, timeout=2.0):
    if not hasattr(port, "_klipper_response_stash"):
        port._klipper_response_stash = []
    stash = port._klipper_response_stash
    for index, response in enumerate(stash):
        if response.get("#name") == name and predicate(response):
            return stash.pop(index)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = read_packet(port, parser, deadline)
        if response.get("#name") == name and predicate(response):
            return response
        if response.get("#name") in ("shutdown", "is_shutdown"):
            raise RuntimeError("MCU shutdown while waiting for %s: %r" %
                               (name, response))
        stash.append(response)
    raise TimeoutError("no matching %s response" % name)


def unzip_data(blob, signed=True):
    raw = bytearray(blob)
    count = raw[0]
    groups = (count + 3) // 4
    pos = 1 + groups
    previous = 0
    values = []
    for index in range(count):
        selector = raw[1 + groups - 1 - index // 4]
        width = ((selector >> ((index & 3) * 2)) & 3) + 1
        delta = int.from_bytes(raw[pos:pos + width], "little", signed=True)
        pos += width
        previous = (previous + delta) & 0xffffffff
        values.append(previous - 0x100000000
                      if signed and previous & 0x80000000 else previous)
    return values


def identify(port, parser, seq):
    blob = bytearray()
    resync_count = 0
    for _ in range(1024):
        requested_offset = len(blob)
        while True:
            sent_next = send(port, parser, seq,
                             "identify offset=%d count=40" %
                             requested_offset)
            deadline = time.monotonic() + 2.0
            retry = False
            while True:
                response = read_packet(port, parser, deadline,
                                       include_transport=True)
                if response["#name"] == "#transport_ack":
                    ack_next = response["sequence"]
                    if ack_next == seq:
                        # The data response is normally followed by an ACK
                        # with the same next-sequence value.  That ACK can be
                        # the first packet seen by the following identify
                        # request, so ignore it as stale.
                        continue
                    if ack_next != sent_next:
                        # The MCU transport sequence survives a host COM-port
                        # close.  Adopt the NAK-advertised next sequence and
                        # retransmit instead of requiring a physical reset.
                        seq = ack_next
                        resync_count += 1
                        print("transport_resync=%d->%d" %
                              ((sent_next - 1) & msgproto.MESSAGE_SEQ_MASK,
                               ack_next), flush=True)
                        if resync_count > 16:
                            raise RuntimeError(
                                "transport sequence did not converge")
                        retry = True
                        break
                    continue
                if response.get("#name") != "identify_response":
                    continue
                if response["offset"] < requested_offset:
                    continue
                if response["offset"] > requested_offset:
                    raise RuntimeError("unexpected identify offset")
                seq = sent_next
                break
            if retry:
                continue
            break
        chunk = bytes(response["data"])
        if not chunk:
            break
        blob.extend(chunk)
    else:
        raise RuntimeError("identify did not terminate")
    parser.process_identify(bytes(blob))
    return seq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port")
    ap.add_argument("--baud", type=int, default=230400)
    ap.add_argument(
        "--dictionary",
        help="load the matching uncompressed klipper.dict instead of querying identify",
    )
    ap.add_argument("--adc-pin", default="PA0",
                    help="MCU ADC input used for pressure mode (default: PA0)")
    ap.add_argument("--step-swp-pin", default="PB2",
                    help="MCU digital input used for PRTouch step sync (default: PB2)")
    ap.add_argument("--pressure-acq-tick", type=int, default=6000000,
                    help="PRTouch pressure acquisition interval in MCU ticks")
    ap.add_argument("--pressure-wait", type=float, default=0.16,
                    help="seconds to accumulate pressure samples before reading")
    ap.add_argument(
        "--pressure-poke-interval", type=float, default=0.0,
        help=("send get_clock commands while pressure sampling to exercise "
              "cooperative task wakeups; 0 disables it"),
    )
    ap.add_argument("--pressure-read-len", type=int, default=2,
                    help="number of pressure samples to read (1..64)")
    ap.add_argument("--pressure-read-index", type=int, default=0,
                    help="first pressure history index to read (default: 0)")
    ap.add_argument("--print-pressure-values", action="store_true",
                    help="print decoded pressure samples for stimulus correlation")
    ap.add_argument("--skip-pressure", action="store_true")
    ap.add_argument("--stop-after-pressure", action="store_true")
    ap.add_argument("--stop-after-step", action="store_true")
    args = ap.parse_args()

    if args.pressure_acq_tick <= 0:
        ap.error("--pressure-acq-tick must be positive")
    if args.pressure_wait < 0:
        ap.error("--pressure-wait must not be negative")
    if args.pressure_poke_interval < 0:
        ap.error("--pressure-poke-interval must not be negative")
    if 0 < args.pressure_poke_interval < 0.020:
        ap.error("--pressure-poke-interval must be 0 or at least 0.020 seconds")
    if not 1 <= args.pressure_read_len <= 64:
        ap.error("--pressure-read-len must be in 1..64")
    if not 0 <= args.pressure_read_index < 64:
        ap.error("--pressure-read-index must be in 0..63")
    if args.pressure_read_index + args.pressure_read_len > 64:
        ap.error("pressure read range must fit in the 64-sample history")

    parser = msgproto.MessageParser()
    seq = 0
    if args.dictionary:
        with open(args.dictionary, "rb") as dictionary_file:
            parser.process_identify(dictionary_file.read(), decompress=False)
    with serial.Serial(args.port, args.baud, timeout=0.05) as port:
        time.sleep(0.25)
        port.reset_input_buffer()
        if args.dictionary:
            # Loading a local dictionary skips identify, but the MCU's four-
            # bit transport sequence may survive a previous COM-port close.
            # Synchronize with a read-only command before allocating OIDs.
            seq, _clock, resyncs, ambiguous_retries = transact(
                port, parser, seq, "get_clock", "clock", timeout=0.5)
            print("transport_sync resyncs=%d ambiguous_retries=%d seq=%d" %
                  (resyncs, ambiguous_retries, seq), flush=True)
        else:
            seq = identify(port, parser, seq)
        required = ("config_prtouch_pres", "config_prtouch_step",
                    "config_prtouch_apax", "read_prtouch_pres",
                    "read_prtouch_step", "cont_prtouch_step")
        commands = parser.messages_by_name
        missing = [name for name in required if name not in commands]
        if missing:
            raise RuntimeError("firmware missing: %s" % ", ".join(missing))

        # The selected ADC and step-sync pins are sampled but otherwise left
        # electrically untouched.
        # PB0/PB1 define a stepper object, but no queue_step command is sent.
        seq = send(port, parser, seq, "allocate_oids count=3")
        seq = send(port, parser, seq,
                   "config_prtouch_pres oid=0 idx=0 swp_pin=PB3 clk_pin=%s sdo_pin=%s" %
                   (args.adc_pin, args.adc_pin))
        seq = send(port, parser, seq,
                   "config_stepper oid=1 step_pin=PB0 dir_pin=PB1 invert_step=0 step_pulse_ticks=240")
        seq = send(port, parser, seq,
                   "config_prtouch_step oid=2 oid_xstp=1 oid_ystp=1 oid_zstp=1 swp_pin=%s" %
                   args.step_swp_pin)
        seq = send(port, parser, seq,
                   "config_prtouch_apax oid=0 oid_estp=1")
        # Match the normal Klippy lifecycle before starting any periodic task.
        seq = send(port, parser, seq, "finalize_config crc=0")
        seq = send(port, parser, seq, "get_clock")
        wait_match(port, parser, "ack_prtouch",
                   lambda response: response["oid"] == 0)
        wait_match(port, parser, "ack_prtouch",
                   lambda response: response["oid"] == 2)
        time.sleep(0.05)

        if not args.skip_pressure:
            seq = send(port, parser, seq,
                       "start_prtouch_pres oid=0 cfg_regs=0 acq_tick=%d ned_tftr=0 ned_hftr=0 ned_lftr=0 min_hold=3 max_hold=4095 add_hold=0 lmt_dead=64" %
                       args.pressure_acq_tick)
            pressure_pokes = 0
            pressure_deadline = time.monotonic() + args.pressure_wait
            if args.pressure_poke_interval:
                while time.monotonic() < pressure_deadline:
                    poke_started = time.monotonic()
                    seq = send(port, parser, seq, "get_clock")
                    pressure_pokes += 1
                    remaining = (args.pressure_poke_interval
                                 - (time.monotonic() - poke_started))
                    if remaining > 0:
                        time.sleep(min(remaining,
                                       max(0.0, pressure_deadline
                                           - time.monotonic())))
            else:
                time.sleep(args.pressure_wait)
            seq = send(port, parser, seq,
                       "read_prtouch_pres oid=0 is_src=1 ch=0 idx=%d len=%d" %
                       (args.pressure_read_index, args.pressure_read_len))
            seq = send(port, parser, seq, "stop_prtouch_pres oid=0 sta_swap=2")
            seq = send(port, parser, seq, "get_clock")
            ack_pres = wait_match(
                port, parser, "ack_prtouch",
                lambda response: response["expar0"] == 64)
            pres = wait_name(port, parser, "resault_prtouch_pres")
            stop_pres = wait_match(
                port, parser, "ack_prtouch",
                lambda response: response["expar1"] == 71)
            pres_values = unzip_data(pres["datas"])
            pres_ticks = unzip_data(pres["ticks"], signed=False)
            print("pressure_buffer=%d samples=%d decoded=%d version=%d stop_err=%d" %
                  (ack_pres["expar0"], pres["len"], len(pres_values),
                   stop_pres["expar1"], stop_pres["err"]), flush=True)
            if args.pressure_poke_interval:
                print("pressure_pokes=%d poke_interval=%.6f" %
                      (pressure_pokes, args.pressure_poke_interval), flush=True)
            if args.print_pressure_values:
                print("pressure_values=" + ",".join(map(str, pres_values)),
                      flush=True)
                print("pressure_ticks=" + ",".join(map(str, pres_ticks)),
                      flush=True)
                print("pressure_tick_payload=" + bytes(pres["ticks"]).hex(),
                      flush=True)
                print("pressure_data_payload=" + bytes(pres["datas"]).hex(),
                      flush=True)
            time.sleep(0.10)

        if args.stop_after_pressure:
            return

        seq = send(port, parser, seq,
                   "start_prtouch_step oid=2 aqc_tick=6000000")
        time.sleep(0.12)
        seq = send(port, parser, seq,
                   "read_prtouch_step oid=2 idx=0 len=2")
        seq = send(port, parser, seq, "cont_prtouch_step oid=2")
        seq = send(port, parser, seq, "stop_prtouch_step oid=2")
        seq = send(port, parser, seq, "get_clock")
        wait_match(port, parser, "ack_prtouch",
                   lambda response: response["oid"] == 2 and
                   response["expar1"] == 0)
        step = wait_name(port, parser, "resault_prtouch_step")
        step_cnt = wait_name(port, parser, "resault_prtouch_step_cnt")
        stop_step = wait_match(
            port, parser, "ack_prtouch",
            lambda response: response["expar1"] == 71)
        step_values = unzip_data(step["datas"])
        print("step_samples=%d decoded=%d counts=%d,%d,%d sync_state=%d" %
              (step["len"], len(step_values), step_cnt["cnt_x"],
               step_cnt["cnt_y"], step_cnt["cnt_z"],
               stop_step["expar0"]), flush=True)
        time.sleep(0.10)

        if args.stop_after_step:
            return

        seq = send(port, parser, seq,
                   "start_prtouch_apax oid=0 cfg_regs=0 acq_tick=6000000")
        # APAX is a periodic producer.  Keep the USB request/response stream
        # moving until the first asynchronous batch is observed; stopping it
        # first clears the pending sample in the MCU by design.
        seq = send(port, parser, seq, "get_clock")
        wait_match(port, parser, "ack_prtouch",
                   lambda response: response["expar1"] == 6000000)
        time.sleep(0.35)
        seq = send(port, parser, seq, "get_clock")
        apax = wait_name(port, parser, "resault_prtouch_apax", timeout=2.0)
        seq = send(port, parser, seq, "stop_prtouch_apax oid=0")
        seq = send(port, parser, seq, "get_clock")
        stop_apax = wait_match(
            port, parser, "ack_prtouch",
            lambda response: response["oid"] == 0 and
            response["expar0"] == 0 and response["expar1"] == 0)

    print("port=%s version=%s" % (args.port, parser.version))
    apax_values = unzip_data(apax["datas"])
    apax_intervals = unzip_data(apax["espds"], signed=False)
    print("apax_samples=%d decoded=%d intervals=%d stop_err=%d" %
          (apax["len"], len(apax_values), len(apax_intervals),
           stop_apax["err"]))


if __name__ == "__main__":
    main()
