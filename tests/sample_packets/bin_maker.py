from tarpn.ax25 import UFrame, AX25Call, SupervisoryCommand, UnnumberedType
from tarpn.port.kiss import encode_kiss_frame, KISSFrame, KISSCommand

if __name__ == "__main__":
    # Write out some binary KISS data
    #data = bytes([192, 0, 150, 104, 136, 132, 180, 64, 228, 150, 104, 136, 132, 180, 64, 115, 17, 192])
    frame = UFrame.u_frame(AX25Call("k4dbz", 2), AX25Call("k4dbz", 9), [], SupervisoryCommand.Command, UnnumberedType.SABM, True)
    data = encode_kiss_frame(KISSFrame(0, KISSCommand.Data, frame.buffer), False)
    with open("sabm_frame.kiss", "wb") as fp:
        fp.write(data)
