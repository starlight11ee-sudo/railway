#!/usr/bin/env python3
"""
Minimal VLESS over WebSocket server for Railway.

- No pip packages needed (pure Python standard library).
- Open the domain in a browser: shows the VLESS WS TLS and non-TLS links
  with copy buttons.
- UUID: env UUID if set, otherwise derived from Railway's own service id
  (random-looking, but stays the same after redeploys), otherwise random.
"""
import asyncio
import base64
import hashlib
import html
import ipaddress
import os
import socket
import struct
import uuid as uuidlib
from urllib.parse import quote

PORT = int(os.environ.get("PORT", "8080"))
NAME = os.environ.get("CONFIG_NAME", "Railway")
TLS_PORT = int(os.environ.get("TLS_PORT", "443"))
PLAIN_PORT = int(os.environ.get("PLAIN_PORT", "80"))
ALLOW_PRIVATE = os.environ.get("ALLOW_PRIVATE", "") == "1"  # only for local tests
MAX_FRAME = 16 * 1024 * 1024
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
READ_SIZE = 64 * 1024


def get_uuid() -> str:
    env = os.environ.get("UUID", "").strip()
    if env:
        try:
            return str(uuidlib.UUID(env))
        except ValueError:
            pass
    seed = os.environ.get("RAILWAY_SERVICE_ID") or os.environ.get("RAILWAY_PROJECT_ID")
    if seed:
        return str(uuidlib.uuid5(uuidlib.NAMESPACE_DNS, "vless-ws-" + seed))
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".uuid")
    try:
        with open(path) as f:
            return str(uuidlib.UUID(f.read().strip()))
    except Exception:
        u = str(uuidlib.uuid4())
        try:
            with open(path, "w") as f:
                f.write(u)
        except OSError:
            pass
        return u


UUID = get_uuid()
UUID_BYTES = uuidlib.UUID(UUID).bytes
WS_PATH = "/ws/" + UUID


# ---------------------------------------------------------------- web page
def build_links(host: str):
    p = quote(WS_PATH, safe="")
    tls = (
        f"vless://{UUID}@{host}:{TLS_PORT}?encryption=none&security=tls"
        f"&sni={host}&alpn=http%2F1.1&fp=chrome&type=ws&host={host}&path={p}"
        f"#{quote(NAME + '-TLS')}"
    )
    plain = (
        f"vless://{UUID}@{host}:{PLAIN_PORT}?encryption=none&security=none"
        f"&type=ws&host={host}&path={p}#{quote(NAME + '-NonTLS')}"
    )
    return tls, plain


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>VLESS</title>
<style>
:root{--bg:#f4f6fa;--card:#fff;--fg:#111827;--mut:#6b7280;--bd:#e5e7eb;--ac:#2563eb}
@media(prefers-color-scheme:dark){:root{--bg:#0b1020;--card:#151b2e;--fg:#e5e7eb;--mut:#9ca3af;--bd:#263049;--ac:#3b82f6}}
*{box-sizing:border-box}
body{margin:0;padding:20px;background:var(--bg);color:var(--fg);font:16px/1.5 -apple-system,system-ui,Segoe UI,Roboto,sans-serif}
.w{max-width:640px;margin:0 auto}
h1{font-size:20px;margin:4px 0 16px}
.c{background:var(--card);border:1px solid var(--bd);border-radius:14px;padding:16px;margin-bottom:14px}
.c h2{font-size:15px;margin:0 0 4px}
.c p{margin:0 0 10px;color:var(--mut);font-size:13px}
textarea{width:100%;height:110px;resize:none;border:1px solid var(--bd);border-radius:10px;padding:10px;background:transparent;color:var(--fg);font:12px/1.4 ui-monospace,Menlo,monospace;word-break:break-all}
button{width:100%;margin-top:10px;padding:12px;border:0;border-radius:10px;background:var(--ac);color:#fff;font-size:16px;font-weight:600}
button:active{opacity:.85}
</style></head><body><div class="w">
<h1>VLESS &middot; WebSocket</h1>
<div class="c"><h2>VLESS WS &middot; TLS</h2><p>Port __TLSPORT__</p>
<textarea id="a" readonly>__TLS__</textarea>
<button onclick="cp('a',this)">Copy</button></div>
<div class="c"><h2>VLESS WS &middot; non-TLS</h2><p>Port __PLAINPORT__</p>
<textarea id="b" readonly>__PLAIN__</textarea>
<button onclick="cp('b',this)">Copy</button></div>
</div>
<script>
function done(btn){var t=btn.textContent;btn.textContent='Copied \\u2713';setTimeout(function(){btn.textContent=t},1500)}
function cp(id,btn){
  var el=document.getElementById(id);
  function fb(){el.focus();el.select();el.setSelectionRange(0,99999);try{document.execCommand('copy')}catch(e){}done(btn)}
  if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(el.value).then(function(){done(btn)},fb)}else{fb()}
}
</script></body></html>
"""


def render_page(host: str) -> bytes:
    tls, plain = build_links(host)
    out = (
        PAGE.replace("__TLS__", html.escape(tls))
        .replace("__PLAIN__", html.escape(plain))
        .replace("__TLSPORT__", str(TLS_PORT))
        .replace("__PLAINPORT__", str(PLAIN_PORT))
    )
    return out.encode()


async def http_reply(writer, status: str, body: bytes, ctype="text/plain; charset=utf-8"):
    head = (
        f"HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\n"
        f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\n"
        f"Connection: close\r\n\r\n"
    ).encode()
    writer.write(head + body)
    try:
        await writer.drain()
    except Exception:
        pass


# ---------------------------------------------------------------- websocket
def unmask(data: bytes, mask: bytes) -> bytes:
    n = len(data)
    if n == 0:
        return data
    m = (mask * (n // 4 + 1))[:n]
    return (int.from_bytes(data, "big") ^ int.from_bytes(m, "big")).to_bytes(n, "big")


def make_frame(op: int, data: bytes) -> bytes:
    n = len(data)
    b0 = 0x80 | op
    if n < 126:
        head = bytes([b0, n])
    elif n < 65536:
        head = bytes([b0, 126]) + struct.pack(">H", n)
    else:
        head = bytes([b0, 127]) + struct.pack(">Q", n)
    return head + data


class WS:
    def __init__(self, reader, writer):
        self.r = reader
        self.w = writer
        self.lock = asyncio.Lock()
        self.closed = False

    async def _write(self, data: bytes):
        async with self.lock:
            self.w.write(data)
            await self.w.drain()

    async def send(self, data: bytes):
        await self._write(make_frame(0x2, data))

    async def close(self, code: int = 1000):
        if not self.closed:
            self.closed = True
            try:
                await self._write(make_frame(0x8, struct.pack(">H", code)))
            except Exception:
                pass

    async def recv(self):
        """Return next complete message (bytes), or None when closed."""
        buf = bytearray()
        while True:
            try:
                h = await self.r.readexactly(2)
            except (asyncio.IncompleteReadError, ConnectionError):
                return None
            fin = h[0] & 0x80
            op = h[0] & 0x0F
            masked = h[1] & 0x80
            ln = h[1] & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", await self.r.readexactly(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", await self.r.readexactly(8))[0]
            if ln > MAX_FRAME:
                return None
            mask = await self.r.readexactly(4) if masked else None
            data = await self.r.readexactly(ln) if ln else b""
            if mask:
                data = unmask(data, mask)
            if op == 0x8:
                return None
            if op == 0x9:
                await self._write(make_frame(0xA, data))
                continue
            if op == 0xA:
                continue
            buf += data
            if len(buf) > MAX_FRAME:
                return None
            if fin:
                return bytes(buf)


async def ws_keepalive(ws: WS):
    try:
        while True:
            await asyncio.sleep(25)
            await ws._write(make_frame(0x9, b""))
    except Exception:
        pass


# ---------------------------------------------------------------- vless
def parse_vless(b: bytes):
    if len(b) < 24:
        raise ValueError("short header")
    if b[1:17] != UUID_BYTES:
        raise ValueError("bad uuid")
    pos = 17
    pos += 1 + b[pos]
    cmd = b[pos]
    pos += 1
    port = int.from_bytes(b[pos:pos + 2], "big")
    pos += 2
    at = b[pos]
    pos += 1
    if at == 1:
        addr = socket.inet_ntoa(b[pos:pos + 4])
        pos += 4
    elif at == 2:
        ln = b[pos]
        pos += 1
        addr = b[pos:pos + ln].decode("utf-8", "ignore")
        pos += ln
    elif at == 3:
        addr = socket.inet_ntop(socket.AF_INET6, b[pos:pos + 16])
        pos += 16
    else:
        raise ValueError("bad address type")
    if pos > len(b):
        raise ValueError("truncated header")
    return b[0], cmd, addr, port, b[pos:]


def allowed_ip(ip: str) -> bool:
    if ALLOW_PRIVATE:
        return True
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    return a.is_global and not a.is_multicast


async def resolve(addr: str, port: int, kind: int):
    """Return allowed (family, ip) candidates, IPv4 first. Blocks private/internal targets."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(addr, port, type=kind)
    infos.sort(key=lambda i: 0 if i[0] == socket.AF_INET else 1)
    out = []
    for fam, _, _, _, sa in infos:
        if allowed_ip(sa[0]):
            out.append((fam, sa[0]))
    if not out:
        raise ValueError("target blocked or unresolved")
    return out


async def open_tcp(addr: str, port: int):
    last = None
    for _, ip in await resolve(addr, port, socket.SOCK_STREAM):
        try:
            return await asyncio.wait_for(asyncio.open_connection(ip, port), 10)
        except Exception as e:  # try next address
            last = e
    raise last or ValueError("connect failed")


async def tcp_tunnel(ws: WS, ver: int, addr: str, port: int, payload: bytes):
    r, w = await open_tcp(addr, port)
    sock = w.get_extra_info("socket")
    if sock is not None:
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
    if payload:
        w.write(payload)
        await w.drain()
    hdr = bytes([ver, 0])

    async def up():
        while True:
            m = await ws.recv()
            if m is None:
                break
            w.write(m)
            await w.drain()

    async def down():
        first = True
        while True:
            d = await r.read(READ_SIZE)
            if not d:
                break
            await ws.send(hdr + d if first else d)
            first = False

    tasks = [asyncio.create_task(up()), asyncio.create_task(down())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        try:
            w.close()
        except Exception:
            pass


class _UdpProto(asyncio.DatagramProtocol):
    def __init__(self, queue):
        self.q = queue

    def datagram_received(self, data, addr):
        self.q.put_nowait(data)


async def udp_tunnel(ws: WS, ver: int, addr: str, port: int, payload: bytes):
    fam, ip = (await resolve(addr, port, socket.SOCK_DGRAM))[0]
    q: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: _UdpProto(q), remote_addr=(ip, port)
    )
    hdr = bytes([ver, 0])
    buf = bytearray(payload)

    def flush():
        while len(buf) >= 2:
            ln = (buf[0] << 8) | buf[1]
            if len(buf) < 2 + ln:
                break
            transport.sendto(bytes(buf[2:2 + ln]))
            del buf[:2 + ln]

    async def up():
        flush()
        while True:
            m = await ws.recv()
            if m is None:
                break
            buf.extend(m)
            flush()

    async def down():
        first = True
        while True:
            d = await q.get()
            msg = struct.pack(">H", len(d)) + d
            await ws.send(hdr + msg if first else msg)
            first = False

    tasks = [asyncio.create_task(up()), asyncio.create_task(down())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        transport.close()


async def ws_tunnel(reader, writer, headers):
    key = headers.get("sec-websocket-key", "")
    if not key:
        await http_reply(writer, "400 Bad Request", b"bad request")
        return
    accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
    writer.write(
        (
            "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n"
        ).encode()
    )
    await writer.drain()
    ws = WS(reader, writer)
    ka = asyncio.create_task(ws_keepalive(ws))
    try:
        first = await asyncio.wait_for(ws.recv(), 30)
        if not first:
            return
        ver, cmd, addr, port, payload = parse_vless(first)
        if cmd == 1:
            await tcp_tunnel(ws, ver, addr, port, payload)
        elif cmd == 2:
            await udp_tunnel(ws, ver, addr, port, payload)
    except Exception as e:
        print(f"[ws] closed: {type(e).__name__}: {e}", flush=True)
    finally:
        ka.cancel()
        await ws.close()


# ---------------------------------------------------------------- http entry
async def handle(reader, writer):
    try:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 15)
        except Exception:
            return
        lines = head.decode("latin-1").split("\r\n")
        parts = lines[0].split(" ")
        if len(parts) < 2:
            return
        target = parts[1]
        headers = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        path = target.split("?", 1)[0]

        if headers.get("upgrade", "").lower() == "websocket" and path == WS_PATH:
            await ws_tunnel(reader, writer, headers)
        elif path == "/health":
            await http_reply(writer, "200 OK", b"ok")
        elif path in ("/", "/index.html"):
            host = headers.get("host", "").split(":")[0] or "YOUR-DOMAIN"
            await http_reply(writer, "200 OK", render_page(host), "text/html; charset=utf-8")
        else:
            await http_reply(writer, "404 Not Found", b"not found")
    except Exception as e:
        print(f"[http] error: {type(e).__name__}: {e}", flush=True)
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def main():
    try:
        server = await asyncio.start_server(handle, None, PORT, limit=READ_SIZE, backlog=1024)
    except OSError:
        server = await asyncio.start_server(handle, "0.0.0.0", PORT, limit=READ_SIZE, backlog=1024)
    print(f"[vless-ws] listening on :{PORT}  uuid={UUID}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
