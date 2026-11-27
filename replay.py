"""在本机提供演练参与方的只读输入。"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parent


def open_stream(name):
    if not name.replace("_", "").isalnum():
        raise ValueError("资料名称无效")
    return json.loads((ROOT / "data" / (name + ".json")).read_text(encoding="utf-8"))


class ReplayHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlsplit(self.path)
        try:
            if url.path == "/streams":
                data = sorted(path.stem for path in (ROOT / "data").glob("*.json"))
            elif url.path.startswith("/streams/"):
                data = open_stream(unquote(url.path.removeprefix("/streams/")))
            elif url.path == "/timeline":
                limit = int(parse_qs(url.query).get("through", ["999999"])[0])
                data = [event for event in open_stream("timeline") if event["at"] <= limit]
            else:
                self.send_error(404)
                return
        except (ValueError, FileNotFoundError):
            self.send_error(404)
            return
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="读取本地合成业务输入")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    with ThreadingHTTPServer(("127.0.0.1", args.port), ReplayHandler) as server:
        print(f"监听 {server.server_address[0]}:{server.server_address[1]}", flush=True)
        server.serve_forever()
