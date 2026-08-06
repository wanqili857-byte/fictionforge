"""等待本地端口就绪（start.sh / start.bat 复用）。

用法: python scripts/_wait_proxy.py <port> [timeout_sec]
就绪退出 0；超时退出 1。
"""
import socket
import sys
import time

port = int(sys.argv[1])
timeout = int(sys.argv[2]) if len(sys.argv) > 2 else 30

for _ in range(timeout):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            print(f"port {port} ready")
            sys.exit(0)
    except OSError:
        time.sleep(1)
print(f"port {port} not ready after {timeout}s")
sys.exit(1)
