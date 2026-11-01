"""Minimal pure-stdlib RTSP client and RTP stream statistics.

Just enough of RFC 2326/3550 to run an acceptance check against a camera:
OPTIONS/DESCRIBE/SETUP/PLAY, UDP or TCP-interleaved delivery, Basic and
Digest auth, sequence loss / jitter / timestamp health, and H.264 NAL
reassembly for the optional decode spot-check.
"""

import base64
import hashlib
import socket
import time
from urllib.parse import unquote, urlparse


class RtspError(Exception):
    pass


def parse_rtsp_url(url: str) -> dict:
    parts = urlparse(url)
    if parts.scheme != "rtsp" or not parts.hostname:
        raise ValueError("not an rtsp:// URL: " + url)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return {"scheme": "rtsp", "username": unquote(parts.username) if parts.username else None,
            "password": unquote(parts.password) if parts.password else None,
            "host": parts.hostname, "port": parts.port or 554, "path": path,
            "base_url": "rtsp://%s:%d%s" % (parts.hostname, parts.port or 554, path)}


def redact_url(url: str) -> str:
    """Mask the password; never let credentials reach a report or log."""
    try:
        info = parse_rtsp_url(url)
    except ValueError:
        return "rtsp://(unparseable)"
    auth = ""
    if info["username"]:
        auth = info["username"] + (":***" if info["password"] else "") + "@"
    return "rtsp://%s%s:%d%s" % (auth, info["host"], info["port"], info["path"])


def parse_sdp(text: str) -> dict:
    """Extract the first video track's codec/clock/size/fps/control URL."""
    result = {"video": None, "session_name": None}
    section = None
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if line.startswith("s="):
            result["session_name"] = line[2:]
        elif line.startswith("m="):
            fields = line[2:].split()
            section = {"media": fields[0], "payload": int(fields[3]) if len(fields) > 3 else None,
                       "codec": None, "clock_rate": None, "control": None,
                       "width": None, "height": None, "fps": None, "fmtp": {}}
            if fields[0] == "video" and result["video"] is None:
                result["video"] = section
            else:
                section = {"media": fields[0]}  # keep consuming attrs harmlessly
        elif line.startswith("a=") and section is not None and section.get("media") == "video":
            attr = line[2:]
            video = result["video"]
            if attr.startswith("rtpmap:") and video is section:
                _, _, rest = attr.partition(":")
                tokens = rest.strip().split()
                encoding = None
                if len(tokens) >= 2 and tokens[0].isdigit():
                    if int(tokens[0]) == video["payload"]:
                        encoding = tokens[1]
                elif len(tokens) == 1:
                    encoding = tokens[0]
                if encoding:
                    codec_parts = encoding.split("/")
                    video["codec"] = codec_parts[0].upper()
                    if len(codec_parts) > 1 and codec_parts[1].isdigit():
                        video["clock_rate"] = int(codec_parts[1])
            elif attr.startswith("control:"):
                video["control"] = attr[len("control:"):]
            elif attr.startswith("framesize:"):
                bits = attr.split()
                if len(bits) == 2 and "-" in bits[1]:
                    w, _, h = bits[1].partition("-")
                    if w.isdigit() and h.isdigit():
                        video["width"], video["height"] = int(w), int(h)
            elif attr.startswith("framerate:"):
                try:
                    video["fps"] = float(attr[len("framerate:"):])
                except ValueError:
                    pass
            elif attr.startswith("fmtp:"):
                _, _, rest = attr.partition(":")
                params = {}
                for pair in rest.strip().split(";", 1)[-1].split(";"):
                    if "=" in pair:
                        key, _, value = pair.partition("=")
                        params[key.strip()] = value.strip()
                video["fmtp"] = params
                if video["width"] is None and "sprop-parameter-sets" not in params:
                    pass
    return result


def parse_rtp(packet: bytes):
    """Return (seq, timestamp, marker, payload_type, payload) or None."""
    if len(packet) < 12 or (packet[0] >> 6) != 2:
        return None
    cc = packet[0] & 0x0F
    has_ext = (packet[0] >> 4) & 1
    marker = (packet[1] >> 7) & 1
    payload_type = packet[1] & 0x7F
    seq = int.from_bytes(packet[2:4], "big")
    timestamp = int.from_bytes(packet[4:8], "big")
    offset = 12 + 4 * cc
    if has_ext:
        if len(packet) < offset + 4:
            return None
        ext_words = int.from_bytes(packet[offset + 2:offset + 4], "big")
        offset += 4 + 4 * ext_words
    if len(packet) < offset:
        return None
    return seq, timestamp, marker, payload_type, packet[offset:]


class StreamStats:
    """Running RTP statistics over the measurement window."""

    def __init__(self, clock_rate: int = 90000):
        self.clock_rate = clock_rate or 90000
        self.packets = 0
        self.bytes = 0
        self.frames = 0
        self.first_packet_at = None
        self.first_frame_at = None
        self.last_frame_ts = None
        self.frame_timestamps = []
        self.ts_regressions = 0
        self._base_seq = None
        self._max_seq_ext = None
        self._prev_seq = None
        self._prev_transit = None
        self.jitter_s = 0.0

    def feed(self, seq: int, timestamp: int, marker: int, size: int, arrival: float) -> None:
        if self.first_packet_at is None:
            self.first_packet_at = arrival
        self.packets += 1
        self.bytes += size
        if self._base_seq is None:
            self._base_seq = seq
            self._max_seq_ext = seq
            self._prev_seq = seq
        else:
            ext = self._extend(seq)
            if ext > self._max_seq_ext:
                self._max_seq_ext = ext
            self._prev_seq = seq
        transit = arrival - timestamp / self.clock_rate
        if self._prev_transit is not None:
            diff = abs(transit - self._prev_transit)
            self.jitter_s += (diff - self.jitter_s) / 16.0
        self._prev_transit = transit
        if marker:
            self.frames += 1
            if self.first_frame_at is None:
                self.first_frame_at = arrival
            if self.last_frame_ts is not None:
                if timestamp <= self.last_frame_ts:
                    self.ts_regressions += 1
                self.frame_timestamps.append(timestamp)
            self.last_frame_ts = timestamp

    def _extend(self, seq: int) -> int:
        base = self._max_seq_ext
        candidate = (base - (base % 65536)) + seq
        if candidate > base + 32768:
            candidate -= 65536
        elif candidate < base - 32768:
            candidate += 65536
        return candidate

    def summary(self, window_s: float) -> dict:
        expected = None
        lost = None
        loss_pct = None
        if self._base_seq is not None and self._max_seq_ext is not None:
            expected = self._max_seq_ext - self._base_seq + 1
            lost = max(0, expected - self.packets)
            loss_pct = round(100.0 * lost / expected, 3) if expected else None
        gaps = 0
        deltas = []
        stamps = self.frame_timestamps
        for prev, cur in zip(stamps, stamps[1:]):
            deltas.append(cur - prev)
        if deltas:
            ordered = sorted(deltas)
            median = ordered[len(ordered) // 2]
            if median > 0:
                gaps = sum(1 for d in deltas if d > 3 * median)
        return {"packets": self.packets, "bytes": self.bytes, "frames": self.frames,
                "expected_packets": expected, "lost_packets": lost, "loss_pct": loss_pct,
                "jitter_ms": round(self.jitter_s * 1000.0, 3),
                "fps_measured": round(self.frames / window_s, 2) if window_s > 0 else None,
                "bitrate_mbps": round(self.bytes * 8 / window_s / 1e6, 3) if window_s > 0 else None,
                "ts_regressions": self.ts_regressions, "ts_gaps": gaps}


class H264Reassembler:
    """Rebuild Annex-B NAL units from RTP payloads (single NAL + FU-A)."""

    def __init__(self):
        self.data = bytearray()
        self._fu_open = False

    def feed(self, payload: bytes) -> None:
        if not payload:
            return
        nal_type = payload[0] & 0x1F
        if 1 <= nal_type <= 23:
            self.data += b"\x00\x00\x00\x01" + payload
            self._fu_open = False
        elif nal_type == 24 and len(payload) > 1:  # STAP-A
            rest = payload[1:]
            while len(rest) >= 2:
                size = int.from_bytes(rest[:2], "big")
                if size <= 0 or len(rest) < 2 + size:
                    break
                self.data += b"\x00\x00\x00\x01" + rest[2:2 + size]
                rest = rest[2 + size:]
        elif nal_type == 28 and len(payload) >= 2:  # FU-A
            fu_header = payload[1]
            start = fu_header >> 7
            end = (fu_header >> 6) & 1
            if start:
                nal_header = (payload[0] & 0xE0) | (fu_header & 0x1F)
                self.data += b"\x00\x00\x00\x01" + bytes([nal_header]) + payload[2:]
                self._fu_open = True
            elif self._fu_open:
                self.data += payload[2:]
            if end:
                self._fu_open = False


class RtspClient:
    """Blocking RTSP client; enough for a scripted acceptance session."""

    def __init__(self, url: str, transport: str = "udp", timeout: float = 8.0):
        self.info = parse_rtsp_url(url)
        self.transport = transport
        self.timeout = timeout
        self.sock = None
        self.udp_sock = None
        self.session = None
        self.cseq = 0
        self.buffer = b""
        self.auth_header = None
        self.interleaved = (0, 1)
        self.server_port = None

    # -- transport helpers ------------------------------------------------
    def connect(self) -> float:
        started = time.monotonic()
        self.sock = socket.create_connection((self.info["host"], self.info["port"]),
                                             timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        return time.monotonic() - started

    def close(self) -> None:
        for sock in (self.sock, self.udp_sock):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        self.sock = None
        self.udp_sock = None

    def _send(self, text: str) -> None:
        self.sock.sendall(text.encode("utf-8"))

    def _read_response(self):
        while b"\r\n\r\n" not in self.buffer:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RtspError("connection closed by server")
            self.buffer += chunk
        head, _, rest = self.buffer.partition(b"\r\n\r\n")
        headers = {}
        lines = head.decode("utf-8", "replace").split("\r\n")
        status = int(lines[0].split()[1]) if len(lines[0].split()) > 1 else 0
        for line in lines[1:]:
            if ": " in line:
                key, _, value = line.partition(": ")
                headers[key.strip().lower()] = value.strip()
        length = int(headers.get("content-length", "0") or 0)
        while len(rest) < length:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RtspError("connection closed by server")
            rest += chunk
        self.buffer = rest[length:]
        return status, headers, rest[:length].decode("utf-8", "replace")

    def _request(self, method: str, url: str, extra: dict = None, retry_auth: bool = True):
        self.cseq += 1
        headers = {"CSeq": str(self.cseq), "User-Agent": "rkdoctor"}
        if self.session:
            headers["Session"] = self.session
        if self.auth_header:
            headers["Authorization"] = self.auth_header
        if extra:
            headers.update(extra)
        text = method + " " + url + " RTSP/1.0\r\n" + "".join(
            "%s: %s\r\n" % item for item in headers.items()) + "\r\n"
        self._send(text)
        status, resp_headers, body = self._read_response()
        if status == 401 and retry_auth and self.info["username"]:
            challenge = resp_headers.get("www-authenticate", "")
            self.auth_header = self._build_auth(challenge, method, url)
            if self.auth_header:
                return self._request(method, url, extra, retry_auth=False)
        return status, resp_headers, body

    def _build_auth(self, challenge: str, method: str, url: str):
        user = self.info["username"]
        password = self.info["password"] or ""
        if challenge.lower().startswith("basic"):
            token = base64.b64encode(("%s:%s" % (user, password)).encode()).decode()
            return "Basic " + token
        if challenge.lower().startswith("digest"):
            fields = {}
            for part in challenge[6:].split(","):
                if "=" in part:
                    key, _, value = part.partition("=")
                    fields[key.strip()] = value.strip().strip('"')
            realm = fields.get("realm", "")
            nonce = fields.get("nonce", "")
            ha1 = hashlib.md5(("%s:%s:%s" % (user, realm, password)).encode()).hexdigest()
            ha2 = hashlib.md5(("%s:%s" % (method, url)).encode()).hexdigest()
            if fields.get("qop"):
                cnonce = hashlib.md5(str(time.time()).encode()).hexdigest()[:16]
                nc = "00000001"
                response = hashlib.md5(("%s:%s:%s:%s:auth:%s"
                                        % (ha1, nonce, nc, cnonce, ha2)).encode()).hexdigest()
                return ('Digest username="%s", realm="%s", nonce="%s", uri="%s", '
                        'response="%s", qop=auth, nc=%s, cnonce="%s"'
                        % (user, realm, nonce, url, response, nc, cnonce))
            response = hashlib.md5(("%s:%s:%s" % (ha1, nonce, ha2)).encode()).hexdigest()
            return ('Digest username="%s", realm="%s", nonce="%s", uri="%s", response="%s"'
                    % (user, realm, nonce, url, response))
        return None

    # -- session ----------------------------------------------------------
    def describe(self) -> str:
        status, _, body = self._request("DESCRIBE", self.info["base_url"],
                                        {"Accept": "application/sdp"})
        if status != 200:
            raise RtspError("DESCRIBE failed with status %d" % status)
        return body

    def setup(self, track_url: str) -> None:
        if self.transport == "tcp":
            transport = "RTP/AVP/TCP;unicast;interleaved=0-1"
            self.interleaved = (0, 1)
        else:
            self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp_sock.bind(("", 0))
            client_port = self.udp_sock.getsockname()[1]
            if client_port % 2:
                self.udp_sock.close()
                self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.udp_sock.bind(("", client_port + 1))
                client_port += 1
            transport = "RTP/AVP;unicast;client_port=%d-%d" % (client_port, client_port + 1)
        status, headers, _ = self._request("SETUP", track_url, {"Transport": transport})
        if status != 200:
            raise RtspError("SETUP failed with status %d" % status)
        session = headers.get("session")
        if session:
            self.session = session.split(";")[0].strip()

    def play(self) -> float:
        status, _, _ = self._request("PLAY", self.info["base_url"], {"Range": "npt=0.000-"})
        if status != 200:
            raise RtspError("PLAY failed with status %d" % status)
        return time.monotonic()

    def teardown(self) -> None:
        try:
            if self.sock is not None and self.session:
                self._request("TEARDOWN", self.info["base_url"])
        except (OSError, RtspError):
            pass

    # -- media collection ---------------------------------------------------
    def collect(self, duration: float, stats: StreamStats,
                reassembler: H264Reassembler = None) -> None:
        deadline = time.monotonic() + duration
        if self.transport == "udp":
            self.udp_sock.settimeout(1.0)
            while time.monotonic() < deadline:
                try:
                    packet, _ = self.udp_sock.recvfrom(65536)
                except socket.timeout:
                    continue
                self._feed_packet(packet, stats, reassembler)
        else:
            self.sock.settimeout(1.0)
            while time.monotonic() < deadline:
                if self.buffer and self.buffer[:1] == b"$":
                    if not self._consume_interleaved(stats, reassembler):
                        if not self._fill():
                            break
                    continue
                if not self._fill():
                    if time.monotonic() >= deadline:
                        break
                    continue

    def _fill(self) -> bool:
        try:
            chunk = self.sock.recv(65536)
        except socket.timeout:
            return True
        if not chunk:
            return False
        self.buffer += chunk
        return True

    def _consume_interleaved(self, stats, reassembler) -> bool:
        if len(self.buffer) < 4 or self.buffer[:1] != b"$":
            return False
        channel = self.buffer[1]
        length = int.from_bytes(self.buffer[2:4], "big")
        if len(self.buffer) < 4 + length:
            return False
        payload = self.buffer[4:4 + length]
        self.buffer = self.buffer[4 + length:]
        if channel == self.interleaved[0]:
            self._feed_packet(payload, stats, reassembler)
        return True

    def _feed_packet(self, packet: bytes, stats: StreamStats, reassembler) -> None:
        parsed = parse_rtp(packet)
        if parsed is None:
            return
        seq, timestamp, marker, _, payload = parsed
        stats.feed(seq, timestamp, marker, len(packet), time.monotonic())
        if reassembler is not None:
            reassembler.feed(payload)
