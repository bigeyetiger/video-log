"""语音转写:本地 faster-whisper(不耗 token),结果按视频 md5 缓存。
与 daily-log 的区别:这里的时间是片内偏移(秒),不换算成绝对时刻。
"""
import json
import re
from pathlib import Path

_CJK = re.compile(r"[\u4e00-\u9fff]")


def _load_model(cfg: dict):
    from faster_whisper import WhisperModel

    asr_cfg = cfg["asr"]
    try:
        return WhisperModel(asr_cfg["model"], device=asr_cfg.get("device", "auto"))
    except Exception as e:
        raise RuntimeError(
            f"faster-whisper 模型加载失败: {e}\n"
            "首次运行需联网下载模型;若 huggingface 访问失败,"
            "可设置环境变量 HF_ENDPOINT=https://hf-mirror.com 后重试"
        ) from e


def _vad_regions(path: str) -> list[tuple[float, float]]:
    """用 silero VAD 找出真实语音区间(秒)。失败返回空列表(跳过校正)。"""
    try:
        from faster_whisper.audio import decode_audio
        from faster_whisper.vad import get_speech_timestamps

        audio = decode_audio(path, sampling_rate=16000)
        return [(t["start"] / 16000, t["end"] / 16000) for t in get_speech_timestamps(audio)]
    except Exception as e:
        print(f"[警告] VAD 语音区间检测失败({e}),跳过时间戳校正")
        return []


def _clamp_to_speech(segments: list[dict], path: str) -> list[dict]:
    """把每条转写段钳制到与其重叠最大的语音区间。

    faster-whisper 拼接 VAD 语音块送模型后,块边界处的时间抖动可能把一段话
    的起点映射到上一块尾部,时间戳横穿几十秒静音;钳制后恢复真实位置。
    完全落在静音里的段视为幻觉,直接丢弃(顺带滤掉空镜/风噪的假字幕)。
    """
    regions = _vad_regions(path)
    if not regions:
        return segments
    fixed = []
    for seg in segments:
        best_len, best = 0.0, None
        for a, b in regions:
            lo, hi = max(seg["start"], a), min(seg["end"], b)
            if hi - lo > best_len:
                best_len, best = hi - lo, (a, b)
        if best is None:
            continue
        a, b = best
        seg = {**seg, "start": round(max(seg["start"], a), 1),
               "end": round(min(seg["end"], b), 1)}
        if seg["end"] - seg["start"] >= 0.2:
            fixed.append(seg)
    return fixed


def transcribe(model, path: str, cfg: dict) -> list[dict]:
    """返回 [{start, end, text}],start/end 为片内秒数。无音轨/转写失败返回 []。"""
    asr_cfg = cfg["asr"]
    kwargs = {
        "language": asr_cfg.get("language") or None,
        "vad_filter": True,
        "initial_prompt": asr_cfg.get("initial_prompt") or None,
    }
    try:
        segments, _info = model.transcribe(path, **kwargs)
        segs = [{"start": round(s.start, 1), "end": round(s.end, 1), "text": s.text.strip()}
                for s in segments if s.text.strip()]
    except Exception as e:
        if "vad" in str(e).lower() or "onnxruntime" in str(e).lower():
            segments, _info = model.transcribe(path, **{**kwargs, "vad_filter": False})
            segs = [{"start": round(s.start, 1), "end": round(s.end, 1), "text": s.text.strip()}
                    for s in segments if s.text.strip()]
        else:  # 无音轨等情况:视为无台词
            print(f"[警告] 转写失败({e}),按无台词处理")
            segs = []
    return _clamp_to_speech(segs, path)


def run_asr(path: str, video_md5: str, cfg: dict, cache_dir: Path, skip: bool = False,
            model=None):
    """返回 (segments, model)。缓存按视频 md5;model 传 None 时自动加载一次并回传复用。"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"{video_md5}.json"
    if skip:
        return [], model
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8")), model
    if model is None:
        print(f"[asr] 加载模型 {cfg['asr']['model']} ...", flush=True)
        model = _load_model(cfg)
    segs = transcribe(model, path, cfg)
    cache_file.write_text(json.dumps(segs, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[asr] 转写完成:{len(segs)} 句", flush=True)
    return segs, model
