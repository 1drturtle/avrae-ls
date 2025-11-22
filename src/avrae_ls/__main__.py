from __future__ import annotations

import argparse

from .server import create_server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Avrae draconic alias language server")
    parser.add_argument("--tcp", action="store_true", help="Run in TCP mode instead of stdio")
    parser.add_argument("--host", default="127.0.0.1", help="TCP host (when --tcp is set)")
    parser.add_argument("--port", type=int, default=2087, help="TCP port (when --tcp is set)")
    parser.add_argument("--stdio", action="store_true", help="Accept stdio flag for VS Code clients (ignored)")
    args = parser.parse_args(argv)

    server = create_server()
    if args.tcp:
        server.start_tcp(args.host, args.port)
    else:
        server.start_io()


if __name__ == "__main__":
    main()
