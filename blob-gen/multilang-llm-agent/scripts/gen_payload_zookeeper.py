import struct


def p32(a):
    return struct.pack("<I", a)


def p64(a):
    return struct.pack("<Q", a)


# open('a', 'wb').write(p32(0)+p64(0)+b'a:b:c:d:e:f:g:h:i:j:k:l:m:n')
open("a", "wb").write(
    p32(0)
    + p64(0)
    + b"2001:db8:1234:0000:0000:0000:0000:0000:0000:<script>alert(1);</script>"
)
