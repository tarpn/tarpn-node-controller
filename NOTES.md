# Serial ports

This application will make heavy use of serial ports since that's how our TNCs
are connected to the PC. Here's a simple way to test things out without a real
serial device

Create a pair of pseudo-terminals

```
socat -d -d PTY,raw,echo=1,link=/tmp/vmodem0 PTY,raw,echo=0,link=/tmp/vmodem1
```

Run the serial dump tool to monitor one end of the pair(speed doesn't matter)

```
python tools/serial_dump.py /tmp/vmodem0 9600
```

Send some data to the _other_ end of the pair

```
cat README.md /tmp/vmodem1
```

# Structure

Data Link Layer is an AX.25 state machine which manages the L2 connection with any number of senders. Each 
port needs its own state machine since it's a different physical connection (e.g., two sites can have a 2m and a 
440 link). This means the data link ID (port number) needs to be passed on to higher layers.


Or... there's just one AX.25 state machine with any number of ports and we can only establish one connection
for a given source address. This is easier to implement and probably not a problem in practice.

From the spec:

```
The Link Multiplexer State Machine allows one or more data links to share the same physical (radio) channel. 
The Link Multiplexer State Machine provides the logic necessary to give each data link an opportunity to use 
the channel, according to the rotation algorithm embedded within the link multiplexer.

One Link Multiplexer State Machine exists per physical channel. If a single piece of equipment has multiple physical 
channels operating simultaneously, then an independently operating Link Multiplexer State Machine exists for each channel.
```

Each radio/TNC has a LMSM. Each LMSM has many DataLinks, but a DataLink only belongs to a
single LM

LMSM primitives:
* LM-SEIZE Request: DLSM wants control over the radio
* LM-SEIZE Confirm: The DLSM has control over the radio                                                                                                                                                        
* LM-DATA Request: Data is ready to be written out from the DLSM
* LM-DATA Indication: Data is ready to be read


Data Link is a connection between two stations physically connected (by radio). A Data Link has a state machine
that manages the connection state and interacts with the L3

Each Data Link belongs to a Link Multiplexer (port). 



plus a byte stream such as KISS+Serial or TCP.

Each Data Link is a pair of call signs and a port where they communicate. 


Data Links that get NET/ROM pid will send their DL_DATA and DL_UNIT_DATA messages to the NET/ROM layer

NET/ROM packets include a source and destination callsign



AARONL -> DAVID
KN4ORB-2 -> K4DBZ-2 on 2m (port 1)
KN4ORB-2 -> K4DBZ-2 on 6m as well? (port 2)




The NET/ROM destination is an application alias, like K4DBZ-9 for CHAT. This automatically routes packets sent to this 
dest to a running CHAT instance

KN4ORB-2 > K4DBZ-2: NET/ROM KN4ORB-9 > K4DBZ-9: DKA2DEW-5 KA2DEW some chat message



# "Socket" type thing

Client creates circuit=10000 (this is like a port), connects to a well known service circuit on remote side
like 9 for chat, 1 for bbs, etc. But, we can't define which circuit we are connecting to on the far end. This
is a big limitation of NET/ROM. So instead we use special destination calls.

My CHAT wants to connect to Aaron's chat as a client:

> NetRomStateEvent.nl_connect(10000, "KN4ORB-9", "K4DBZ-9")

This will look for a route to KN4ORB-9, which we see is accessible through port 1 (the K4DBZ-2 to KN4OBR-2 link).

This is kind of like opening a socket

```
socket = new Socket();
socket.bind("K4DBZ-9", 10000); // local address
socket.connect("KN4ORB-9", 9); // remote address
```


# CHAT messages
    
Data message: 

    D<REMOTE CALL>: <REMOTE ALIAS> message
    
Keep alive?

    K<REMOTE CALL> <LOCAL CALL> <VERSION>
    
Leave

    L<LOCAL CALL> <LOCAL ALIAS> status message
    
Join

    J<LOCAL CALL> <LOCAL ALIAS> status message
    
Status. When you Join, your neighbor gives you all their station statuses

    S<REMOTE CALL> <REMOTE ALIAS> <LOCAL ALIAS> $TH:[ACT|IDL]<DD-MM-YYYY> <HH:MM>
    

NetRomInfo(dest=K4DBZ-10, origin=K4DBZ-9, ttl=7, circuit_idx=1, circuit_id=1, tx_seq_num=0, rx_seq_num=0, 
          op_byte=5, info=b'*RTL\r\x01KK4DBZ-9 K4DBZ-10 6.0.14.12\r')


*RTL\r\x01KK4DBZ-9 K4DBZ-10 6.0.14.12\r

200812 21:12:10 |          Connecting to Chat Node ZORB09
200812 21:12:10 >          c KN4ORB-9

200812 21:12:11 <          DAVID:K4DBZ-2} Connected to ZORB09:KN4ORB-9

200812 21:12:11 >          *RTL
200812 21:12:11 >          ^AKK4DBZ-9 KN4ORB-9 6.0.14.12

200812 21:12:12 <          [BPQChatServer-6.0.14.12]
200812 21:12:13 <          OK

200812 21:12:13 >          ^AJK4DBZ-9 K4DBZ David Granville County near Grissom
200812 21:12:13 >          ^ATK4DBZ-9 K4DBZ General

200812 21:12:13 <          ^ANKN4ORB-9 KO4BIC-9 ZBIC09 6.0.14.12
200812 21:12:13 <          ^ANKN4ORB-9 K4CBW-9 ZCBW09 6.0.14.12
200812 21:12:13 <          ^ANKN4ORB-9 KC2BXN-9 ZBXN09 6.0.14.12
200812 21:12:13 <          ^ANKN4ORB-9 KE4VNC-9 ZVNC09 6.0.14.12
200812 21:12:13 <          ^ANKN4ORB-9 KM4IFU-9 ZIFU09 6.0.14.12
200812 21:12:13 <          ^ANKN4ORB-9 NC4FG-9 Z4FG09 6.0.14.12
200812 21:12:14 <          ^ANKN4ORB-9 KF4EZU-9 ZEZU09 6.0.14.12
200812 21:12:14 <          ^ANKN4ORB-9 KM4EP-9 Z4EP09 6.0.14.12
200812 21:12:14 <          ^ANKN4ORB-9 KO4BZB-9 ZBZB09 6.0.14.12
200812 21:12:14 <          ^ANKN4ORB-9 N3LTV-9 ZLTV09 6.0.14.12
200812 21:12:14 <          ^ANKN4ORB-9 W4EIP-9 ZEIP09 6.0.14.12
200812 21:12:14 <          ^ANKN4ORB-9 W4GIA-9 ZGIA09 6.0.14.12

200812 21:14:17 >          ^AKK4DBZ-9 KN4ORB-9 6.0.14.12

200812 21:14:20 <          ^ANKN4ORB-9 AG4DB-9 Z4DB09 6.0.14.12
200812 21:14:20 <          ^ANKN4ORB-9 K4RGN-9 ZRGN09 6.0.14.12
200812 21:14:20 <          ^ANKN4ORB-9 N8ZU-9 Z8ZU09 6.0.14.12
200812 21:14:20 <          ^ANKN4ORB-9 KA2DEW-6 ZDEW06 6.0.14.12
200812 21:14:20 <          ^ANKN4ORB-9 KK4VBE-9 ZVBE09 6.0.14.12
200812 21:14:20 <          ^ANKN4ORB-9 K4KDE-9 ZKDE09 6.0.14.12
200812 21:14:20 <          ^ANKN4ORB-9 K4KDE-10 ZKDE10 6.0.14.12


200812 21:14:39 >          <01>SK4DBZ-9 K4DBZ K4RGN $TH:IDL08-12-2020 14:23<0d>

200812 21:14:38 >          <01>SK4DBZ-9 K4DBZ AG4DB $TH:IDL08-12-2020 14:23<0d>
200812 21:14:39 <          /S K4RGN $TH:IDL08-12-2020 14:23

200812 21:15:34 ?          topic_leave user ZORB09 topic General addr 2420f68 ref 16
200812 21:15:34 <          <01>LKM4EP-9 KM4EP Jay NW Raleigh, off Leesville Rd. near the school complex
200812 21:15:34 >          KM4EP  : *** Left

200812 21:21:58 <          <01>NKA2DEW-5 KM4EP-9 Z4EP09 6.0.14.12
200812 21:22:01 <          <01>NKA2DEW-5 KF4EZU-9 ZEZU09 6.0.14.12
200812 21:22:01 <          <01>NKA2DEW-5 NC4FG-9 Z4FG09 6.0.14.12


200812 21:24:51 >          <01>KK4DBZ-9 KN4ORB-9 6.0.14.12


200812 21:28:22 <          <01>SKC2BXN-9 KC2BXN AG4DB $TH:IDL08-12-2020 11:46
200812 21:28:31 <          <01>SKC2BXN-9 KC2BXN W4EIP $TH:IDL08-12-2020 11:46
200812 21:28:31 <          <01>SKC2BXN-9 KC2BXN N3LTV $TH:IDL08-12-2020 11:46
200812 21:28:49 <          <01>SKC2BXN-9 KC2BXN KM4EP $TH:IDL08-12-2020 11:46
200812 21:29:52 <          <01>SKC2BXN-9 KC2BXN KO4BZB $TH:IDL08-12-2020 11:46
200812 21:29:54 <          <01>SKC2BXN-9 KC2BXN NC4FG $TH:IDL08-12-2020 11:46
200812 21:29:59 <          <01>QK4RGN-9 K4KDE-9 ZKDE09
200812 21:29:59 <          <01>QK4RGN-9 K4KDE-10 ZKDE10





# L2 window size problem

L3 sends payloads to L3 outbound queue

L2 polls from this queue continuously

If the L2 window size is exceeded, what to do?



# Offline install
python setup.py bdist_wheel --universal
pip download . --dest dist --no-binary=:all:

python3 -m venv venv
source venv/bin/activate
pip install wheel
pip install tarpn-core --no-index --find-links .



http://www.cs.cornell.edu/info/projects/spinglass/public_pdfs/SWIM.pdf