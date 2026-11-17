# MRV-26 test helper (not part of the testbed): a fake lease endpoint on the
# card's control address that answers one HELLO the way a broken or stale
# reflector would. usage: fake_lease.py stale NONCE | echo | mute
import socket
import struct
import sys
import time

READY = int.from_bytes(b"INLINERD", "little")
mode = sys.argv[1]
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("10.10.0.2", 7526))
s.listen(1)
print("fake lease endpoint up, mode", mode, flush=True)
c, peer = s.accept()
hello = struct.unpack("<8Q", c.recv(64))
print("HELLO from", peer, "nonce %#018x" % hello[2], flush=True)
if mode == "stale":
    nonce = int(sys.argv[2], 0)  # a nonce of an earlier run
elif mode == "echo":
    nonce = hello[2]  # this run's nonce, but no slot behind it
if mode != "mute":
    c.sendall(struct.pack("<8Q", READY, 1, nonce, 15, 4242, 0, 0, 0))
    print("sent READY with nonce %#018x" % nonce, flush=True)
time.sleep(4)
