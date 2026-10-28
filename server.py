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
NAME = os.environ.get("CONFIG_NAME", "BLACK SPACE")
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
<meta name="theme-color" content="#000000">
<title>__NAME__</title>
<style>
:root{--a1:#ffb347;--a2:#ff5e62;--a3:#7c8cff;--a4:#9ef0ff;--fg:#f2f4ff;--mut:#7f86a3}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{height:100%}
body{margin:0;background:#000;color:var(--fg);overflow:hidden;
  font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  padding:env(safe-area-inset-top,0) 0 env(safe-area-inset-bottom,0)}
#fx{position:fixed;inset:0;z-index:0;pointer-events:none}
.nebula{position:fixed;inset:0;z-index:0;pointer-events:none;
  background:
   radial-gradient(60% 40% at 15% 90%,rgba(124,140,255,.10),transparent 70%),
   radial-gradient(50% 35% at 90% 10%,rgba(255,94,98,.08),transparent 70%)}
.vig{position:fixed;inset:0;z-index:1;pointer-events:none;
  background:radial-gradient(circle at 50% 42%,transparent 40%,rgba(0,0,0,.75) 100%)}
main{position:relative;z-index:2;height:100%;display:flex;flex-direction:column;align-items:center;
  justify-content:center;text-align:center;padding:20px 52px 20px 20px}

/* ---------- black hole ---------- */
.bh{position:relative;width:min(74vw,320px);aspect-ratio:1;margin-bottom:34px;perspective:700px;
  animation:float 9s ease-in-out infinite}
@keyframes float{50%{transform:translateY(-8px)}}
.bh .halo{position:absolute;inset:-18%;border-radius:50%;
  background:radial-gradient(circle,rgba(255,150,60,.34) 0%,rgba(255,94,98,.16) 38%,transparent 66%);
  animation:breathe 5s ease-in-out infinite}
@keyframes breathe{50%{opacity:.65;transform:scale(1.07)}}
.bh .disk,.bh .arc{position:absolute;inset:0;border-radius:50%;
  background:conic-gradient(from 0deg,#ffb347,#fff3d6,#ff5e62,#7c8cff,#9ef0ff,#ff5e62,#ffb347);
  -webkit-mask-image:radial-gradient(circle,transparent 31%,#000 35%,#000 50%,transparent 70%);
  mask-image:radial-gradient(circle,transparent 31%,#000 35%,#000 50%,transparent 70%)}
.bh .disk{transform:rotateX(74deg);animation:diskspin 7s linear infinite;filter:saturate(1.25) drop-shadow(0 0 14px rgba(255,150,60,.8))}
@keyframes diskspin{from{transform:rotateX(74deg) rotateZ(0)}to{transform:rotateX(74deg) rotateZ(360deg)}}
.bh .arc{inset:6%;clip-path:inset(0 0 52% 0);animation:arcspin 7s linear infinite;
  filter:saturate(1.25) drop-shadow(0 -2px 12px rgba(255,190,120,.9));opacity:.95}
@keyframes arcspin{to{transform:rotate(360deg)}}
.bh .arc{-webkit-mask-image:radial-gradient(circle,transparent 46%,#000 49%,#000 58%,transparent 72%);
  mask-image:radial-gradient(circle,transparent 46%,#000 49%,#000 58%,transparent 72%)}
.bh .core{position:absolute;left:50%;top:50%;width:34%;aspect-ratio:1;transform:translate(-50%,-50%);border-radius:50%;
  background:#000;box-shadow:0 0 0 2px rgba(255,214,150,.75),0 0 28px 6px rgba(255,150,60,.55),inset 0 0 22px #000}

h1{margin:0;font-weight:200;font-size:clamp(26px,8.4vw,44px);letter-spacing:.42em;text-indent:.42em;text-transform:uppercase;
  background:linear-gradient(100deg,#fff 20%,var(--a1) 45%,#fff 55%,var(--a4) 80%);background-size:250% 100%;
  -webkit-background-clip:text;background-clip:text;color:transparent;animation:shimmer 7s ease-in-out infinite alternate;
  filter:drop-shadow(0 0 18px rgba(255,179,71,.25))}
@keyframes shimmer{to{background-position:100% 0}}
.sub{margin:10px 0 26px;color:var(--mut);font-size:11px;letter-spacing:.42em;text-indent:.42em;text-transform:uppercase}
.stat{display:flex;align-items:center;justify-content:center;flex-wrap:wrap;gap:6px 0;font-size:11px;
  letter-spacing:.2em;text-transform:uppercase;color:var(--mut)}
.stat span{padding:0 14px}
.stat span+span{border-left:1px solid rgba(255,255,255,.14)}
.stat b{font-weight:600;color:var(--fg)}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:#5dffa8;margin-right:8px;
  box-shadow:0 0 10px #5dffa8;animation:blink 1.6s ease-in-out infinite;vertical-align:1px}
@keyframes blink{50%{opacity:.25}}
.hint{position:absolute;left:0;right:0;bottom:calc(22px + env(safe-area-inset-bottom,0));text-align:center;
  color:#4d5370;font-size:10px;letter-spacing:.36em;text-transform:uppercase}

/* ---------- side tab ||| ---------- */
#tab{position:fixed;right:0;top:50%;transform:translateY(-50%);z-index:6;width:40px;height:96px;padding:0;
  display:flex;align-items:center;justify-content:center;gap:5px;cursor:pointer;
  background:rgba(12,12,18,.55);border:1px solid rgba(255,179,71,.45);border-right:0;border-radius:14px 0 0 14px;
  backdrop-filter:blur(10px);-webkit-backdrop-filter:blur(10px);box-shadow:0 0 22px rgba(255,150,60,.28);
  animation:tabpulse 3.2s ease-in-out infinite}
#tab span{display:block;width:3px;height:40px;border-radius:2px;background:linear-gradient(var(--a1),var(--a2))}
#tab span:nth-child(2){height:54px;background:linear-gradient(var(--a4),var(--a3))}
@keyframes tabpulse{50%{box-shadow:0 0 34px rgba(124,140,255,.45)}}

/* ---------- drawer ---------- */
#scrim{position:fixed;inset:0;z-index:7;background:rgba(0,0,0,.72);opacity:0;pointer-events:none;transition:opacity .3s}
#scrim.on{opacity:1;pointer-events:auto}
#drawer{position:fixed;top:0;right:0;bottom:0;z-index:8;width:min(90vw,390px);transform:translateX(104%);
  transition:transform .4s cubic-bezier(.2,.9,.2,1);display:flex;flex-direction:column;overflow-y:auto;
  padding:calc(20px + env(safe-area-inset-top,0)) 20px calc(20px + env(safe-area-inset-bottom,0));
  background:linear-gradient(180deg,#07070c,#000);border-left:1px solid rgba(255,179,71,.4);
  box-shadow:-24px 0 70px rgba(255,120,60,.18)}
#drawer.open{transform:none}
.dh{display:flex;align-items:center;justify-content:space-between;margin-bottom:6px}
.dh h2{margin:0;font-size:12px;font-weight:600;letter-spacing:.42em;color:var(--a1)}
#x{width:34px;height:34px;border-radius:50%;border:1px solid rgba(255,255,255,.22);background:transparent;color:var(--fg);font-size:20px;line-height:1}
.dp{color:var(--mut);font-size:12px;margin:0 0 20px;letter-spacing:.04em}
.btn{width:100%;padding:16px;border:0;border-radius:999px;color:#07070c;font:700 14px -apple-system,Segoe UI,Roboto,sans-serif;
  letter-spacing:.34em;text-indent:.34em;text-transform:uppercase;
  background:linear-gradient(90deg,var(--a1),var(--a2),var(--a3),var(--a4),var(--a1));background-size:300% 100%;
  animation:shimmer 5s linear infinite alternate;box-shadow:0 0 28px rgba(255,150,60,.45)}
.btn:active{transform:scale(.98)}
.btn:disabled{opacity:.65}
.btn.sm{padding:12px;font-size:12px;margin-top:10px}
#key{width:100%;margin-bottom:12px;padding:14px;border-radius:999px;border:1px solid rgba(255,179,71,.45);
  background:#0b0b12;color:var(--fg);font:15px ui-monospace,Menlo,monospace;text-align:center;letter-spacing:.3em}
#err{min-height:18px;margin:12px 0 0;color:var(--a2);font-size:11px;text-align:center;letter-spacing:.28em}
#links{display:none}
#links.show{display:block;animation:rise .5s ease}
@keyframes rise{from{opacity:0;transform:translateY(16px)}}
.card{margin-top:14px;padding:16px;border-radius:18px;border:1px solid rgba(255,179,71,.28);background:#0a0a11}
.card.alt{border-color:rgba(124,140,255,.38)}
.card h3{margin:0 0 2px;font-size:12px;font-weight:600;letter-spacing:.26em;color:var(--a1)}
.card.alt h3{color:var(--a4)}
.card p{margin:0 0 10px;font-size:11px;color:var(--mut);letter-spacing:.2em}
textarea{width:100%;height:96px;resize:none;border-radius:12px;border:1px solid rgba(255,255,255,.1);background:#000;
  color:#d7defa;padding:10px;font:11px/1.4 ui-monospace,Menlo,monospace;word-break:break-all}
.note{margin-top:12px;font-size:11px;color:#ffd27a;text-align:center}
.hide{margin-top:18px;width:100%;padding:12px;border-radius:999px;background:transparent;border:1px solid rgba(255,255,255,.2);
  color:var(--mut);font:11px -apple-system,Segoe UI,sans-serif;letter-spacing:.3em;text-transform:uppercase}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style></head>
<body>
<canvas id="fx"></canvas><div class="nebula"></div><div class="vig"></div>
<main>
  <div class="bh"><div class="halo"></div><div class="disk"></div><div class="core"></div><div class="arc"></div></div>
  <h1>__NAME__</h1>
  <div class="sub">beyond the event horizon</div>
  <div class="stat">
    <span><i class="dot"></i><b>Online</b></span>
    <span>Ping <b id="ping">--</b></span>
    <span>WS <b>Ready</b></span>
  </div>
  <div class="hint">tap <b>|||</b> on the right edge</div>
</main>

<button id="tab" aria-label="Open menu"><span></span><span></span><span></span></button>
<div id="scrim"></div>
<aside id="drawer" aria-hidden="true">
  <div class="dh"><h2>ACCESS</h2><button id="x" aria-label="Close">&times;</button></div>
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


/* starfield + shooting stars */
(function(){
  if(window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches)return;
  var c=$('#fx'),x=c.getContext('2d'),W,H,dpr=Math.min(window.devicePixelRatio||1,2),S=[],N=130,shoot=null,next=Date.now()+2500;
  var tint=['255,255,255','255,236,200','190,210,255','255,200,180'];
  function size(){W=innerWidth;H=innerHeight;c.width=W*dpr;c.height=H*dpr;x.setTransform(dpr,0,0,dpr,0,0)}
  size();addEventListener('resize',size);
  for(var i=0;i<N;i++)S.push({x:Math.random()*W,y:Math.random()*H,z:Math.random()*.9+.1,t:Math.random()*6.28,
    s:Math.random()*1.6+.4,c:tint[i%4]});
  function f(){
    if(!document.hidden){
      x.clearRect(0,0,W,H);
      for(var i=0;i<N;i++){var a=S[i];a.t+=.02*a.z+.01;a.y+=.06*a.z;if(a.y>H){a.y=-2;a.x=Math.random()*W}
        var al=.35+.65*Math.abs(Math.sin(a.t));
        x.fillStyle='rgba('+a.c+','+(al*a.z).toFixed(3)+')';
        x.beginPath();x.arc(a.x,a.y,a.s*a.z,0,6.283);x.fill()}
      var now=Date.now();
      if(!shoot&&now>next){shoot={x:Math.random()*W*.8+W*.2,y:Math.random()*H*.35,l:0};}
      if(shoot){shoot.x-=11;shoot.y+=5;shoot.l+=1;
        var g=x.createLinearGradient(shoot.x,shoot.y,shoot.x+70,shoot.y-32);
        g.addColorStop(0,'rgba(255,255,255,.95)');g.addColorStop(1,'rgba(255,255,255,0)');
        x.strokeStyle=g;x.lineWidth=1.6;x.beginPath();x.moveTo(shoot.x,shoot.y);x.lineTo(shoot.x+70,shoot.y-32);x.stroke();
        if(shoot.l>70||shoot.x<-80){shoot=null;next=now+3500+Math.random()*5000}}
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
