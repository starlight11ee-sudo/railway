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
import hmac
import ipaddress
import json
import os
import socket
import struct
import uuid as uuidlib
from urllib.parse import parse_qs, quote

PORT = int(os.environ.get("PORT", "8080"))
NAME = os.environ.get("CONFIG_NAME", "Railway")
TLS_PORT = int(os.environ.get("TLS_PORT", "443"))
# Non-TLS: Railway TCP Proxy host/port are picked up automatically (or set PLAIN_HOST / PLAIN_PORT).
PLAIN_HOST = (os.environ.get("PLAIN_HOST") or os.environ.get("RAILWAY_TCP_PROXY_DOMAIN") or "").strip()
PLAIN_PORT = int(os.environ.get("PLAIN_PORT") or os.environ.get("RAILWAY_TCP_PROXY_PORT") or "80")
ACCESS_KEY = os.environ.get("ACCESS_KEY", "")  # optional: if set, links need this key
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


# ---------------------------------------------------------------- links + web page
def build_links(req_host: str):
    tls_host = os.environ.get("TLS_HOST") or req_host or os.environ.get("RAILWAY_PUBLIC_DOMAIN") or "YOUR-DOMAIN"
    plain_host = PLAIN_HOST or tls_host
    p = quote(WS_PATH, safe="")
    tls = (
        f"vless://{UUID}@{tls_host}:{TLS_PORT}?encryption=none&security=tls"
        f"&sni={tls_host}&alpn=http%2F1.1&fp=chrome&type=ws&host={tls_host}&path={p}"
        f"#{quote(NAME + '-TLS')}"
    )
    plain = (
        f"vless://{UUID}@{plain_host}:{PLAIN_PORT}?encryption=none&security=none"
        f"&type=ws&host={plain_host}&path={p}#{quote(NAME + '-NonTLS')}"
    )
    note = ""
    if not PLAIN_HOST:
        note = "Railway ma TCP Proxy enable karo, warna non-TLS link Railway par nahi chalega."
    return {
        "tls": tls,
        "plain": plain,
        "tls_port": TLS_PORT,
        "plain_port": PLAIN_PORT,
        "note": note,
    }


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#04060f">
<title>__NAME__</title>
<style>
:root{--bg:#04060f;--c1:#00f0ff;--c2:#a855ff;--c3:#ff2e93;--c4:#7cff6b;--fg:#e8f1ff;--mut:#8fa3c7}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{height:100%}
body{margin:0;background:var(--bg);color:var(--fg);overflow:hidden;
  font:15px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  padding:env(safe-area-inset-top,0) 0 env(safe-area-inset-bottom,0)}
/* ---------- background ---------- */
.aurora{position:fixed;inset:-20%;z-index:0;pointer-events:none;
  background:
   radial-gradient(40% 35% at 20% 25%,rgba(168,85,255,.38),transparent 70%),
   radial-gradient(38% 34% at 80% 20%,rgba(0,240,255,.30),transparent 70%),
   radial-gradient(45% 40% at 70% 85%,rgba(255,46,147,.28),transparent 70%);
  animation:drift 18s ease-in-out infinite alternate}
@keyframes drift{to{transform:translate3d(-4%,3%,0) scale(1.08)}}
.grid{position:fixed;left:-50%;right:-50%;bottom:-8%;height:55%;z-index:0;pointer-events:none;
  background:
   linear-gradient(rgba(0,240,255,.35) 1px,transparent 1px) 0 0/48px 48px,
   linear-gradient(90deg,rgba(168,85,255,.35) 1px,transparent 1px) 0 0/48px 48px;
  transform:perspective(380px) rotateX(62deg);transform-origin:50% 100%;
  -webkit-mask-image:linear-gradient(to top,#000 10%,transparent 85%);
  mask-image:linear-gradient(to top,#000 10%,transparent 85%);
  animation:gridmove 3.2s linear infinite}
@keyframes gridmove{to{background-position:0 48px,48px 0}}
#fx{position:fixed;inset:0;z-index:1;pointer-events:none}
.scan{position:fixed;inset:0;z-index:2;pointer-events:none;opacity:.07;
  background:repeating-linear-gradient(0deg,#fff 0 1px,transparent 1px 3px)}
/* ---------- hero ---------- */
main{position:relative;z-index:3;height:100%;display:flex;flex-direction:column;
  align-items:center;justify-content:center;text-align:center;padding:24px 56px 24px 24px}
.orb{position:relative;width:min(56vw,230px);aspect-ratio:1;margin-bottom:30px}
.orb i{position:absolute;inset:0;border-radius:50%;border:2px solid transparent}
.orb .r1{border-top-color:var(--c1);border-bottom-color:var(--c3);animation:spin 5s linear infinite;
  filter:drop-shadow(0 0 8px var(--c1))}
.orb .r2{inset:12%;border-left-color:var(--c2);border-right-color:var(--c4);animation:spin 3.4s linear infinite reverse;
  filter:drop-shadow(0 0 8px var(--c2))}
.orb .r3{inset:24%;border-top-color:var(--c3);border-left-color:var(--c1);animation:spin 2.4s linear infinite;
  filter:drop-shadow(0 0 8px var(--c3))}
.orb .core{position:absolute;inset:36%;border-radius:50%;
  background:radial-gradient(circle at 35% 30%,#fff,var(--c1) 30%,var(--c2) 65%,transparent 72%);
  box-shadow:0 0 40px var(--c1),0 0 90px var(--c2);animation:pulse 2.6s ease-in-out infinite}
@keyframes spin{to{transform:rotate(360deg)}}
@keyframes pulse{50%{transform:scale(1.18);filter:hue-rotate(60deg)}}
h1{margin:0;font-size:clamp(26px,8vw,40px);letter-spacing:.22em;font-weight:800;
  background:linear-gradient(90deg,var(--c1),var(--c2),var(--c3),var(--c1));background-size:300% 100%;
  -webkit-background-clip:text;background-clip:text;color:transparent;animation:hue 6s linear infinite}
@keyframes hue{to{background-position:300% 0}}
.sub{margin:8px 0 22px;color:var(--mut);font-size:12px;letter-spacing:.3em;text-transform:uppercase}
.chips{display:flex;gap:10px;flex-wrap:wrap;justify-content:center}
.chip{padding:7px 12px;border-radius:999px;font-size:11px;letter-spacing:.14em;text-transform:uppercase;
  border:1px solid rgba(0,240,255,.35);background:rgba(8,16,40,.55);backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px)}
.chip b{color:var(--c4)}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--c4);margin-right:7px;
  box-shadow:0 0 10px var(--c4);animation:blink 1.4s ease-in-out infinite}
@keyframes blink{50%{opacity:.3}}
.hint{position:absolute;left:0;right:0;bottom:calc(22px + env(safe-area-inset-bottom,0));text-align:center;
  color:var(--mut);font-size:11px;letter-spacing:.2em;text-transform:uppercase;opacity:.8}
/* ---------- side tab ||| ---------- */
#tab{position:fixed;right:0;top:50%;transform:translateY(-50%);z-index:6;width:46px;height:104px;border:0;
  border-radius:16px 0 0 16px;display:flex;align-items:center;justify-content:center;gap:5px;cursor:pointer;
  background:linear-gradient(160deg,rgba(0,240,255,.25),rgba(168,85,255,.35));
  border:1px solid rgba(0,240,255,.55);border-right:0;backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);
  box-shadow:0 0 24px rgba(0,240,255,.45),inset 0 0 14px rgba(168,85,255,.35);animation:tabglow 2.6s ease-in-out infinite}
#tab span{display:block;width:4px;height:44px;border-radius:3px;background:linear-gradient(var(--c1),var(--c3));
  box-shadow:0 0 10px var(--c1)}
#tab span:nth-child(2){height:58px;background:linear-gradient(var(--c3),var(--c2))}
@keyframes tabglow{50%{box-shadow:0 0 38px rgba(255,46,147,.6),inset 0 0 18px rgba(0,240,255,.4)}}
/* ---------- drawer ---------- */
#scrim{position:fixed;inset:0;z-index:7;background:rgba(2,4,12,.6);opacity:0;pointer-events:none;transition:opacity .3s;
  backdrop-filter:blur(3px);-webkit-backdrop-filter:blur(3px)}
#scrim.on{opacity:1;pointer-events:auto}
#drawer{position:fixed;top:0;right:0;bottom:0;z-index:8;width:min(90vw,390px);transform:translateX(104%);
  transition:transform .38s cubic-bezier(.2,.9,.2,1);display:flex;flex-direction:column;
  padding:calc(18px + env(safe-area-inset-top,0)) 18px calc(18px + env(safe-area-inset-bottom,0));
  background:linear-gradient(170deg,rgba(14,20,48,.94),rgba(10,8,30,.96));
  border-left:1px solid transparent;
  border-image:linear-gradient(180deg,var(--c1),var(--c2),var(--c3)) 1;
  box-shadow:-20px 0 60px rgba(168,85,255,.35);overflow-y:auto}
#drawer.open{transform:none}
.dh{display:flex;align-items:center;justify-content:space-between;margin-bottom:6px}
.dh h2{margin:0;font-size:14px;letter-spacing:.3em;color:var(--c1);text-shadow:0 0 12px var(--c1)}
#x{width:36px;height:36px;border-radius:10px;border:1px solid rgba(255,46,147,.5);background:rgba(255,46,147,.1);
  color:var(--c3);font-size:20px;line-height:1}
.dp{color:var(--mut);font-size:12px;margin:0 0 18px}
.btn{position:relative;width:100%;padding:16px;border:0;border-radius:14px;color:#fff;font:800 16px ui-monospace,Menlo,monospace;
  letter-spacing:.25em;text-transform:uppercase;background:linear-gradient(90deg,var(--c1),var(--c2),var(--c3),var(--c1));
  background-size:300% 100%;animation:hue 5s linear infinite;box-shadow:0 0 26px rgba(0,240,255,.5),0 0 50px rgba(255,46,147,.25)}
.btn:active{transform:scale(.98)}
.btn:disabled{opacity:.7}
.btn.sm{padding:12px;font-size:13px;margin-top:10px}
#key{width:100%;margin-bottom:12px;padding:13px;border-radius:12px;border:1px solid rgba(0,240,255,.4);
  background:rgba(4,8,24,.7);color:var(--fg);font:15px ui-monospace,Menlo,monospace;text-align:center;letter-spacing:.2em}
#err{min-height:18px;margin:10px 0 0;color:var(--c3);font-size:12px;text-align:center;letter-spacing:.1em}
#links{display:none}
#links.show{display:block;animation:rise .45s ease}
@keyframes rise{from{opacity:0;transform:translateY(14px)}}
.card{margin-top:14px;padding:14px;border-radius:16px;border:1px solid rgba(0,240,255,.3);
  background:linear-gradient(160deg,rgba(0,240,255,.07),rgba(168,85,255,.09))}
.card.alt{border-color:rgba(255,46,147,.4);background:linear-gradient(160deg,rgba(255,46,147,.08),rgba(168,85,255,.08))}
.card h3{margin:0 0 2px;font-size:13px;letter-spacing:.16em;color:var(--c1)}
.card.alt h3{color:var(--c3)}
.card p{margin:0 0 10px;font-size:11px;color:var(--mut);letter-spacing:.12em}
textarea{width:100%;height:96px;resize:none;border-radius:10px;border:1px solid rgba(255,255,255,.14);
  background:rgba(2,4,14,.7);color:#cfe9ff;padding:10px;font:11px/1.4 ui-monospace,Menlo,monospace;word-break:break-all}
.note{margin-top:12px;font-size:11px;color:#ffd166;text-align:center;letter-spacing:.05em}
.hide{margin-top:16px;background:transparent;border:1px solid rgba(255,255,255,.25);color:var(--mut);
  padding:12px;width:100%;border-radius:12px;font:12px ui-monospace,Menlo,monospace;letter-spacing:.2em;text-transform:uppercase}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style></head>
<body>
<div class="aurora"></div><div class="grid"></div><canvas id="fx"></canvas><div class="scan"></div>
<main>
  <div class="orb"><i class="r1"></i><i class="r2"></i><i class="r3"></i><div class="core"></div></div>
  <h1>__NAME__</h1>
  <div class="sub">VLESS &middot; WebSocket Node</div>
  <div class="chips">
    <div class="chip"><span class="dot"></span><b>Online</b></div>
    <div class="chip">Ping <b id="ping">--</b></div>
    <div class="chip">WS <b>Ready</b></div>
  </div>
  <div class="hint">tap the <b>|||</b> on the right edge</div>
</main>

<button id="tab" aria-label="Open menu"><span></span><span></span><span></span></button>
<div id="scrim"></div>
<aside id="drawer" aria-hidden="true">
  <div class="dh"><h2>ACCESS PORTAL</h2><button id="x" aria-label="Close">&times;</button></div>
  <p class="dp">Links hidden hain. Neeche button dabao.</p>
  <div id="gate">
    <input id="key" type="password" placeholder="ACCESS KEY" autocomplete="off" style="display:none">
    <button id="open" class="btn"><span>Open Link</span></button>
    <div id="err"></div>
  </div>
  <div id="links">
    <div class="card"><h3>VLESS WS &middot; TLS</h3><p id="tlsp"></p>
      <textarea id="tls" readonly></textarea>
      <button class="btn sm" data-copy="tls">Copy</button></div>
    <div class="card alt"><h3>VLESS WS &middot; NON-TLS</h3><p id="plainp"></p>
      <textarea id="plain" readonly></textarea>
      <button class="btn sm" data-copy="plain">Copy</button>
      <div class="note" id="note"></div></div>
    <button class="hide" id="hide">Hide links</button>
  </div>
</aside>

<script>
var LOCK=__LOCK__;
var $=function(s){return document.querySelector(s)};
var drawer=$('#drawer'),scrim=$('#scrim');
function openD(){drawer.classList.add('open');scrim.classList.add('on');drawer.setAttribute('aria-hidden','false')}
function closeD(){drawer.classList.remove('open');scrim.classList.remove('on');drawer.setAttribute('aria-hidden','true')}
$('#tab').onclick=openD;$('#x').onclick=closeD;scrim.onclick=closeD;
document.addEventListener('keydown',function(e){if(e.key==='Escape')closeD()});
if(LOCK)$('#key').style.display='block';

function hideLinks(){
  $('#links').classList.remove('show');$('#gate').style.display='block';
  $('#tls').value='';$('#plain').value='';$('#err').textContent='';
}
$('#hide').onclick=hideLinks;

$('#open').onclick=function(){
  var b=$('#open'),lab=b.querySelector('span'),err=$('#err');
  err.textContent='';b.disabled=true;lab.textContent='Decrypting...';
  var url='/api/links';
  if(LOCK)url+='?k='+encodeURIComponent($('#key').value.trim());
  fetch(url,{cache:'no-store'}).then(function(r){
    if(!r.ok)throw new Error(r.status);return r.json();
  }).then(function(d){
    $('#tls').value=d.tls;$('#plain').value=d.plain;
    $('#tlsp').textContent='PORT '+d.tls_port;$('#plainp').textContent='PORT '+d.plain_port;
    $('#note').textContent=d.note||'';
    $('#gate').style.display='none';$('#links').classList.add('show');
  }).catch(function(){
    err.textContent='ACCESS DENIED';
  }).then(function(){b.disabled=false;lab.textContent='Open Link'});
};

function done(btn){var t=btn.textContent;btn.textContent='Copied \u2713';setTimeout(function(){btn.textContent=t},1500)}
document.querySelectorAll('[data-copy]').forEach(function(btn){
  btn.onclick=function(){
    var el=document.getElementById(btn.getAttribute('data-copy'));
    function fb(){el.focus();el.select();el.setSelectionRange(0,99999);try{document.execCommand('copy')}catch(e){}done(btn)}
    if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(el.value).then(function(){done(btn)},fb)}else{fb()}
  };
});

/* live ping */
(function(){
  function p(){var t=performance.now();
    fetch('/health',{cache:'no-store'}).then(function(){$('#ping').textContent=Math.round(performance.now()-t)+' ms'})
    .catch(function(){$('#ping').textContent='--'})}
  p();setInterval(function(){if(!document.hidden)p()},15000);
})();

/* constellation background */
(function(){
  if(window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches)return;
  var c=$('#fx'),x=c.getContext('2d'),W,H,pts=[],N=58,dpr=Math.min(window.devicePixelRatio||1,2);
  var cols=['0,240,255','168,85,255','255,46,147'];
  function size(){W=innerWidth;H=innerHeight;c.width=W*dpr;c.height=H*dpr;x.setTransform(dpr,0,0,dpr,0,0)}
  size();addEventListener('resize',size);
  for(var i=0;i<N;i++)pts.push({x:Math.random()*W,y:Math.random()*H,vx:(Math.random()-.5)*.35,vy:(Math.random()-.5)*.35,
    r:Math.random()*1.6+.6,c:cols[i%3]});
  function f(){
    if(!document.hidden){
      x.clearRect(0,0,W,H);
      for(var i=0;i<N;i++){var a=pts[i];a.x+=a.vx;a.y+=a.vy;
        if(a.x<0||a.x>W)a.vx*=-1;if(a.y<0||a.y>H)a.vy*=-1;
        for(var j=i+1;j<N;j++){var b=pts[j],dx=a.x-b.x,dy=a.y-b.y,d=dx*dx+dy*dy;
          if(d<14000){x.strokeStyle='rgba('+a.c+','+(1-d/14000)*.35+')';x.lineWidth=1;x.beginPath();x.moveTo(a.x,a.y);x.lineTo(b.x,b.y);x.stroke()}}
        x.fillStyle='rgba('+a.c+',.9)';x.shadowColor='rgba('+a.c+',1)';x.shadowBlur=8;
        x.beginPath();x.arc(a.x,a.y,a.r,0,6.283);x.fill();x.shadowBlur=0}
    }
    requestAnimationFrame(f)
  }
  f();
})();
</script></body></html>
"""


def render_page() -> bytes:
    out = (
        PAGE.replace("__NAME__", NAME.replace("<", "").replace(">", "").replace("&", ""))
        .replace("__LOCK__", "true" if ACCESS_KEY else "false")
    )
    return out.encode()


def json_bytes(obj) -> bytes:
    return json.dumps(obj).encode()


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
    print(f"[ws] handshake ok proto={headers.get('x-forwarded-proto','-')} "
          f"host={headers.get('host','-')}", flush=True)
    try:
        first = await asyncio.wait_for(ws.recv(), 30)
        if not first:
            return
        ver, cmd, addr, port, payload = parse_vless(first)
        print(f"[ws] cmd={cmd} -> {addr}:{port}", flush=True)
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
        path, _, query = target.partition("?")

        if headers.get("upgrade", "").lower() == "websocket" and path == WS_PATH:
            await ws_tunnel(reader, writer, headers)
        elif path == "/health":
            await http_reply(writer, "200 OK", b"ok")
        elif path == "/api/links":
            key = parse_qs(query).get("k", [""])[0]
            if ACCESS_KEY and not hmac.compare_digest(key.encode(), ACCESS_KEY.encode()):
                await http_reply(writer, "403 Forbidden", b'{"error":"denied"}', "application/json")
            else:
                host = headers.get("host", "").split(":")[0]
                await http_reply(writer, "200 OK", json_bytes(build_links(host)), "application/json")
        elif path in ("/", "/index.html"):
            await http_reply(writer, "200 OK", render_page(), "text/html; charset=utf-8")
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
