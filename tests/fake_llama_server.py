"""Stand-in for llama-server, so ordinary tests exercise the pipeline without weights.

Its answers come from the test, so tests using it say nothing about model quality.
"""

import json
import os
import sys
import zlib
from http.server import BaseHTTPRequestHandler, HTTPServer

DIMENSIONS = 64


def word_vector(text: str) -> list[float]:
    vector = [0.0] * DIMENSIONS
    for word in text.lower().replace("`", " ").split():
        vector[zlib.crc32(word.encode()) % DIMENSIONS] += 1.0
    return vector


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._reply(200, {"status": "ok"})

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        log = os.environ.get("FAKE_LLAMA_LOG")
        if log:
            with open(log, "a") as file:
                file.write(json.dumps({"path": self.path, "body": request}) + "\n")

        if self.path == "/v1/embeddings":
            if os.environ.get("FAKE_LLAMA_FAIL") == "embedding":
                return self._reply(500, {"error": "embedding failed"})
            data = [
                {"index": index, "embedding": word_vector(text)}
                for index, text in enumerate(request["input"])
            ]
            return self._reply(200, {"data": data})

        failure = os.environ.get("FAKE_LLAMA_FAIL")
        if failure == "chat":
            return self._reply(500, {"error": "inference failed"})
        if failure == "context":
            return self._reply(
                400, {"error": {"type": "exceed_context_size_error", "code": 400}}
            )
        if failure == "malformed":
            return self._reply(200, {"unexpected": True})

        content = os.environ.get("FAKE_LLAMA_REPLY", "")
        self._reply(
            200,
            {
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 100},
            },
        )

    def _reply(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    if os.environ.get("FAKE_LLAMA_FAIL") == "load":
        sys.exit(7)
    port = int(sys.argv[sys.argv.index("--port") + 1])
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
