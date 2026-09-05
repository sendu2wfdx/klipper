# Build integration for the independently reconstructed Creality-compatible
# 12 KiB UART bootloader.  Its implementation is kept in a pinned submodule.

ifeq ($(wildcard src/bootloader/klipper.mk),)
$(error Missing src/bootloader submodule; run 'git submodule update --init --recursive')
endif

# The submodule adapter also supports the older V57 source tree.  Derive its
# board-role variables here instead of importing the V57 board-version menu.
CONFIG_BOARD_INFO_CONFIGURE := y
ifeq ($(CONFIG_MACH_GD32F303XC),y)
CONFIG_MAIN_MCU_BOARD := y
else ifeq ($(CONFIG_MACH_GD32F303XB),y)
CONFIG_NOZZLE_MCU_BOARD := y
else ifeq ($(CONFIG_MACH_GD32E230X8),y)
CONFIG_BED_MCU_BOARD := y
else
$(error Unsupported MCU for the Creality-compatible bootloader)
endif

bootloader_src-y :=
bootloader_dirs-y := bootloader
BOOTLOADER_CFLAGS := -I$(OUT) -I$(OUT)board -std=gnu11 -O2 -MD \
    -Wall -Wold-style-definition $(call cc-option,$(CC),-Wtype-limits,) \
    -ffunction-sections -fdata-sections -fno-delete-null-pointer-checks
OBJS_bootloader.elf = $(patsubst %.c,$(OUT)bootloader/%.o,$(bootloader_src-y))
CFLAGS_bootloader.elf = $(BOOTLOADER_CFLAGS) -Wl,--gc-sections

include src/bootloader/klipper.mk

# Make the generated dependency files visible to Klipper's normal cleanup and
# dependency loader.  The source adapter appends bootloader/src here.
dirs-y += $(bootloader_dirs-y)

$(OUT)bootloader/%.o: src/bootloader/%.c $(OUT)autoconf.h
	@echo "  Compiling $@"
	$(Q)mkdir -p $(dir $@)
	$(Q)$(CC) $(BOOTLOADER_CFLAGS) -c $< -o $@

$(OUT)bootloader.elf: $(OBJS_bootloader.elf)
	@echo "  Linking $@"
	$(Q)$(CC) $(OBJS_bootloader.elf) $(CFLAGS_bootloader.elf) -o $@
