#!/usr/bin/env python3
"""Memory Proxy - Reverse-Proxy im OpenAI-Format (chat_completions).

Prinzip (Push statt Pull, Wiki the design note): Vor jeder LLM-Anfrage fragt
der Proxy den Abruf-Endpunkt (QMD-Huelle) und spritzt die Top-Karten in die
System-Nachricht. Fail-silent: haengt der Abruf, leitet der Proxy ohne
Einspritzung weiter. Kein Agent steht je wegen des Gedaechtnisses.

Nous-Auth-Durchreich: der eingehende Authorization-Header (Bearer JWT fuer
Nous/OpenRouter/Codex) wird an den Upstream weitergereicht. Ohne diesen Header
schlaegt die Upstream-API mit 401 fehl. Siehe the design note "Rollout":
base_url der Hermes-Profile wird pro Rolle umgestellt.

Start:   python ./proxy.py
Port:    127.0.0.1:8787 (nur localhost)
Upstream: env MEMORY_PROXY_UPSTREAM (Default http://127.0.0.1:11434/v1 -> Ollama)
Abruf:    env MEMORY_PROXY_ABRUF   (Default http://127.0.0.1:8791/top)
Top-K:    env MEMORY_PROXY_K       (Default 5)

Stdlib-only. KEINE Aenderung an Hermes-Profilen â€” die Umbiegung von base_url
erfolgt spaeter separat, Rolle fuer Rolle.
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
PORT = int(os.environ.get("MEMORY_PROXY_PORT", "8787"))

UPSTREAM = os.environ.get("MEMORY_PROXY_UPSTREAM", "http://127.0.0.1:11434").rstrip("/")
if UPSTREAM.endswith("/v1"):  # self.path liefert /v1/... mit -> kein doppeltes /v1
    UPSTREAM = UPSTREAM[:-3]
ABRUF_URL = os.environ.get("MEMORY_PROXY_ABRUF", "http://127.0.0.1:8791/top")
TOP_K = int(os.environ.get("MEMORY_PROXY_K", "5"))
ABRUF_TIMEOUT = float(os.environ.get("MEMORY_PROXY_ABRUF_TIMEOUT", "25"))
INJECT_HEADER = "x-memory-proxy"

# Headers forwarded verbatim from the incoming request to the upstream model API.
# Authorization is required for OAuth/bearer-token providers (Nous, OpenRouter, Codex);
# the proxy must NOT strip it or the upstream returns 401. See the design note
# "Rollout": base_url auf 127.0.0.1:8787.
FORWARDED_HEADERS = ("Authorization", "OpenAI-Beta", "OpenAI-Beta-",
                      "X-Organization-Organization", "HTTP-Referer")

WISSEN_PREFIX = (
    "Injiziertes Wissen aus der Wissensschicht (Memory-Proxy, automatisch). "
    "Relevante Karten zu dieser Anfrage:\n"
)


def _forwarded_auth_headers(headers):
    """Sammelt Auth- und Provider-Header vom eingehenden Request fuer die Upstream-Weitergabe.

    Der Proxy darf diese Header nicht kreuzen â€” ohne Authorization erhaelt der Upstream
    (z.B. Nous OAuth Bearer) HTTP 401. Siehe the design note "Rollout": the first role
    zuerst, base_url auf 127.0.0.1:8787.
    """
    out = {}
    for h in FORWARDED_HEADERS:
        val = headers.get(h) or headers.get(h.lower())
        if val:
            out[h] = val
    return out


def _fetch_top_karten(query, k):
    """Holt Top-Karten vom Abruf-Endpunkt. Wirft bei Problemen (Fail-silent faengt es)."""
    payload = json.dumps({"query": query, "k": k}).encode("utf-8")
    req = urllib.request.Request(
        ABRUF_URL, data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=ABRUF_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("results") or []


def _karten_block(results):
    if not results:
        return None
    lines = [WISSEN_PREFIX]
    for i, r in enumerate(results, 1):
        title = r.get("title") or "(ohne Titel)"
        docid = r.get("docid") or "?"
        src = r.get("file") or "?"
        snip = (r.get("snippet") or "").strip()
        lines.append(f"[{i}] {title} ({docid}, Quelle: {src})")
        if snip:
            lines.append(f"    {snip[:400]}")
    return "\n".join(lines)


def _last_user_text(messages):
    """Qualitaets-Hebel Stufe 1: die letzte Nutzerzeile als Suchfrage."""
    for msg in reversed(messages or []):
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = msg.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                return " ".join(parts)
    return ""


def _inject(messages):
    """Karten in die erste System-Nachricht spritzen. Gibt (messages, block) zurueck."""
    query = _last_user_text(messages)
    if not query:
        return messages, None
    results = _fetch_top_karten(query, TOP_K)
    block = _karten_block(results)
    if not block:
        return messages, None
    messages = list(messages)
    injected = False
    for i, msg in enumerate(messages):
        if msg.get("role") == "system":
            msg = dict(msg)
            msg["content"] = str(msg.get("content", "")) + "\n\n" + block
            messages[i] = msg
            injected = True
            break
    if not injected:
        messages.insert(0, {"role": "system", "content": block})
        injected = True
    return messages, block


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[proxy] %s %s\n" % (self.command, self.path))

    def _relay(self, body_bytes, extra_headers=None):
        """Leitet die Anfrage an das echte Modell weiter und streamt die Antwort zurueck.

        Authorization-Header (Nous-Auth-Durchreich) wird an den Upstream
        durchgereicht â€” siehe Modul-Docstring.
        """
        url = UPSTREAM + self.path
        req_headers = {
            "Content-Type": "application/json",
            "Accept": self.headers.get("Accept", "*/*"),
        }
        req_headers.update(_forwarded_auth_headers(self.headers))
        req = urllib.request.Request(
            url, data=body_bytes, method="POST",
            headers=req_headers,
        )
        try:
            upstream = urllib.request.urlopen(req, timeout=600)
        except urllib.error.HTTPError as exc:
            err = exc.read()
            self.send_response(exc.code)
            self.send_header("Content-Type", exc.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(err)))
            self.send_header("Connection", "close")
            self.close_connection = True
            for k, v in (extra_headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(err)
            return
        self.send_response(upstream.status)
        ct = upstream.headers.get("Content-Type", "application/json")
        self.send_header("Content-Type", ct)
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        if "text/event-stream" in ct:
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            while True:
                chunk = upstream.read(4096)
                if not chunk:
                    break
                self.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
        else:
            data = upstream.read()
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.close_connection = True
            self.end_headers()
            self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({"ok": True, "upstream": UPSTREAM, "abruf": ABRUF_URL, "k": TOP_K}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        # andere GETs (z.B. /v1/models) transparent weiterleiten
        try:
            req_headers = _forwarded_auth_headers(self.headers)
            req = urllib.request.Request(UPSTREAM + self.path, headers=req_headers,
                                         method="GET")
            upstream = urllib.request.urlopen(req, timeout=30)
            data = upstream.read()
            self.send_response(upstream.status)
            self.send_header("Content-Type", upstream.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception as exc:  # noqa: BLE001
            body = json.dumps({"error": str(exc)}).encode()
            self.send_response(502)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def do_POST(self):
        t0 = time.time()
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        if not self.path.endswith("/chat/completions"):
            self._relay(body)
            return
        try:
            payload = json.loads(body)
        except Exception:  # noqa: BLE001
            self._relay(body)
            return

        injected = False
        block = None
        try:
            messages, block = _inject(payload.get("messages"))
            payload["messages"] = messages
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            injected = block is not None
        except Exception as exc:  # noqa: BLE001
            # Fail-silent: haengt der Abruf/QMD, geht die Anfrage unveraendert weiter.
            sys.stderr.write("[proxy] ABRUF FEHLGESCHLAGEN (fail-silent): %r\n" % exc)

        # Erste Karte-Zeile aus dem injizierten Block (Zeile 0 = WISSEN_PREFIX).
        karte_label = "-"
        if block:
            sl = block.splitlines()
            # WISSEN_PREFIX endet mit \n -> nach join zusaetzlich \n -> Leerzeile.
            # Finde die erste Zeile, die mit '[' beginnt (eine Karte).
            for _line in sl:
                if _line.startswith("["):
                    karte_label = _line[:60]
                    break
        sys.stderr.write("[proxy] inject=%s karte=%s ms=%d\n" % (
            injected, karte_label, int((time.time() - t0) * 1000)))
        self._relay(body, {"x-memory-proxy": "injected" if injected else "passthrough"})


def main():
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    sys.stderr.write(f"[proxy] lauscht auf http://{HOST}:{PORT} -> {UPSTREAM} (abruf: {ABRUF_URL}, top-k: {TOP_K})\n")
    srv.serve_forever()


if __name__ == "__main__":
    main()

