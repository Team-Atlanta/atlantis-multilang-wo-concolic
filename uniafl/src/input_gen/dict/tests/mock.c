void target_1(const uint8_t *data, size_t size) {
  char buf[0x40];
  if (size > 0 && data[0] == 'A')
    memcpy(buf, data, size);
}
