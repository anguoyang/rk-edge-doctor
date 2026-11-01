"""rkdoctor rtsp: acceptance-check an RTSP camera stream."""

import os
import platform
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

from .. import __version__
from ..probe import Probe
from ..rtsp_client import (H264Reassembler, RtspClient, RtspError, StreamStats,
                           parse_rtsp_url, parse_sdp, redact_url)

# verdict thresholds
LOSS_WARN_PCT = 1.0
LOSS_FAIL_PCT = 5.0
JITTER_WARN_MS = 30.0
FIRST_FRAME_WARN_S = 5.0
FPS_DEVIATION_WARN = 0.20


def _host_info() -> dict:
    probe = Probe("/")
    model = probe.read("/proc/device-tree/model") or "unknown"
    try:
        loadavg = [round(value, 2) for value in os.getloadavg()]
    except (OSError, AttributeError):
        loadavg = None
    return {"hostname": platform.node() or "unknown", "soc": model,
            "kernel": platform.release() or "unknown", "loadavg": loadavg}


def _check(check_id, title, status, value, detail, hint=None):
    return {"id": check_id, "title": title, "status": status,
            "value": value, "detail": detail, "hint": hint}


def diagnose_rtsp(url: str, duration: float = 15.0, transport: str = "udp",
                  expect: Optional[dict] = None, client=None) -> dict:
    expect = expect or {}
    report = {
        "schema": 1, "kind": "rtsp", "tool_version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "host": _host_info(),
        "stream": {"url": redact_url(url), "transport": transport,
                   "duration_s": duration, "negotiated": None},
        "stats": None, "checks": [], "verdict": "BROKEN", "notes": [],
    }
    checks = report["checks"]
    client = client if client is not None else RtspClient(url, transport=transport)
    try:
        connect_s = client.connect()
        checks.append(_check("reachability", "RTSP reachable", "pass",
                             "%.0f ms" % (connect_s * 1000),
                             "TCP connect + session start succeeded"))
    except (OSError, RtspError) as error:
        checks.append(_check("reachability", "RTSP reachable", "fail", None,
                             "connect failed: %s" % error,
                             "Check host, port, network and that the RTSP service is up"))
        report["notes"].append("stream could not be opened; remaining checks skipped")
        client.close()
        return report
    try:
        sdp_text = client.describe()
        sdp = parse_sdp(sdp_text)
        video = sdp["video"]
        if video is None or video.get("payload") is None:
            checks.append(_check("negotiation", "SDP video track", "fail", None,
                                 "DESCRIBE returned no video track",
                                 "The URL may point at an audio-only or wrong path"))
            return report
        report["stream"]["negotiated"] = {
            "codec": video["codec"], "clock_rate": video["clock_rate"],
            "width": video["width"], "height": video["height"], "fps_sdp": video["fps"],
        }
        checks.append(_check("negotiation", "SDP video track", "pass",
                             video["codec"] or "unknown codec",
                             "payload %s, clock %s" % (video["payload"], video["clock_rate"])))
        control = video["control"]
        base_url = client.info["base_url"] if hasattr(client, "info") else url
        if not control:
            track_url = base_url
        elif control.startswith("rtsp://"):
            track_url = control
        else:
            track_url = urljoin(base_url.rstrip("/") + "/", control)
        client.setup(track_url)
        play_at = client.play()
        stats = StreamStats(video["clock_rate"] or 90000)
        reassembler = H264Reassembler() if video["codec"] == "H264" else None
        client.collect(duration, stats, reassembler)
        try:
            client.teardown()
        except Exception:
            pass
        summary = stats.summary(duration)
        summary["first_frame_ms"] = (
            round((stats.first_frame_at - play_at) * 1000) if stats.first_frame_at else None)
        report["stats"] = summary
        _judge_stream(report, video, summary, stats, expect)
        _decode_spot_check(report, video, reassembler)
        fails = any(item["status"] == "fail" for item in checks)
        warns = any(item["status"] == "warn" for item in checks)
        report["verdict"] = "BROKEN" if fails else "DEGRADED" if warns else "HEALTHY"
        return report
    except (OSError, RtspError, ValueError) as error:
        checks.append(_check("session", "RTSP session", "fail", None,
                             "session failed: %s" % error,
                             "Try --transport tcp; check credentials and path"))
        return report
    finally:
        client.close()


def _judge_stream(report, video, summary, stats, expect):
    checks = report["checks"]
    first_frame_ms = summary["first_frame_ms"]
    if first_frame_ms is None:
        checks.append(_check("first_frame", "First frame", "fail", None,
                             "no complete frame arrived in the window",
                             "The camera may be streaming audio only, or the track is stalled"))
    else:
        status = "warn" if first_frame_ms > FIRST_FRAME_WARN_S * 1000 else "pass"
        checks.append(_check("first_frame", "First frame", status,
                             "%d ms" % first_frame_ms,
                             "time from PLAY to first complete frame"))
    loss = summary["loss_pct"]
    if loss is None:
        checks.append(_check("loss", "Packet loss", "skip", None, "no packets received"))
    else:
        status = "fail" if loss > LOSS_FAIL_PCT else "warn" if loss > LOSS_WARN_PCT else "pass"
        checks.append(_check("loss", "Packet loss", status, "%.3f %%" % loss,
                             "%s of %s packets lost" % (summary["lost_packets"],
                                                        summary["expected_packets"]),
                             "Loss above 1% usually shows as tearing or stalls" if status != "pass" else None))
    jitter = summary["jitter_ms"]
    checks.append(_check("jitter", "Jitter", "warn" if jitter > JITTER_WARN_MS else "pass",
                         "%.1f ms" % jitter, "RFC3550 inter-arrival jitter estimate"))
    fps = summary["fps_measured"]
    reference = expect.get("fps") or video["fps"]
    if fps is None:
        checks.append(_check("fps", "Frame rate", "skip", None, "no frames to measure"))
    elif reference:
        deviation = abs(fps - reference) / reference
        status = "warn" if deviation > FPS_DEVIATION_WARN else "pass"
        checks.append(_check("fps", "Frame rate", status, "%.2f fps" % fps,
                             "measured vs expected %.2f fps (%.0f%% off)"
                             % (reference, deviation * 100)))
    else:
        checks.append(_check("fps", "Frame rate", "pass", "%.2f fps" % fps,
                             "measured; no SDP or expected rate to compare"))
    ts_bad = summary["ts_regressions"] + summary["ts_gaps"]
    checks.append(_check("timestamps", "RTP timestamps",
                         "warn" if ts_bad else "pass",
                         "%d regressions, %d gaps" % (summary["ts_regressions"], summary["ts_gaps"]),
                         "non-monotonic or jumping RTP timestamps break recorders and analytics"))
    for key, label in (("width", "Width"), ("height", "Height")):
        if expect.get(key):
            actual = video["width"] if key == "width" else video["height"]
            status = "pass" if actual == expect[key] else ("warn" if actual is None else "fail")
            checks.append(_check("expect_" + key, "Expected " + label.lower(), status,
                                 ("%s px" % actual) if actual else None,
                                 "expected %s px" % expect[key]))


def _decode_spot_check(report, video, reassembler):
    checks = report["checks"]
    if video["codec"] != "H264" or reassembler is None or not reassembler.data:
        report["notes"].append("decode spot-check skipped: needs an H.264 stream with frames")
        return
    try:
        import cv2
        import numpy as np
    except ImportError:
        report["notes"].append("decode spot-check skipped: opencv (cv2) is not installed")
        return
    tmp = Path("/tmp/rkdoctor_rtsp_spot.h264")
    try:
        tmp.write_bytes(bytes(reassembler.data))
        capture = cv2.VideoCapture(str(tmp))
        frames = []
        for _ in range(90):
            ok, frame = capture.read()
            if not ok:
                break
            frames.append(frame)
        capture.release()
        if not frames:
            checks.append(_check("decode", "Decode spot-check", "fail", None,
                                 "frames arrived but none could be decoded",
                                 "The bitstream may be corrupt or an unsupported profile"))
            return
        first = frames[0].astype("float32")
        black = float(first.mean()) < 5.0
        frozen = False
        if len(frames) > 10:
            later = frames[-1].astype("float32")
            frozen = first.shape == later.shape and float(np.abs(first - later).mean()) < 0.5
        if black:
            checks.append(_check("decode", "Decode spot-check", "warn",
                                 "mean %.1f" % float(first.mean()),
                                 "decoded frames are (near) black",
                                 "Check lens cover, IR/night mode and exposure"))
        elif frozen:
            checks.append(_check("decode", "Decode spot-check", "warn",
                                 "%d frames decoded" % len(frames),
                                 "first and last decoded frames are identical (frozen image?)",
                                 "The camera may be sending a stalled picture"))
        else:
            checks.append(_check("decode", "Decode spot-check", "pass",
                                 "%d frames decoded" % len(frames),
                                 "frames decode and the picture changes over time"))
    except Exception as error:  # spot-check is best-effort
        report["notes"].append("decode spot-check skipped: %s" % error)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
