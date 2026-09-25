---
name: web-services
description: Building and testing HTTP servers/APIs and clients, running background processes, ports and health checks.
---
## Building
- Stdlib server: `http.server.BaseHTTPRequestHandler` + `ThreadingHTTPServer`; implement `do_GET`/`do_POST`; read body with `int(self.headers.get('Content-Length', 0))`; reply with `send_response`, `send_header('Content-Type','application/json')`, `end_headers`, `wfile.write(json.dumps(x).encode())`. Return 400 for bad JSON, 404 for unknown paths.
- Take host/port from argv or env when the task says so; bind `127.0.0.1` unless told otherwise.

## Running & testing (never block the terminal)
1. Start in background: `nohup python3 server.py 8080 > server.log 2>&1 & echo $! > server.pid`
2. Wait until ready: `for i in $(seq 30); do curl -s localhost:8080/health && break; sleep 0.5; done`
3. Test every endpoint with `curl -s -X POST -H 'Content-Type: application/json' -d '{...}' localhost:8080/path`, including error cases.
4. On failure check `cat server.log`.
5. Stop it when done unless told to keep it running: `kill $(cat server.pid)`.
