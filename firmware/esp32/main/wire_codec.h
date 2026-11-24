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
/* Store the four microphone samples in each three-frame cycle. Preserve the
 * marker phase and use a common exponent per block so quiet sounds retain
 * precision. Return zero on invalid framing (caller sends the original). */
static inline size_t xvf_encode_pcm16(const uint32_t *words, size_t frames, uint8_t *out) {
    if (frames < 3) return 0;
    unsigned first = 0;
    while (first < 3 && (words[2 * first] & 1)) first++;
    if (first == 3) return 0;
    uint32_t peak = 0;
    for (size_t i = 0; i < frames; i++) {
        unsigned mark = i % 3 != first;
        if ((words[2*i] & 1) != mark || (words[2*i+1] & 1) != mark ||
            (mark && words[2*i+1] != 1)) return 0;
        for (unsigned j = 0; j < 2 - mark; j++) {
            uint32_t v = words[2*i+j] & ~1u;
            uint32_t magnitude = (int32_t)v < 0 ? ~v : v;
            if (magnitude > peak) peak = magnitude;
        }
    }
    unsigned shift = 0;
    while ((peak >> shift) > 32767u) shift++;
    out[0] = first;
    out[1] = shift;
    size_t used = 2;
    for (size_t i = 0; i < frames; i++) {
        unsigned channels = i % 3 == first ? 2 : 1;
        for (unsigned j = 0; j < channels; j++) {
            uint16_t sample = (words[2*i+j] & ~1u) >> shift;
            out[used++] = sample & 255;
            out[used++] = sample >> 8;
        }
    }
    return used;
}
#endif
