"""切段:固定间隔为主骨架,ffmpeg 场景检测出的转场点优先作为切分边界;
每段按中点抽代表帧;dHash 合并画面几乎不变的静止段(纯色帧不参与合并)。
"""
import hashlib
import json
import subprocess
from pathlib import Path

from PIL import Image

from .media import ffmpeg_exe


def detect_transitions(video_path: str, video_md5: str, threshold: float,
                       cache_root: Path) -> list[float]:
    """场景切换检测,返回转场时间(秒,升序)。结果按 (视频md5, 阈值) 缓存。"""
    cache_dir = cache_root / "transitions"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{video_md5}_{threshold}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))
    p = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", video_path,
         "-vf", f"select='gt(scene,{threshold})',metadata=print:file=-", "-an", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    times = []
    for line in p.stdout.splitlines():
        if "pts_time:" in line:
            try:
                times.append(float(line.split("pts_time:")[1].split()[0]))
            except (ValueError, IndexError):
                continue
    cache_file.write_text(json.dumps([round(t, 2) for t in times]), encoding="utf-8")
    return [round(t, 2) for t in times]


def snap_boundaries(raw: list[float], transitions: list[float], tolerance: float) -> list[float]:
    """把每个内部边界吸附到最近的转场点(在 tolerance 秒内),保持单调递增。"""
    if not transitions:
        return raw
    bounds = [raw[0]]
    for b in raw[1:-1]:
        near = min(transitions, key=lambda t: abs(t - b))
        b2 = near if abs(near - b) <= tolerance else b
        if b2 > bounds[-1] + 1.0:
            bounds.append(b2)
        else:
            bounds.append(b)
    bounds.append(raw[-1])
    return bounds


def _extract_frame_at(video_path: str, t: float, out_path: Path, max_px: int) -> None:
    if out_path.exists():
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [ffmpeg_exe(), "-y", "-ss", f"{t:.2f}", "-i", video_path, "-frames:v", "1",
         "-vf", f"scale={max_px}:{max_px}:force_original_aspect_ratio=decrease",
         "-q:v", "3", str(out_path)],
        check=True, capture_output=True,
    )


def file_md5(path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _dhash(path: str, hash_size: int = 8) -> int | None:
    try:
        with Image.open(path) as im:
            im = im.convert("L").resize((hash_size + 1, hash_size), Image.LANCZOS)
            px = list(im.getdata())
    except Exception:
        return None
    bits = 0
    for row in range(hash_size):
        for col in range(hash_size):
            bits = (bits << 1) | (1 if px[row * (hash_size + 1) + col] > px[row * (hash_size + 1) + col + 1] else 0)
    return bits


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _load_hash(frame_path: str, md5: str, cache_dir: Path) -> int | None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{md5}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))["hash"]
    h = _dhash(frame_path)
    cache_file.write_text(json.dumps({"md5": md5, "hash": h}), encoding="utf-8")
    return h


def make_segments(video_path: str, video_md5: str, duration: float, interval: float,
                  frame_max_px: int, cache_root: Path, merge_dist: int,
                  transitions: list[float] | None = None,
                  snap_ratio: float = 0.35) -> list[dict]:
    """返回 [{index, start, end, duration, frame_path, frame_md5, frame_count,
    transition}]。内部边界在 snap_ratio*interval 秒内吸附到最近转场点。"""
    transitions = transitions or []
    frame_dir = cache_root / "frames" / video_md5

    raw = [0.0]
    t = interval
    while t < duration - 0.5:
        raw.append(round(t, 2))
        t += interval
    raw.append(duration)
    bounds = snap_boundaries(raw, transitions, snap_ratio * interval)

    segments = []
    for i in range(len(bounds) - 1):
        start, end = bounds[i], bounds[i + 1]
        mid = round((start + end) / 2, 2)
        frame = frame_dir / f"mid_{mid:08.2f}.jpg"
        _extract_frame_at(video_path, mid, frame, frame_max_px)
        near = min(transitions, key=lambda x: abs(x - start)) if transitions else None
        transition = bool(near is not None and start > 0.5 and abs(near - start) <= 1.0)
        segments.append({
            "index": i,
            "start": start,
            "end": end,
            "duration": round(end - start, 1),
            "frame_path": str(frame.resolve()),
            "frame_md5": file_md5(frame),
            "frame_count": 1,
            "transition": transition,
        })

    # 相邻段画面相似则合并(静止机位/空镜),代表帧取第一段。
    # 哈希为 0 的纯色帧(过曝天空/纯色测试图)不参与合并,不同纯色场景会被误判为相同。
    groups: list[list[dict]] = []
    prev_hash: int | None = None
    for seg in segments:
        h = _load_hash(seg["frame_path"], seg["frame_md5"], cache_root / "hashes")
        mergeable = h is not None and h != 0
        if (groups and mergeable and prev_hash is not None
                and _hamming(h, prev_hash) <= merge_dist):
            groups[-1].append(seg)
        else:
            groups.append([seg])
        prev_hash = h if mergeable else None
    merged = []
    for g in groups:
        merged.append({
            "index": g[0]["index"],
            "start": g[0]["start"],
            "end": g[-1]["end"],
            "duration": round(g[-1]["end"] - g[0]["start"], 1),
            "frame_path": g[0]["frame_path"],
            "frame_md5": g[0]["frame_md5"],
            "frame_count": len(g),
            "transition": any(x["transition"] for x in g),
        })
    return merged


def tc(seconds: float) -> str:
    """秒 -> MM:SS(超过 1 小时用 HH:MM:SS)。"""
    s = int(round(seconds))
    if s >= 3600:
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
    return f"{s // 60:02d}:{s % 60:02d}"
