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


def crc_b(b: bytes):
    crc = PRESET
    for c in b:
        crc = _update_crc(crc, c)
    return crc


def crc16x25(data: bytes, poly=0x8408):
    """
    CRC-16-CCITT Algorithm
    """
    data = bytearray(data)
    crc = 0xFFFF
    for b in data:
        cur_byte = 0xFF & b
        for _ in range(0, 8):
            if (crc & 0x0001) ^ (cur_byte & 0x0001):
                crc = (crc >> 1) ^ poly
            else:
                crc >>= 1
            cur_byte >>= 1
    crc = (~crc & 0xFFFF)
    crc = (crc << 8) | ((crc >> 8) & 0xFF)

    return crc & 0xFFFF