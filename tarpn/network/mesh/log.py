from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, cast

from tarpn.ax25 import AX25Call
from tarpn.network.mesh import MeshAddress
from tarpn.util import lollipop_compare


@dataclass
class Record:
    epoch: int


@dataclass
class Reset(Record):
    pass


@dataclass
class LinkRecord(Record):
    source: MeshAddress
    destination: MeshAddress
    quality: int


@dataclass
class NameRecord(Record):
    node: MeshAddress
    callsign: AX25Call
    name: str


class Log:
    def __init__(self):
        self.records: Dict[MeshAddress, List[Record]] = defaultdict(list)
        self.epochs: Dict[MeshAddress, int] = dict()

    def append(self, address: MeshAddress, record: Record) -> bool:
        records = self.records[address]
        if len(records) == 0:
            records.append(record)
        else:
            latest = records[-1]
            epoch_cmp = lollipop_compare(latest.epoch, record.epoch)
            if epoch_cmp == 1:
                records.append(record)
                self.epochs[address] = record.epoch
                return True
            elif epoch_cmp == 0:
                records.append(Reset(latest.epoch))
                records.append(record)
                self.epochs[address] = record.epoch
                return True
            else:
                return False

    def get_link_states(self, node: MeshAddress) -> Dict[MeshAddress, int]:
        states = {}
        if node not in self.epochs.keys():
            return states

        for record in self.records[node]:
            if isinstance(record, LinkRecord):
                link_record = cast(LinkRecord, record)
                states[link_record.source] = link_record.quality
        return states


if __name__ == "__main__":
    log = Log()
    nodeA = MeshAddress.parse("00.aa")
    nodeB = MeshAddress.parse("00.bb")
    log.append(nodeA, LinkRecord(-128, nodeA, nodeB, 100))
    log.append(nodeA, LinkRecord(-127, nodeA, nodeB, 99))
    log.append(nodeA, LinkRecord(10, nodeA, nodeB, 98))
    log.append(nodeA, LinkRecord(9, nodeA, nodeB, 97))
    log.append(nodeA, LinkRecord(-128, nodeA, nodeB, 100))

    print(log.get_link_states(nodeA))
    print(log.get_link_states(nodeB))
    print(log.records)