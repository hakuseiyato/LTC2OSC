import socket

from pythonosc import udp_client

from tc_generator import Timecode


def _resolve_ipv4(host: str) -> str:
    """ホスト名を IPv4 のみで解決する。
    DDNS が AAAA レコードを持っていても A レコード優先。
    """
    info = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_DGRAM)
    if not info:
        raise OSError(f"IPv4 resolve failed: {host}")
    return info[0][4][0]


class OscSender:
    """複数の送信先へ OSC/UDP で TC を送信する。冗長送信でパケロス対策。"""

    def __init__(self, address: str = "/tc"):
        self._address = address
        self._clients: list[udp_client.SimpleUDPClient] = []
        self._targets: list[dict] = []
        self._redundancy: int = 1  # 1 フレームあたりの送信回数

    def set_address(self, address: str) -> None:
        self._address = address

    def set_targets(self, targets: list[dict]) -> None:
        """targets: [{"ip": str, "port": int}, ...]"""
        self._targets = targets
        clients = []
        for t in targets:
            host = t["ip"]
            port = int(t["port"])
            try:
                ipv4 = _resolve_ipv4(host)
                clients.append(udp_client.SimpleUDPClient(ipv4, port))
            except Exception:
                # 解決失敗時はそのままホスト名で試行（IPv4 リテラルなら成功する）
                try:
                    clients.append(udp_client.SimpleUDPClient(host, port))
                except Exception:
                    pass
        self._clients = clients

    def set_redundancy(self, n: int) -> None:
        self._redundancy = max(1, min(5, int(n)))

    def has_targets(self) -> bool:
        return bool(self._clients)

    def send(self, tc: Timecode) -> None:
        tc_str = str(tc)
        for _ in range(self._redundancy):
            for client in self._clients:
                try:
                    client.send_message(self._address, tc_str)
                except Exception:
                    pass
