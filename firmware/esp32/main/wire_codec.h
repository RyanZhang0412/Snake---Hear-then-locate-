#ifndef XVF_WIRE_CODEC_H
#define XVF_WIRE_CODEC_H
#include <stdint.h>
#include <stddef.h>
/* Preserve all 32 bits, including the packed-frame marker. Zero means use RAW. */
static inline size_t xvf_encode_words(const uint32_t *words,size_t count,uint8_t *out,size_t capacity) {
    size_t used=0;
    for (size_t i=0;i<count;i++) {
        uint32_t v=(words[i]<<1)^(0u-(words[i]>>31));
        do {
            if (used>=capacity) return 0;
            uint8_t byte=v&0x7f;v>>=7;out[used++]=byte|(v?0x80:0);
        } while (v);
    }
    return used;
}
#endif
