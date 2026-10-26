"""媒体工具:ffmpeg 定位、音视频探测(时长/创建时间)、视频抽帧。"""
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

_FFMPEG: str | None = None


def ffmpeg_exe() -> str:
    """优先用 imageio-ffmpeg 自带二进制(免系统安装),否则回退 PATH。"""
    global _FFMPEG
    if _FFMPEG:
        return _FFMPEG
    try:
        import imageio_ffmpeg

        _FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        exe = shutil.which("ffmpeg")
        if not exe:
            raise RuntimeError("未找到 ffmpeg。可 pip install imageio-ffmpeg,或将 ffmpeg 加入 PATH")
        _FFMPEG = exe
    return _FFMPEG


def probe(path: Path) -> dict:
    """读取时长与容器 creation_time。手机视频的 creation_time 是 UTC,这里转本地时间。"""
    p = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", str(path), "-f", "ffmetadata", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    duration_sec = None
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)", p.stderr)
    if m:
        duration_sec = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    creation_time = None
    # ffmetadata 输出不含 creation_time,需从 stderr 的 "creation_time   : ..." 行解析(首个为文件级)
    m_ct = re.search(r"creation_time\s*:\s*(\S+)", p.stderr)
    if m_ct:
        try:
            creation_time = datetime.fromisoformat(m_ct.group(1).replace("Z", "+00:00"))
        except ValueError:
            pass
    if creation_time is not None:
        if creation_time.tzinfo is not None:
            creation_time = creation_time.astimezone()
        creation_time = creation_time.replace(tzinfo=None, microsecond=0)
    m_res = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", p.stderr)
    width = int(m_res.group(1)) if m_res else 0
    height = int(m_res.group(2)) if m_res else 0
    return {
        "duration_sec": duration_sec,
        "creation_time": creation_time,
        "width": width,
        "height": height,
        "has_audio": "Audio:" in p.stderr,
    }


def extract_frames(video_path: str, out_dir: Path, interval_sec: float, max_px: int) -> list[Path]:
    """按固定间隔抽帧,结果缓存在 out_dir(按视频 md5 命名),已有帧则跳过抽帧。

    用按帧号 select 而不是 fps 滤镜:实测 fps 滤镜对部分 HEVC 手机视频
    (带旋转元数据)会输出 0 帧;format=yuvj420p 是 mjpeg 编码所需的 full-range。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(out_dir.glob("f_*.jpg"))
    if existing:
        return existing
    scale = f"scale={max_px}:{max_px}:force_original_aspect_ratio=decrease,format=yuvj420p"
    fps = probe(Path(video_path)).get("fps")
    if fps and fps > 0:
        k = max(1, round(fps * interval_sec))
        vf = f"select='not(mod(n,{k}))',{scale}"
    else:
        vf = f"fps=1/{interval_sec:g},{scale}"
    p = subprocess.run(
        [ffmpeg_exe(), "-y", "-i", video_path, "-vf", vf, "-q:v", "3", str(out_dir / "f_%04d.jpg")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if p.returncode != 0 or not any(out_dir.glob("f_*.jpg")):
        raise RuntimeError(f"视频抽帧失败 {video_path}: {p.stderr[-400:]}")
    return sorted(out_dir.glob("f_*.jpg"))
