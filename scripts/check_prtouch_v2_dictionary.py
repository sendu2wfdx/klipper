#!/usr/bin/env python3
"""检查 PRTouch V1/V2 共用 MCU 消息字典。"""

import argparse
import json
import pathlib
import sys


COMMANDS = {
    "config_step_prtouch", "add_step_prtouch", "read_swap_prtouch",
    "start_step_prtouch", "manual_get_steps", "config_pres_prtouch",
    "add_pres_prtouch", "write_swap_prtouch", "read_pres_prtouch",
    "deal_avgs_prtouch", "start_pres_prtouch", "manual_get_pres",
}
RESPONSES = {
    "debug_prtouch", "result_read_swap_prtouch",
    "result_manual_get_steps", "result_run_step_prtouch",
    "resault_write_swap_prtouch", "result_deal_avgs_prtouch",
    "resault_manual_get_pres", "result_read_pres_prtouch",
    "result_run_pres_prtouch",
}


def _names(table):
    return {signature.split()[0] for signature in table}


def verify(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    commands = _names(data.get("commands", {}))
    responses = _names(data.get("responses", {}))
    return sorted(COMMANDS - commands), sorted(RESPONSES - responses)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dictionary", type=pathlib.Path)
    args = parser.parse_args(argv)
    missing_commands, missing_responses = verify(args.dictionary)
    print("commands=%d/%d responses=%d/%d" % (
        len(COMMANDS) - len(missing_commands), len(COMMANDS),
        len(RESPONSES) - len(missing_responses), len(RESPONSES)))
    if missing_commands or missing_responses:
        print("missing_commands=%r" % missing_commands, file=sys.stderr)
        print("missing_responses=%r" % missing_responses, file=sys.stderr)
        return 1
    print("SUMMARY prtouch_v1_v2_mcu_dictionary PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
