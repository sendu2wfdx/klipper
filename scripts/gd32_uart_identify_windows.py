#!/usr/bin/env python3
"""Minimal Klipper identify probe for a Windows COM port."""

import argparse
import pathlib
import sys
import time
import zlib

import serial

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "klippy"))
import msgproto


class PacketReader:
    """Keep bytes following the first frame instead of silently dropping them."""

    def __init__(self, port, parser):
        self.port = port
        self.parser = parser
        self.data = bytearray()
        self.next_sequence = None
        self.recent_names = []
        self.recent_packets = []

    def read_packet(self, deadline):
        while time.monotonic() < deadline:
            self.data.extend(self.port.read(256))
            while self.data:
                packet_len = self.parser.check_packet(self.data)
                if packet_len > 0:
                    packet = bytes(self.data[:packet_len])
                    del self.data[:packet_len]
                    return packet
                if packet_len < 0:
                    del self.data[0]
                    continue
                break
        raise TimeoutError(
            "no valid Klipper response packet; buffered=%s ack_next=%s "
            "seen=%s packets=%s"
            % (self.data.hex(" "), self.next_sequence, self.recent_names,
               self.recent_packets))


def read_named_packet(reader, parser, name, deadline):
    """Ignore asynchronous startup/status packets until the reply arrives."""
    response = None
    got_ack = False
    while time.monotonic() < deadline:
        packet = reader.read_packet(deadline)
        reader.recent_packets.append(packet.hex(" "))
        del reader.recent_packets[:-8]
        if len(packet) == 5:
            # A normal command ACK contains only framing and no message body.
            got_ack = True
            reader.next_sequence = packet[1] & msgproto.MESSAGE_SEQ_MASK
        else:
            parsed = parser.parse(packet)
            reader.recent_names.append(parsed.get("#name"))
            del reader.recent_names[:-8]
            if parsed.get("#name") == name:
                response = parsed
        if response is not None and got_ack:
            return response
    raise TimeoutError("no complete %s response plus ACK" % (name,))


def encode_packet(parser, seq, command):
    """Flatten msgproto's payload list plus two one-byte trailer objects."""
    framed = parser.encode_msgblock(seq, command)
    return bytes(framed[:-2] + framed[-2] + framed[-1:])


def identify_once(port_name, baud, parser, seq, double_prime=False,
                  query_stats=False):
    """Read one dictionary while preserving Klipper's rolling sequence."""
    identify = bytearray()
    with serial.Serial(port_name, baud, timeout=0.05) as port:
        port.reset_input_buffer()
        reader = PacketReader(port, parser)
        for _ in range(1024):
            command = parser.create_command(
                "identify offset=%d count=40" % len(identify)
            )
            packet = encode_packet(parser, seq, command)
            port.write(packet)
            if double_prime and not identify:
                port.write(packet)
            port.flush()
            response = read_named_packet(
                reader, parser, "identify_response", time.monotonic() + 1.0)
            if response["offset"] != len(identify):
                raise RuntimeError("unexpected identify offset")
            if reader.next_sequence is None:
                raise RuntimeError("identify response did not carry an ACK sequence")
            seq = reader.next_sequence
            chunk = bytes(response["data"])
            if not chunk:
                break
            identify.extend(chunk)
        else:
            raise RuntimeError("identify dictionary did not terminate")
        parser.process_identify(bytes(identify))
        stats = None
        if query_stats:
            stats, seq = query_485_stats(port, reader, parser, seq)
    return bytes(identify), seq, stats


def query_485_stats(port, reader, parser, seq):
    """Read the firmware's two read-only USART0 diagnostic records."""
    results = []
    queries = (
        ("get_clock", "clock"),
        ("query_creality_485_uart", "creality_485_uart_status"),
        ("query_creality_485_uart_errors", "creality_485_uart_errors"),
    )
    for command_name, response_name in queries:
        # Leave the single-buffer USB IN endpoint one scheduling turn after
        # the previous response+ACK pair before issuing another read-only
        # query.  The real Klipper serialqueue provides equivalent pacing.
        time.sleep(0.05)
        command = parser.create_command(command_name)
        last_error = None
        for _attempt in range(3):
            port.write(encode_packet(parser, seq, command))
            port.flush()
            try:
                response = read_named_packet(
                    reader, parser, response_name, time.monotonic() + 1.0)
            except TimeoutError as exc:
                last_error = exc
                # A missing business response with a newer ACK means the MCU
                # consumed the read-only command.  Repeat it at the sequence
                # explicitly requested by that ACK.
                if reader.next_sequence is not None:
                    seq = reader.next_sequence
                continue
            if reader.next_sequence is None:
                raise RuntimeError(
                    "diagnostic response did not carry an ACK sequence")
            seq = reader.next_sequence
            results.append(response)
            break
        else:
            raise last_error
    return results, seq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port")
    ap.add_argument("--baud", type=int, default=230400)
    ap.add_argument("--blink-pin")
    ap.add_argument("--cycles", type=int, default=5)
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--double-prime", action="store_true",
                    help="首次 identify 连发两包，用于诊断 USB IN 双缓冲启动")
    ap.add_argument("--sessions", type=int, default=1,
                    help="在同一协议序号链中关闭并重开串口的次数")
    ap.add_argument("--query-485-stats", action="store_true",
                    help="identify 后读取只读 USART0 F7 收发及错误计数")
    ap.add_argument("--local-dictionary", type=pathlib.Path,
                    help="使用同版 klipper.dict 跳过 identify，隔离发送队列测试")
    args = ap.parse_args()

    parser = msgproto.MessageParser()
    seq = 0
    if args.sessions < 1:
        ap.error("--sessions 必须不小于 1")
    if args.local_dictionary:
        if not args.query_485_stats:
            ap.error("--local-dictionary 需要同时指定 --query-485-stats")
        parser.process_identify(zlib.compress(args.local_dictionary.read_bytes()))
        with serial.Serial(args.port, args.baud, timeout=0.05) as port:
            port.reset_input_buffer()
            stats, seq = query_485_stats(
                port, PacketReader(port, parser), parser, seq)
    else:
        stats = None
        for session in range(args.sessions):
            identify, seq, stats = identify_once(
                args.port, args.baud, parser, seq,
                double_prime=args.double_prime and session == 0,
                query_stats=(args.query_485_stats
                             and session == args.sessions - 1))
    print("port=%s baud=%d" % (args.port, args.baud))
    print("version=%s" % parser.version)
    print("build_versions=%s" % parser.build_versions)
    print("sessions=%d next_sequence=%d" % (args.sessions, seq))
    for key in ("MCU", "CLOCK_FREQ", "SERIAL_BAUD", "RESERVE_PINS_serial",
                "RESERVE_PINS_creality_485_uart"):
        print("%s=%s" % (key, parser.get_constants().get(key)))

    if args.query_485_stats:
        clock, status, errors = stats
        print("clock=%d" % clock["clock"])
        print("creality_485_uart rx_frames=%d tx_frames=%d"
              % (status["rx_frames"], status["tx_frames"]))
        print("creality_485_uart_errors invalid=%d dropped=%d"
              % (errors["invalid"], errors["dropped"]))

    if args.blink_pin:
        with serial.Serial(args.port, args.baud, timeout=0.05) as port:
            port.reset_input_buffer()
            for _ in range(args.cycles):
                for value in (1, 0):
                    command = parser.create_command(
                        "set_digital_out pin=%s value=%d"
                        % (args.blink_pin, value)
                    )
                    port.write(encode_packet(parser, seq, command))
                    port.flush()
                    seq = (seq + 1) & 0x0f
                    time.sleep(args.interval)
        print("blink_pin=%s cycles=%d" % (args.blink_pin, args.cycles))


if __name__ == "__main__":
    main()
