// Factory-compatible application metadata at image offset 0x200.
#include <stdint.h>
#include "autoconf.h"

_Static_assert(sizeof(CONFIG_GD32_APP_VERSION) == 13,
               "CONFIG_GD32_APP_VERSION must contain exactly 12 bytes");

struct __attribute__((packed)) gd32_app_metadata {
    char version[12];
    uint16_t crc16;
    uint32_t image_length;
};

const struct gd32_app_metadata Gd32AppMetadata
    __attribute__((section(".gd32_app_metadata"), used)) = {
        .version = CONFIG_GD32_APP_VERSION,
        .crc16 = 0,
        .image_length = 0,
    };

_Static_assert(sizeof(Gd32AppMetadata) == 0x12,
               "GD32 application metadata layout changed");
