"""Лёгкий preview-сервер для проверки фронта без установки тяжёлых зависимостей.

Раздаёт frontend/, стабит /api/health. Не для продакшена — только UI-верификация.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
FRONTEND = ROOT / "frontend"


class Handler(BaseHTTPRequestHandler):
    def _serve_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self.send_error(404, f"Not found: {path.name}")
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj: dict, status: int = 200) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        p = self.path.split("?")[0]
        if p == "/" or p == "/index.html":
            self._serve_file(FRONTEND / "index.html", "text/html; charset=utf-8")
        elif p in ("/spellcheck.html", "/legal.html", "/letter.html"):
            self._serve_file(FRONTEND / "views" / p.lstrip("/"), "text/html; charset=utf-8")
        elif p.startswith("/static/"):
            rel = p[len("/static/"):]
            ext_map = {".css": "text/css; charset=utf-8", ".js": "application/javascript; charset=utf-8"}
            path = FRONTEND / rel
            ctype = ext_map.get(path.suffix, "application/octet-stream")
            self._serve_file(path, ctype)
        elif p == "/api/health":
            self._json({
                "ok": False,
                "ollama": {
                    "ok": False,
                    "model": "qwen2.5:14b-instruct-q4_K_M",
                    "warning": "Preview-режим: Ollama не подключена (это UI-верификация)",
                    "installed": [],
                },
                "rag_ready": False,
            })
        elif p.startswith("/api/tasks"):
            self._json({"status": "queued", "result": None, "error": None, "progress": ""})
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        self._json({"error": "Preview-режим: backend не запущен"}, status=503)

    def log_message(self, format, *args):
        return  # тихо


def main() -> None:
    port = 8765
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Preview server on http://127.0.0.1:{port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
