/** @file protocol.c
 * @brief Allocation-free SPSD/CBOR admission checks.
 */
#include "protocol.h"

#include <string.h>
#include <strings.h>

uint32_t sdu_crc(const uint8_t* data, size_t length) {
  // Two nibble lookups preserve CRC-32/ISO-HDLC with only 64 bytes of table.
  static const uint32_t table[16] = {
      0x00000000, 0x1db71064, 0x3b6e20c8, 0x26d930ac, 0x76dc4190, 0x6b6b51f4,
      0x4db26158, 0x5005713c, 0xedb88320, 0xf00f9344, 0xd6d6a3e8, 0xcb61b38c,
      0x9b64c2b0, 0x86d3d2d4, 0xa00ae278, 0xbdbdf21c};
  uint32_t crc = UINT32_MAX;
  for (size_t i = 0; i < length; ++i) {
    crc ^= data[i];
    crc = (crc >> 4) ^ table[crc & 15];
    crc = (crc >> 4) ^ table[crc & 15];
  }
  return crc ^ UINT32_MAX;
}

static bool utf8(const uint8_t* p, size_t n) {
  for (size_t i = 0; i < n;) {
    uint32_t c = p[i++], min = 0;
    unsigned count = 0;
    if (c < 128) continue;
    if (c >= 0xc2 && c <= 0xdf) {
      count = 1;
      min = 0x80;
      c &= 31;
    } else if (c >= 0xe0 && c <= 0xef) {
      count = 2;
      min = 0x800;
      c &= 15;
    } else if (c >= 0xf0 && c <= 0xf4) {
      count = 3;
      min = 0x10000;
      c &= 7;
    } else
      return false;
    if (count > n - i) return false;
    while (count--) {
      if ((p[i] & 0xc0) != 0x80) return false;
      c = (c << 6) | (p[i++] & 63);
    }
    if (c < min || c > 0x10ffff || (c >= 0xd800 && c <= 0xdfff)) return false;
  }
  return true;
}

static bool item(const uint8_t* p, size_t n, size_t* at, unsigned depth) {
  if (depth > 4 || *at >= n) return false;
  unsigned initial = p[(*at)++], major = initial >> 5, ai = initial & 31;
  uint64_t len = ai;
  if (ai >= 24) {
    if (ai > 27) return false;
    unsigned bytes = 1U << (ai - 24);
    if (bytes > n - *at) return false;
    len = 0;
    for (unsigned i = 0; i < bytes; ++i) len = (len << 8) | p[(*at)++];
    if (len < (bytes == 1 ? 24 : UINT64_C(1) << (4 * bytes))) return false;
  }
  if (major == 0) return true;
  if (major == 2 || major == 3) {
    if (len > (major == 2 ? SDU_CHUNK : 128U) || len > n - *at) return false;
    if (major == 3 && !utf8(p + *at, len)) return false;
    *at += len;
    return true;
  }
  if (major != 4 && major != 5) return false;
  if (len > (major == 5 ? 32U : 64U)) return false;
  size_t last = 0, last_size = 0;
  for (uint64_t i = 0; i < len; ++i) {
    if (major == 5) {
      size_t start = *at;
      if (*at >= n || (p[*at] >> 5) != 3 || !item(p, n, at, depth + 1))
        return false;
      size_t size = *at - start;
      if (i && (size < last_size ||
                (size == last_size && memcmp(p + last, p + start, size) >= 0)))
        return false;
      last = start;
      last_size = size;
    }
    if (!item(p, n, at, depth + 1)) return false;
  }
  return true;
}

bool sdu_cbor(const uint8_t* data, size_t length) {
  size_t at = 0;
  return length && length <= SDU_PAYLOAD && (data[0] >> 5) == 5 &&
         item(data, length, &at, 0) && at == length;
}

static uint32_t be32(const uint8_t* p) {
  return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
         ((uint32_t)p[2] << 8) | p[3];
}
static void put32(uint8_t* p, uint32_t v) {
  p[0] = v >> 24;
  p[1] = v >> 16;
  p[2] = v >> 8;
  p[3] = v;
}

bool sdu_feed(uint8_t byte, int64_t now, const uint8_t** data, size_t* length) {
  static uint8_t buffer[SDU_FRAME] SDU_BUFFER;
  static size_t used;
  static int64_t since;
  static bool delivered;
  if (delivered || (used && now - since > 2000000)) used = 0;
  delivered = false;
  if (!used) since = now;
  buffer[used++] = byte;
  while (used >= 4) {
    bool uploader = memcmp(buffer, "SPSD", 4) == 0;
    if (!uploader && memcmp(buffer, "SPMF", 4) != 0) goto discard;
    if (used < 10) return false;
    uint32_t size = be32(buffer + 6);
    if (buffer[4] != 1 || buffer[5] || size > SDU_PAYLOAD) goto discard;
    if (used < size + 14) return false;
    if (sdu_crc(buffer + 4, size + 6) != be32(buffer + 10 + size)) goto discard;
    delivered = true;
    if (!uploader) {
      used = 0;
      return false;
    }
    *data = buffer + 10;
    *length = size;
    return true;
  discard:
    memmove(buffer, buffer + 1, --used);
    since = now;
  }
  return false;
}

size_t sdu_frame(uint8_t* out, const uint8_t* payload, size_t length) {
  if (length > SDU_PAYLOAD) return 0;
  memcpy(out, "SPSD\1\0", 6);
  put32(out + 6, length);
  memcpy(out + 10, payload, length);
  put32(out + 10 + length, sdu_crc(out + 4, length + 6));
  return length + 14;
}

bool sdu_name(const char* name) {
  size_t n = strlen(name);
  if (!n || n > 128 || !strncasecmp(name, "_sdu_", 5) || name[n - 1] == '.' ||
      name[n - 1] == ' ')
    return false;
  for (size_t i = 0; i < n; ++i)
    if ((unsigned char)name[i] < 32 || (unsigned char)name[i] > 126 ||
        strchr("/\\:\"*?<>|", name[i]))
      return false;
  return true;
}
