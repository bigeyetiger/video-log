"""段代表帧描述:调多模态模型给每个切段打画面描述,按帧 md5 缓存。"""
import base64
import io
import json
import re
import time
from datetime import datetime
from pathlib import Path

from PIL import Image

from .llm import chat
from .shots import tc

PROMPT = (
    "你是视频场记助手。这张图是原始拍摄素材中某一时间段的代表帧,"
    '只输出一个 JSON 对象:{"shot": "画面内容(15字内)", '
    '"subject": "主体或人物(10字内)", "detail": "细节补充,如人物动作/物品/环境(30字内)"}。'
    '看不清或不确定就写"不确定"。不要输出 JSON 以外的任何文字。'
)


def to_b64_jpeg(path: str, max_px: int, quality: int) -> str:
    with Image.open(path) as im:
        im = im.convert("RGB")
        w, h = im.size
        if max(w, h) > max_px:
            scale = max_px / max(w, h)
            im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality)
    return base64.b64encode(buf.getvalue()).decode()


def parse_caption(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            data = json.loads(m.group())
            return {k: str(data.get(k, "不确定")) for k in ("shot", "subject", "detail")}
        except json.JSONDecodeError:
            pass
    return {"shot": text.strip()[:60] or "不确定", "subject": "不确定", "detail": "不确定"}


def describe_segments(segments: list[dict], cfg: dict, cache_dir: Path,
                      dry_run: bool, client=None) -> list[dict]:
    """给每个切段附上 shot/subject/detail,返回新列表(不修改入参)。"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    model = cfg["api"]["vision_model"]
    out, calls = [], 0
    total = len(segments)
    for i, seg in enumerate(segments, 1):
        cache_file = cache_dir / f"{seg['frame_md5']}.json"
        if cache_file.exists():
            caption = json.loads(cache_file.read_text(encoding="utf-8"))["caption"]
        elif dry_run:
            caption = {"shot": "[dry-run 占位]", "subject": "[dry-run]", "detail": "未调用模型"}
        else:
            if client is None:
                raise RuntimeError("没有可用的 LLM client 且缓存缺失")
            try:
                b64 = to_b64_jpeg(seg["frame_path"], cfg["processing"]["max_image_px"],
                                  cfg["processing"]["jpeg_quality"])
                text = chat(client, model, [{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                        {"type": "text", "text": PROMPT},
                    ],
                }], max_tokens=200, temperature=0.2)
                caption = parse_caption(text)
            except Exception as e:  # 单段失败不中断整条场记单
                print(f"[警告] 段 {seg['index']} 描述失败: {e}", flush=True)
                caption = {"shot": "(识别失败)", "subject": "不确定", "detail": ""}
            cache_file.write_text(json.dumps({
                "md5": seg["frame_md5"], "model": model, "caption": caption,
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            calls += 1
            time.sleep(cfg["processing"]["call_interval_sec"])
        out.append({**seg, **caption})
        print(f"[vision] 段 {i}/{total}({tc(seg['start'])}) 新调用 {calls} 次", flush=True)
    return out
