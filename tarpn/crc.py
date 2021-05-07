# Taken from https://stackoverflow.com/questions/25239423/crc-ccitt-16-bit-python-manual-calculation

POLYNOMIAL = 0x1021
PRESET = 0


def _initial(c):
    crc = 0
    c = c << 8
    for j in range(8):
        if (crc ^ c) & 0x8000:
            crc = (crc << 1) ^ POLYNOMIAL
        else:
            crc = crc << 1
        c = c << 1
    return crc


_tab = [ _initial(i) for i in range(256) ]


def _update_crc(crc, c):
    cc = 0xff & c

    tmp = (crc >> 8) ^ cc
    crc = (crc << 8) ^ _tab[tmp & 0xff]
    crc = crc & 0xffff

    return crc


def crc(s: str):
    crc = PRESET
    for c in str:
        crc = _update_crc(crc, ord(c))
    return crc


def crcb(b: bytes):
    crc = PRESET
    for c in b:
        crc = _update_crc(crc, c)
    return crc