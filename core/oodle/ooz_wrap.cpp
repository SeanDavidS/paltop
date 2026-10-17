/* ooz_wrap.cpp - a stable C entry point for paltop.
   Built with -DOOZ_BUILD_DLL=1, which is the source's own guard for excluding
   its command-line front end (that part pulls in the compressor and some
   Windows-only shims, neither of which we have or need). */
#include <stddef.h>
#include <stdint.h>
typedef unsigned char byte;
int Kraken_Decompress(const byte *src, size_t src_len, byte *dst, size_t dst_len);
extern "C" int ooz_decompress(const uint8_t *src, size_t src_len,
                              uint8_t *dst, size_t dst_len) {
    return Kraken_Decompress((const byte *)src, src_len, (byte *)dst, dst_len);
}
