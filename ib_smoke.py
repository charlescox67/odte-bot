import socket, struct, sys

HOST, PORT, CLIENT_ID = '127.0.0.1', 7497, 11

def frame(payload: bytes) -> bytes:
    return struct.pack('>I', len(payload)) + payload

def fields(*vals) -> bytes:
    return b''.join(str(v).encode() + b'\0' for v in vals)

s = socket.create_connection((HOST, PORT), timeout=10)
s.settimeout(10)

# v100+ handshake
s.sendall(b'API\0' + frame(b'v100..187'))

def read_msg():
    hdr = b''
    while len(hdr) < 4:
        c = s.recv(4 - len(hdr))
        if not c:
            return None
        hdr += c
    n = struct.unpack('>I', hdr)[0]
    buf = b''
    while len(buf) < n:
        c = s.recv(n - len(buf))
        if not c:
            return None
        buf += c
    return buf.split(b'\0')

hs = read_msg()
print('server version :', hs[0].decode())
print('connection time:', hs[1].decode())

# START_API (msgId 71, version 2)
s.sendall(frame(fields(71, 2, CLIENT_ID, '')))

accounts = None
for _ in range(40):
    try:
        m = read_msg()
    except socket.timeout:
        break
    if not m:
        break
    if m[0] == b'15':               # MANAGED_ACCTS
        accounts = m[2].decode()
        break
    if m[0] == b'4':                # ERR_MSG
        print('server msg     :', b' '.join(m[3:5]).decode(errors='replace'))

print('managed accts  :', accounts)
s.close()

if not accounts:
    sys.exit('FAIL: no account list returned')
paper = all(a.startswith('D') for a in accounts.split(',') if a)
print('PAPER ACCOUNT  :', paper)
sys.exit(0 if paper else 'FAIL: a non-paper account is reachable on this port')
