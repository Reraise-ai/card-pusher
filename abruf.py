#!/usr/bin/env python3
"""Abruf-Endpunkt: 'gib mir zu diesem Text die Top-Karten'.

Duenne Huelle vor QMD (BM25-Hybridsuche, a card collection).
Start:   python ./abruf.py
Port:    127.0.0.1:8791 (nur localhost)
API:     POST /top  {"query": "...", "k": 5}  ->  {"results": [{docid, score, file, line, title, snippet}], "elapsed_ms": N}
         GET  /health                        ->  {"ok": true}
Stdlib-only, keine externen Abhaengigkeiten.
"""
import json
import os
import re
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "127.0.0.1"
PORT = int(os.environ.get("MEMORY_PROXY_ABRUF_PORT", "8791"))
QMD = os.environ.get("MEMORY_PROXY_QMD", os.path.join(os.environ.get("LOCALAPPDATA", ""), "hermes", "node", "qmd.cmd"))
QMD_CACHE_ENV = os.environ.get("MEMORY_PROXY_QMD_CACHE", r"./qmd-cache")
COLLECTION = os.environ.get("MEMORY_PROXY_COLLECTION", "cards")
DEFAULT_K = 5

# Deutsche+englische Stoppwoerter: qmd-BM25 braucht Stichworte, keine Fragesaetze.
STOPWORDS = set("""aber alle allem allem allem als also am an ander andere anderem anders auch auf aus
bei bin bis bist da damit dann das dass dein deine dem den denn der des deren die dies dieser diese
dieses doch dort du durch ein eine einem einen einer eines er es euer eure fÃ¼r gab hatte hatten hier
hin hinter ich ihm ihn ihnen ihr ihre im in indem ist ja je jede jedem jeden jeder jedes jene jetzt
kann kein keine koennen koennen koennen koennen machen man manche mein meine mir mit muss nach nachdem
neben nicht nichts noch nun nur ob oder ohne ohne sehr sei sein seine selbst sich sie sind so solche
soll sowie sondern statt um und uns unser unsere unter vom von vor warum was weiter weitere wenn werde
werden wie wieder will wir wird wirst wo woher wollen wollte wÃ¤hrend was weshalb wie whom whose what
why when where who which the a an and or of to in on for with is are was were be been being this that
these those it its as at by from has have had do does did not no you your we our they their he she his
her him us them i me my
bitte mal gib zeig zeige sag sage nenne nenn lege leg neue neuen neuer neues meinem meiner meinen mich dich dir dein deinem
genau genauen befehl frage antwort liegt liegen pfad pfade eingespielten eingespielte voll vollen steht stehen finde finden stuetzt stÃ¼tze stÃ¼tzt stuetze welche welcher welches gibt geht""".split())


def stichworte(query):
    """Fragesatz -> Stichwortliste (fuer BM25). Reihenfolge bleibt erhalten."""
    words = [w.strip(".,!?;:()[]{}\\\"'â€žâ€œ?â€žâ€œ") for w in query.split()]
    return " ".join(w for w in words if w and w.lower() not in STOPWORDS)


def stichworte_kurz(query):
    """Nur die ersten N Nicht-Stoppwoerter (weniger Terme = trefferfreudigeres BM25)."""
    worte = stichworte(query).split()
    return " ".join(worte[:3])

# Fragment aus qmd --json Snippet: '@@ -3,4 @@ (2 before, 38 after)\ntyp: konzept\n...'
SNIPPET_MARKER = re.compile(r"@@[^@]*?@@")
PAREN_TAIL = re.compile(r"^\s*\([^)]*\)\s*")


def _clean_snippet(raw):
    if not raw:
        return ""
    s = SNIPPET_MARKER.sub("", raw)
    s = PAREN_TAIL.sub("", s)
    return s.strip()


def qmd_search(query, k):
    """Ruft `qmd search --json` auf. Wirft bei Problemen -> Fail-silent im Handler."""
    env = dict(os.environ, XDG_CACHE_HOME=QMD_CACHE_ENV)
    cmd = [QMD, "search", query, "-c", COLLECTION, "-n", str(k), "--json"]  # -n = Anzahl (qmd 2.8.3 kennt kein -k)
    proc = subprocess.run(
        cmd, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=20,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"qmd rc={proc.returncode}: {proc.stderr.strip()[:200]}")
    try:
        results = json.loads(proc.stdout)
    except ValueError:  # qmd schreibt "No results found." als Text
        return []
    out = []
    for r in results:
        out.append({
            "docid": r.get("docid"),
            "score": r.get("score"),
            "file": r.get("file"),
            "line": r.get("line"),
            "title": r.get("title"),
            "snippet": _clean_snippet(r.get("snippet")),
        })
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # ruhige Logs
        sys.stderr.write("[abruf] %s %s\n" % (self.command, self.path))

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send(200, {"ok": True})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/top":
            self._send(404, {"error": "not found"})
            return
        t0 = time.time()
        try:
            n = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(n) or b"{}")
            query = str(payload.get("query") or "").strip()
            k = int(payload.get("k") or DEFAULT_K)
            if not query:
                self._send(400, {"error": "query fehlt"})
                return
            # BM25 braucht wenige Stichworte (gemessen: 2-3 Terme -> Treffer, 4+ -> 0).
            # Fallback-Kette: 3 Stichworte -> 6 Stichworte -> alle Stichworte -> Original-Frage.
            k_max = max(1, min(k, 20))
            worte = stichworte(query).split()
            varianten = []
            for n in (3, 6, 12):
                if worte[:n]:
                    varianten.append(" ".join(worte[:n]))
            varianten.append(query)
            # 16.10.2026: Hauptsuche (wenige Stichworte) UND die spezifischsten Einzelwoerter parallel,
            # Treffer reihum zusammenfuehren. So drueckt ein haeufiges Wort ("Karten") die eigentliche
            # Sache ("Rechnung", "Edenor") nicht aus der Auswahl.
            spezifisch = sorted({w for w in worte if len(w) >= 4}, key=len, reverse=True)[:4]
            suchen = [v.strip() for v in varianten[:1] if v.strip()] + [w for w in spezifisch if w not in varianten[:1]]
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=5) as ex:
                listen = list(ex.map(lambda q: qmd_search(q, k_max), suchen))
            if not any(listen) and len(varianten) > 1:
                listen = [qmd_search(varianten[-1], k_max)]
            results, gesehen = [], set()
            for i in range(k_max):
                for liste in listen:
                    if i < len(liste) and liste[i]["docid"] not in gesehen:
                        gesehen.add(liste[i]["docid"]); results.append(liste[i])
            results = results[:k_max]
            self._send(200, {"results": results,
                             "elapsed_ms": int((time.time() - t0) * 1000)})
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write("[abruf] FEHLER: %r\n" % exc)
            self._send(502, {"error": str(exc)[:200]})


def main():
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    sys.stderr.write(f"[abruf] lauscht auf http://{HOST}:{PORT} (qmd: {QMD}, collection: {COLLECTION})\n")
    srv.serve_forever()


if __name__ == "__main__":
    main()

