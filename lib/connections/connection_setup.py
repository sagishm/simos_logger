import math
from typing import Optional

DEFAULT_J2534_DLL = (
    "C:/Program Files (x86)/OpenECU/OpenPort 2.0/drivers/openport 2.0/op20pt32.dll"
)


def stmin_to_isotp(st_min):
    if st_min > 1000000:
        return math.ceil(st_min / 1000000)
    hundreds_of_us = math.ceil(st_min / 100000)
    return 0xF0 + hundreds_of_us


def connection_setup(interface, txid, rxid, interface_path=None, st_min: Optional[int] = 350000):
    if st_min is None:
        st_min = 350000

    params = {"tx_padding": 0x55}

    if interface.startswith("SocketCAN"):
        from udsoncan.connections import IsoTPSocketConnection
        can_interface = interface_path if interface_path else interface.split("_")[1]
        conn = IsoTPSocketConnection(can_interface, rxid=rxid, txid=txid, params=params)
        conn.tpsock.set_opts(txpad=0x55, tx_stmin=st_min)

    elif interface == "J2534":
        from .j2534_connection import J2534Connection
        dll = interface_path if interface_path else DEFAULT_J2534_DLL
        conn = J2534Connection(windll=dll, rxid=rxid, txid=txid, st_min=stmin_to_isotp(st_min))

    elif interface.startswith("USBISOTP"):
        from .usb_isotp_connection import USBISOTPConnection
        device_address = interface_path if interface_path else interface.split("_")[1]
        conn = USBISOTPConnection(
            interface_name=device_address, rxid=rxid, txid=txid,
            tx_stmin=int(st_min / 1000),
        )

    else:
        from .fake_connection import FakeConnection
        conn = FakeConnection(testdata={})

    return conn
