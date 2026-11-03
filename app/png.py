"""Small PNG reader (standard library only) for map tiles: returns the RGBA of one pixel."""
import struct
import zlib

CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


class Png:
    def __init__(self, data):
        if data[:8] != b'\x89PNG\r\n\x1a\n':
            raise ValueError('not a PNG')
        pos, idat, self.palette, self.trns = 8, [], None, None
        while pos < len(data):
            length, kind = struct.unpack('>I4s', data[pos:pos + 8])
            body = data[pos + 8:pos + 8 + length]
            pos += 12 + length
            if kind == b'IHDR':
                self.width, self.height, self.depth, self.ctype, _, _, interlace = struct.unpack('>IIBBBBB', body)
                if interlace:
                    raise ValueError('interlaced PNG not supported')
            elif kind == b'PLTE':
                self.palette = [tuple(body[i:i + 3]) for i in range(0, len(body), 3)]
            elif kind == b'tRNS':
                self.trns = body
            elif kind == b'IDAT':
                idat.append(body)
            elif kind == b'IEND':
                break
        self.bpp_bits = CHANNELS[self.ctype] * self.depth
        self.stride = (self.width * self.bpp_bits + 7) // 8
        self._raw = zlib.decompress(b''.join(idat))
        self.rows = []

    def _unfilter_to(self, y):
        """Unfilter rows only as far as row y (tiles are read one pixel at a time)."""
        bpp = max(1, self.bpp_bits // 8)
        rows, raw = self.rows, self._raw
        prev = rows[-1] if rows else bytearray(self.stride)
        pos = len(rows) * (1 + self.stride)
        while len(rows) <= y:
            ftype, line = raw[pos], bytearray(raw[pos + 1:pos + 1 + self.stride])
            pos += 1 + self.stride
            for i in range(self.stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                if ftype == 1:
                    line[i] = (line[i] + a) & 255
                elif ftype == 2:
                    line[i] = (line[i] + b) & 255
                elif ftype == 3:
                    line[i] = (line[i] + ((a + b) >> 1)) & 255
                elif ftype == 4:
                    line[i] = (line[i] + _paeth(a, b, c)) & 255
            rows.append(line)
            prev = line

    def _sample(self, row, index):
        if self.depth == 8:
            return row[index]
        if self.depth == 16:
            return row[2 * index]
        per = 8 // self.depth
        byte = row[index // per]
        shift = 8 - self.depth * (index % per + 1)
        return (byte >> shift) & ((1 << self.depth) - 1)

    def rgba(self, x, y):
        if not 0 <= x < self.width or not 0 <= y < self.height:
            raise IndexError('pixel outside the image')
        self._unfilter_to(y)
        row, n = self.rows[y], CHANNELS[self.ctype]
        vals = [self._sample(row, x * n + k) for k in range(n)]
        if self.ctype == 3:
            idx = vals[0]
            r, g, b = self.palette[idx]
            a = self.trns[idx] if self.trns is not None and idx < len(self.trns) else 255
            return r, g, b, a
        scale = 255 // ((1 << self.depth) - 1) if self.depth < 8 else 1
        if self.ctype == 0:
            v = vals[0] * scale
            return v, v, v, 255
        if self.ctype == 4:
            return vals[0] * scale, vals[0] * scale, vals[0] * scale, vals[1]
        if self.ctype == 2:
            return vals[0], vals[1], vals[2], 255
        return tuple(vals)
