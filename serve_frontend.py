from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import request
import os
import sys


ROOT = Path(__file__).resolve().parent
GRAPHQL_URL = "https://agents-api.vara.network/graphql"


class OnyxHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        if self.path in {"/health", "/healthz", "/ping"}:
            payload = b'{"ok":true,"service":"onyx"}'
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("cache-control", "no-store")
            self.send_header("content-length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        super().do_GET()

    def do_POST(self):
        if self.path != "/api/graphql":
            self.send_error(404, "Not found")
            return

        try:
            length = int(self.headers.get("content-length", "0"))
            body = self.rfile.read(length)
            upstream = request.Request(
                GRAPHQL_URL,
                data=body,
                headers={
                    "content-type": "application/json",
                    "accept": "application/json",
                    "user-agent": "OnyxLocalDev/1.0",
                },
                method="POST",
            )
            with request.urlopen(upstream, timeout=20) as response:
                payload = response.read()
                status = response.status
                content_type = response.headers.get("content-type", "application/json")
        except Exception as exc:
            payload = f'{{"errors":[{{"message":"GraphQL proxy failed: {exc}"}}]}}'.encode()
            status = 502
            content_type = "application/json"

        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(payload)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")
    server = ThreadingHTTPServer((host, port), OnyxHandler)
    print(f"Serving Onyx on http://{host}:{port}/frontend/index.html")
    print("GraphQL proxy enabled at /api/graphql")
    print("Health check enabled at /health")
    server.serve_forever()


if __name__ == "__main__":
    main()
