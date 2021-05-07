from dataclasses import dataclass

from tarpn.network import L3Address


@dataclass(eq=True, frozen=True)
class MeshAddress(L3Address):
    _id: int

    @property
    def id(self) -> int:
        return self._id

    def __repr__(self):
        hi = (self._id >> 8) & 0xFF
        lo = self._id & 0xFF
        return f"{hi:02x}.{lo:02x}"

    @classmethod
    def parse(cls, s: str):
        addr_parts = [int(part, 16) for part in s.split(".")]
        assert len(addr_parts) == 2
        addr = ((addr_parts[0] << 8) & 0xFF00) | (addr_parts[1] & 0x00FF)
        return cls(addr)