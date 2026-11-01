import time
import unittest

from rkdoctor.checks.rtsp import diagnose_rtsp
from rkdoctor.rtsp_client import (H264Reassembler, StreamStats, parse_rtp,
                                  parse_sdp, redact_url)

SDP = """v=0
o=- 0 0 IN IP4 192.0.2.10
s=TestStream
t=0 0
m=video 0 RTP/AVP 96
a=rtpmap:96 H264/90000
a=fmtp:96 packetization-mode=1; sprop-parameter-sets=Z0IAH5WoFAFuQA==,aM48gA==
a=framesize:96 1920-1080
a=framerate:25
a=control:trackID=1
"""


def rtp_packet(seq, ts, marker, payload=b"\x65\x01"):
    header = bytes([0x80, (0x80 if marker else 0x00) | 96])
    return header + seq.to_bytes(2, "big") + ts.to_bytes(4, "big") + (1234).to_bytes(4, "big") + payload


class RedactTests(unittest.TestCase):
    def test_password_masked(self):
        out = redact_url("rtsp://admin:s3cret@192.0.2.10:554/stream1")
        self.assertIn("admin:***@", out)
        self.assertNotIn("s3cret", out)

    def test_no_password_unchanged_shape(self):
        out = redact_url("rtsp://192.0.2.10/live")
        self.assertEqual(out, "rtsp://192.0.2.10:554/live")

    def test_garbage(self):
        self.assertEqual(redact_url("http://x"), "rtsp://(unparseable)")


class SdpTests(unittest.TestCase):
    def test_video_track_parsed(self):
        info = parse_sdp(SDP)
        video = info["video"]
        self.assertEqual(video["codec"], "H264")
        self.assertEqual(video["clock_rate"], 90000)
        self.assertEqual((video["width"], video["height"]), (1920, 1080))
        self.assertEqual(video["fps"], 25.0)
        self.assertEqual(video["control"], "trackID=1")

    def test_no_video(self):
        info = parse_sdp("v=0\nm=audio 0 RTP/AVP 8\na=rtpmap:8 PCMA/8000\n")
        self.assertIsNone(info["video"])


class RtpStatsTests(unittest.TestCase):
    def test_parse_rtp_roundtrip(self):
        parsed = parse_rtp(rtp_packet(7, 3600, 1))
        seq, ts, marker, pt, payload = parsed
        self.assertEqual((seq, ts, marker, pt), (7, 3600, 1, 96))
        self.assertEqual(payload, b"\x65\x01")

    def test_loss_and_frames(self):
        stats = StreamStats(90000)
        t0 = time.monotonic()
        for i, seq in enumerate([100, 101, 102, 103, 104, 106, 107, 108, 109]):
            stats.feed(seq, i * 3600, 1 if i % 3 == 2 else 0, 1000, t0 + i * 0.04)
        summary = stats.summary(0.36)
        self.assertEqual(summary["expected_packets"], 10)
        self.assertEqual(summary["lost_packets"], 1)
        self.assertEqual(summary["loss_pct"], 10.0)
        self.assertEqual(summary["frames"], 3)

    def test_sequence_wraparound(self):
        stats = StreamStats(90000)
        t0 = time.monotonic()
        for i, seq in enumerate([65534, 65535, 0, 1]):
            stats.feed(seq, i * 3600, 0, 500, t0 + i * 0.04)
        summary = stats.summary(0.16)
        self.assertEqual(summary["expected_packets"], 4)
        self.assertEqual(summary["lost_packets"], 0)


class ReassemblerTests(unittest.TestCase):
    def test_single_nal(self):
        reasm = H264Reassembler()
        reasm.feed(b"\x67\x42\x00")
        self.assertEqual(bytes(reasm.data), b"\x00\x00\x00\x01\x67\x42\x00")

    def test_fu_a_two_parts(self):
        reasm = H264Reassembler()
        reasm.feed(bytes([0x7C, 0x85]) + b"AB")
        reasm.feed(bytes([0x7C, 0x45]) + b"CD")
        self.assertEqual(bytes(reasm.data), b"\x00\x00\x00\x01\x65ABCD")


class FakeClient:
    def __init__(self, sdp=SDP, frames=50):
        self._sdp = sdp
        self._frames = frames

    def connect(self):
        return 0.012

    def describe(self):
        return self._sdp

    def setup(self, url):
        self.setup_url = url

    def play(self):
        return time.monotonic()

    def collect(self, duration, stats, reassembler=None):
        seq = 1000
        ts = 0
        base = time.monotonic()
        for frame in range(self._frames):
            for part in range(3):
                stats.feed(seq, ts, 1 if part == 2 else 0, 800, base + frame * 0.04)
                seq += 1
            ts += 3600

    def teardown(self):
        pass

    def close(self):
        pass


class DeadClient(FakeClient):
    def connect(self):
        raise OSError("no route to host")


class DiagnoseTests(unittest.TestCase):
    def test_healthy_stream(self):
        report = diagnose_rtsp("rtsp://user:pw@192.0.2.10/stream", duration=2.0,
                               client=FakeClient())
        self.assertEqual(report["verdict"], "HEALTHY")
        self.assertEqual(report["stream"]["negotiated"]["codec"], "H264")
        self.assertNotIn("pw", str(report))
        self.assertEqual(report["stats"]["loss_pct"], 0.0)

    def test_unreachable_is_broken(self):
        report = diagnose_rtsp("rtsp://192.0.2.10/stream", duration=1.0, client=DeadClient())
        self.assertEqual(report["verdict"], "BROKEN")
        self.assertEqual(report["checks"][0]["status"], "fail")

    def test_no_video_track_is_broken(self):
        client = FakeClient(sdp="v=0\nm=audio 0 RTP/AVP 8\n")
        report = diagnose_rtsp("rtsp://192.0.2.10/stream", duration=1.0, client=client)
        self.assertEqual(report["verdict"], "BROKEN")

    def test_expect_mismatch_fails(self):
        report = diagnose_rtsp("rtsp://192.0.2.10/stream", duration=2.0,
                               expect={"width": 1280}, client=FakeClient())
        width_checks = [c for c in report["checks"] if c["id"] == "expect_width"]
        self.assertEqual(width_checks[0]["status"], "fail")
        self.assertEqual(report["verdict"], "BROKEN")


if __name__ == "__main__":
    unittest.main()
