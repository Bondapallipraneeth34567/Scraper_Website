"""Tiny local web console for the authorised human to solve a paused
CAPTCHA. No framework -- stdlib http.server only.

  python -m mandi_pipeline.operator_console

Security model: binds to OPERATOR_CONSOLE_HOST (127.0.0.1 by default) and
requires OPERATOR_CONSOLE_TOKEN on every request. To use it from a laptop,
SSH-tunnel to the VM:

  ssh -L 8770:127.0.0.1:8770 azureuser@<vm>
  # then open http://127.0.0.1:8770/?token=<OPERATOR_CONSOLE_TOKEN>

It reads/writes only files in CAPTCHA_STATE_DIR:
  <id>.png / <id>.json   written by the pipeline (pending challenges)
  <id>.answer            "the six characters"     -> pipeline submits it
  <id>.skip              (empty)                  -> skip this commodity
  <id>.reject            (empty)                  -> pipeline fetches a new image
"""
from __future__ import annotations

import html
import json
import secrets
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import settings

DIR: Path = settings.captcha_state_dir


def _authed(qs: dict) -> bool:
    tok = (qs.get("token") or [""])[0]
    want = settings.operator_console_token
    return bool(want) and secrets.compare_digest(tok, want)


def _pending() -> list[dict]:
    items = []
    for meta in sorted(DIR.glob("*.json")):
        try:
            d = json.loads(meta.read_text(encoding="utf-8"))
        except Exception:
            continue
        if d.get("status") == "pending" and (DIR / f"{d['id']}.png").exists():
            items.append(d)
    return items


_PAGE = """<!doctype html><meta charset=utf-8>
<title>mandi_pipeline · CAPTCHA console</title>
<style>
 body{{font:14px/1.5 system-ui,sans-serif;margin:2rem;max-width:680px}}
 .c{{border:1px solid #ccc;border-radius:8px;padding:1rem;margin:1rem 0}}
 img{{border:1px solid #999;image-rendering:pixelated;transform:scale(2);transform-origin:left;margin:12px 0 20px}}
 input[type=text]{{font-size:18px;padding:6px 8px;letter-spacing:2px;width:180px}}
 button{{font-size:14px;padding:6px 12px;margin-left:6px;cursor:pointer}}
 .muted{{color:#666}} .none{{color:#080}}
</style>
<h2>CAPTCHA console</h2>
<p class=muted>{count} pending. This page auto-refreshes every 10s.</p>
{body}
<meta http-equiv=refresh content=10>
"""

_CARD = """<div class=c>
 <b>{commodity}</b> <span class=muted>(commodity {cid}, date {date})</span><br>
 <span class=muted>id {id} · key expires {exp}</span><br>
 <img src="/img/{id}?token={token}" alt="captcha">
 <form method=post action="/solve">
   <input type=hidden name=id value="{id}">
   <input type=hidden name=token value="{token}">
   <input type=text name=answer autocomplete=off autocapitalize=off spellcheck=false autofocus>
   <button name=action value=answer>Submit</button>
   <button name=action value=reject formnovalidate>Unreadable</button>
   <button name=action value=skip formnovalidate>Skip</button>
 </form>
</div>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "mandi_pipeline_console"

    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *a):  # quieter
        pass

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        if not _authed(qs):
            self._send(403, b"forbidden: bad or missing ?token=")
            return

        if parsed.path == "/":
            token = html.escape(qs["token"][0])
            pend = _pending()
            cards = "\n".join(
                _CARD.format(
                    commodity=html.escape(str(d.get("context", {}).get("commodity_name", "?"))),
                    cid=html.escape(str(d.get("context", {}).get("commodity_id", "?"))),
                    date=html.escape(str(d.get("context", {}).get("target_date", "?"))),
                    id=html.escape(d["id"]),
                    exp=html.escape(str(d.get("expires_at", "?"))),
                    token=token,
                )
                for d in pend
            ) or "<p class=none>Nothing pending. The pipeline is not waiting on you.</p>"
            self._send(200, _PAGE.format(count=len(pend), body=cards).encode("utf-8"))
            return

        if parsed.path.startswith("/img/"):
            cid = parsed.path[len("/img/"):]
            png = DIR / f"{Path(cid).name}.png"
            if not png.exists():
                self._send(404, b"no such image")
                return
            self._send(200, png.read_bytes(), "image/png")
            return

        self._send(404, b"not found")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
        if not _authed(form):
            self._send(403, b"forbidden")
            return
        cid = Path((form.get("id") or [""])[0]).name
        action = (form.get("action") or [""])[0]
        meta = DIR / f"{cid}.json"
        if not cid or not meta.exists():
            self._send(400, b"unknown id")
            return

        if action == "answer":
            ans = (form.get("answer") or [""])[0].strip()
            if not ans:
                self._send(400, b"empty answer")
                return
            (DIR / f"{cid}.answer").write_text(ans, encoding="utf-8")
        elif action == "skip":
            (DIR / f"{cid}.skip").write_text("", encoding="utf-8")
        elif action == "reject":
            (DIR / f"{cid}.reject").write_text("", encoding="utf-8")
        else:
            self._send(400, b"bad action")
            return

        token = urllib.parse.quote((form.get("token") or [""])[0])
        self.send_response(303)
        self.send_header("Location", f"/?token={token}")
        self.end_headers()


def main() -> None:
    if not settings.operator_console_token:
        raise SystemExit("OPERATOR_CONSOLE_TOKEN is not set -- refusing to start an unauthenticated console")
    DIR.mkdir(parents=True, exist_ok=True)
    addr = (settings.operator_console_host, settings.operator_console_port)
    httpd = ThreadingHTTPServer(addr, Handler)
    print(f"operator console on http://{addr[0]}:{addr[1]}/?token=<OPERATOR_CONSOLE_TOKEN>")
    print(f"watching {DIR}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
