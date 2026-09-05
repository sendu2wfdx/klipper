# BL24C16F/24C16 compatible 2KiB I2C EEPROM support
#
# This module preserves the command and helper API used by Creality V57 while
# using the public Klipper I2C transport.
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
# Copyright (C) 2026 Creality V57 source restoration contributors
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
import struct

from . import bus


CHIP_ADDR_BASE = 0x50
BANK_SIZE = 256
BANK_COUNT = 8
EEPROM_SIZE = BANK_SIZE * BANK_COUNT
PAGE_SIZE = 16


class EEPROMCommandHelper:
    def __init__(self, config, chip):
        self.printer = config.get_printer()
        self.chip = chip
        name_parts = config.get_name().split()
        self.name = name_parts[-1]
        self.register_commands(self.name)
        if len(name_parts) == 1:
            if self.name == "bl24c16f" or not config.has_section("bl24c16f"):
                self.register_commands(None)

    def register_commands(self, name):
        gcode = self.printer.lookup_object("gcode")
        commands = (
            ("EEPROM_DEBUG_READ", self.cmd_EEPROM_DEBUG_READ,
             self.cmd_EEPROM_DEBUG_READ_help),
            ("EEPROM_DEBUG_WRITE_BYTE", self.cmd_EEPROM_DEBUG_WRITE_BYTE,
             self.cmd_EEPROM_DEBUG_WRITE_BYTE_help),
            ("EEPROM_DEBUG_WRITE_INT", self.cmd_EEPROM_DEBUG_WRITE_INT,
             self.cmd_EEPROM_DEBUG_WRITE_INT_help),
            ("EEPROM_DEBUG_WRITE_FLOAT", self.cmd_EEPROM_DEBUG_WRITE_FLOAT,
             self.cmd_EEPROM_DEBUG_WRITE_FLOAT_help),
            ("EEPROM_READ", self.cmd_EEPROM_READ,
             self.cmd_EEPROM_READ_help),
            ("EEPROM_WRITE_BYTE", self.cmd_EEPROM_WRITE_BYTE,
             self.cmd_EEPROM_WRITE_BYTE_help),
            ("EEPROM_WRITE_INT", self.cmd_EEPROM_WRITE_INT,
             self.cmd_EEPROM_WRITE_INT_help),
            ("EEPROM_WRITE_FLOAT", self.cmd_EEPROM_WRITE_FLOAT,
             self.cmd_EEPROM_WRITE_FLOAT_help),
        )
        for command, callback, help_text in commands:
            gcode.register_mux_command(command, "CHIP", name, callback,
                                       desc=help_text)
        for command, callback in (
                ("EEPROM_IS_FIRST_USED", self.cmd_EEPROM_IS_FIRST_USED),
                ("EEPROM_POS", self.cmd_EEPROM_POS),
                ("EEPROM_PRINTER_INFO", self.cmd_EEPROM_PRINTER_INFO)):
            gcode.register_mux_command(command, "CHIP", name, callback)

    def cmd_EEPROM_IS_FIRST_USED(self, gcmd):
        val = self.chip.read_reg(1, 1)
        byte_value = int.from_bytes(val, "little")
        state = byte_value == 255
        gcmd.respond_info("EEPROM_IS_USED val:%s state:%s"
                          % (byte_value, state))
        return state

    def cmd_EEPROM_POS(self, gcmd):
        pos = self.chip.read_reg(0, 1)
        gcmd.respond_info("EEPROM_POS int_pos:%s, pos:%s"
                          % (int.from_bytes(pos, "little"), pos))

    def cmd_EEPROM_PRINTER_INFO(self, gcmd):
        pos = int.from_bytes(self.chip.read_reg(0, 1), "little")
        file_position = self.chip.read_reg(pos * 8, 4)
        base_position_e = self.chip.read_reg(pos * 8 + 4, 4)
        result = {
            "file_position": int.from_bytes(file_position, "little"),
            "base_position_e": struct.unpack("<f", base_position_e)[0],
        }
        gcmd.respond_info("EEPROM_PRINTER_INFO ret:%s" % (result,))

    def _get_addr(self, gcmd):
        return gcmd.get("ADDR", minval=0, maxval=EEPROM_SIZE - 1,
                        parser=lambda value: int(value, 0))

    def _get_size(self, gcmd):
        return gcmd.get("SIZE", minval=0, maxval=56,
                        parser=lambda value: int(value, 0))

    def cmd_EEPROM_DEBUG_READ(self, gcmd):
        addr = self._get_addr(gcmd)
        size = self._get_size(gcmd)
        values = self.chip.read_reg(addr, size)
        gcmd.respond_info("EEPROM_DEBUG_READ size: 0x%x" % (size,))
        lines = []
        for index in range(0, size, 16):
            lines.append(" ".join("0x%x" % value
                                  for value in values[index:index + 16]))
        gcmd.respond_info("read vals: \n" + "\n".join(lines))

    cmd_EEPROM_DEBUG_READ_help = "Read data bytes from eeprom"

    def cmd_EEPROM_DEBUG_WRITE_BYTE(self, gcmd):
        addr = self._get_addr(gcmd)
        value = gcmd.get("VAL", minval=0, maxval=255,
                         parser=lambda item: int(item, 0))
        gcmd.respond_info("EEPROM_DEBUG_WRITE_BYTE : ADDR[0x%x] = 0x%x"
                          % (addr, value))
        self.chip.write_reg(addr, value)

    cmd_EEPROM_DEBUG_WRITE_BYTE_help = "Write byte data to eeprom"

    @staticmethod
    def _int_bytes(value):
        return [(value >> shift) & 0xff for shift in (0, 8, 16, 24)]

    def cmd_EEPROM_DEBUG_WRITE_INT(self, gcmd):
        pos = self.chip.read_reg(0, 1)
        gcmd.respond_info("EEPROM_POS int_pos:%s"
                          % int.from_bytes(pos, "little"))
        addr = self._get_addr(gcmd)
        value = gcmd.get("VAL", minval=0, maxval=4294967296,
                         parser=lambda item: int(item, 0))
        values = self._int_bytes(value)
        gcmd.respond_info("EEPROM_DEBUG_WRITE_INT : val = %d" % value)
        gcmd.respond_info(
            "EEPROM_DEBUG_WRITE_INT : ADDR[0x%x] = "
            "0x%02x 0x%02x 0x%02x 0x%02x" % ((addr,) + tuple(values)))
        self.chip.write_reg(addr, values)

    cmd_EEPROM_DEBUG_WRITE_INT_help = "Write int (4 byte) data to eeprom"

    def cmd_EEPROM_DEBUG_WRITE_FLOAT(self, gcmd):
        addr = self._get_addr(gcmd)
        value = gcmd.get_float("VAL", 0.)
        values = list(struct.pack("<f", value))
        gcmd.respond_info("EEPROM_DEBUG_WRITE_FLOAT : val = %f" % value)
        gcmd.respond_info(
            "EEPROM_DEBUG_WRITE_FLOAT : ADDR[0x%x] = "
            "0x%02x 0x%02x 0x%02x 0x%02x" % ((addr,) + tuple(values)))
        self.chip.write_reg(addr, values)

    cmd_EEPROM_DEBUG_WRITE_FLOAT_help = "Write float (4 byte) data to eeprom"

    def cmd_EEPROM_READ(self, gcmd):
        # Preserve the V57 production command: perform the read silently.
        self.chip.read_reg(self._get_addr(gcmd), self._get_size(gcmd))

    cmd_EEPROM_READ_help = "Read data bytes from eeprom"

    def cmd_EEPROM_WRITE_BYTE(self, gcmd):
        addr = self._get_addr(gcmd)
        value = gcmd.get("VAL", minval=0, maxval=255,
                         parser=lambda item: int(item, 0))
        self.chip.write_reg(addr, value)

    cmd_EEPROM_WRITE_BYTE_help = "Write byte data to eeprom"

    def cmd_EEPROM_WRITE_INT(self, gcmd):
        addr = self._get_addr(gcmd)
        value = gcmd.get("VAL", minval=0, maxval=4294967296,
                         parser=lambda item: int(item, 0))
        self.chip.write_reg(addr, self._int_bytes(value))

    cmd_EEPROM_WRITE_INT_help = "Write int (4 byte) data to eeprom"

    def cmd_EEPROM_WRITE_FLOAT(self, gcmd):
        addr = self._get_addr(gcmd)
        value = gcmd.get_float("VAL", 0.)
        self.chip.write_reg(addr, list(struct.pack("<f", value)))

    cmd_EEPROM_WRITE_FLOAT_help = "Write float (4 byte) data to eeprom"


class BL24C16F:
    def __init__(self, config):
        self.printer = config.get_printer()
        EEPROMCommandHelper(config, self)
        self.name = config.get_name().split()[-1]
        self.reactor = self.printer.get_reactor()
        self.write_cycle_time = config.getfloat(
            "write_cycle_time", .005, minval=0., maxval=.100)
        self.i2cs = []
        for index in range(BANK_COUNT):
            i2c = bus.MCU_I2C_from_config(
                config, default_addr=CHIP_ADDR_BASE + index,
                default_speed=400000)
            self.i2cs.append(i2c)
            setattr(self, "i2c%d" % index, i2c)
        self.mcu = self.i2cs[0].get_mcu()
        self.printer.add_object("bl24c16f " + self.name, self)
        self.printer.register_event_handler("klippy:connect",
                                            self.handle_connect)

    def handle_connect(self):
        logging.info("bl24c16f init...")

    @staticmethod
    def _check_range(addr, length):
        if addr < 0 or length < 0 or addr + length > EEPROM_SIZE:
            raise ValueError("BL24C16F access outside 0..%d"
                             % (EEPROM_SIZE - 1,))

    def read_reg(self, addr, read_len):
        self._check_range(addr, read_len)
        result = bytearray()
        while read_len:
            bank = addr // BANK_SIZE
            offset = addr % BANK_SIZE
            chunk_len = min(read_len, BANK_SIZE - offset)
            params = self.i2cs[bank].i2c_read([offset], chunk_len)
            result.extend(params["response"])
            addr += chunk_len
            read_len -= chunk_len
        return result

    def write_reg(self, addr, data):
        if isinstance(data, int):
            values = [data]
        else:
            values = list(data)
        self._check_range(addr, len(values))
        for value in values:
            if value < 0 or value > 255:
                raise ValueError("BL24C16F data byte outside 0..255")
        # A 24C16 write must not span a 16-byte page or a 256-byte address
        # bank. Normal V57 records are four-byte aligned and remain one write.
        position = 0
        while position < len(values):
            bank = addr // BANK_SIZE
            offset = addr % BANK_SIZE
            chunk_len = min(len(values) - position,
                            PAGE_SIZE - (offset % PAGE_SIZE),
                            BANK_SIZE - offset)
            payload = [offset] + values[position:position + chunk_len]
            self.i2cs[bank].i2c_write(payload)
            if self.write_cycle_time:
                self.reactor.pause(
                    self.reactor.monotonic() + self.write_cycle_time)
            addr += chunk_len
            position += chunk_len

    # These camelCase helpers are part of the V57 virtual_sdcard contract.
    def setEepromDisable(self):
        self.write_reg(1, 255)

    def checkEepromFirstEnable(self):
        return int.from_bytes(self.read_reg(1, 1), "little") == 255

    def eepromReadHeader(self):
        return int.from_bytes(self.read_reg(0, 1), "little")

    def eepromReadBody(self, pos):
        file_position = self.read_reg(pos * 8, 4)
        base_position_e = self.read_reg(pos * 8 + 4, 4)
        return {
            "file_position": int.from_bytes(file_position, "little"),
            "base_position_e": struct.unpack("<f", base_position_e)[0],
        }


def load_config(config):
    return BL24C16F(config)


def load_config_prefix(config):
    return BL24C16F(config)
