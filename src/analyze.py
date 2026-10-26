"""场记分析:LLM 读取段描述、台词与转场点,产出概述/一句话/分段/标签/关键点。

关键点时间码做锚定校正:LLM 给出的时间必须吸附到已知时间锚(台词起点/段起点/转场点,
±5s 内),保证关键点在剪辑软件里跳转能命中真实位置。
"""
import json
import re

from .llm import chat
from .shots import tc

TYPES = ["结论", "待办", "数字", "画面", "演示", "情绪"]

PROMPT_TMPL = """以下是一段原始拍摄素材的场记数据。视频时长 {duration},共 {n_segs} 个时间段。

## 各时间段画面({mark} 为检测到的转场,即画面显著变化点)
{segments_text}

## 台词(带片内时间码)
{transcript_text}

## 检测到的转场时间点
{transitions_text}

请输出一个 JSON 对象(不要输出 JSON 以外的任何文字):
{{
  "overview": "这段素材拍了什么(2-3 句,供剪辑师快速判断)",
  "one_liner": "一句话概括这条素材",
  "tags": ["标签1", "标签2", "标签3"],
  "chapters": [
    {{"from": 0, "to": 2, "title": "章节标题(10字内)", "summary": "这段讲了什么(1句)"}}
  ],
  "highlights": [
    {{"time": "MM:SS", "type": "结论", "score": 3, "reason": "为什么值得剪进成片(20字内)"}}
  ]
}}

要求:
- chapters 的 from/to 是时间段的序号(从 0 开始,含首尾),按顺序把相近内容的时间段归为一章
- 台词里的关键信息(结论、数字、待办)应体现在对应章节的 summary 或 overview 里
- tags 3-5 个,描述主题/场景/人物类型
- highlights 3-8 条,按重要程度给 score(3=必须用,2=可用,1=备用);
  type 从这些里选: {types}。台词相关的 time 用台词时间码,画面相关的 time 用段起点或转场点
"""


def _segments_text(segments: list[dict]) -> str:
    lines = []
    for s in segments:
        multi = f"(该段含 {s['frame_count']} 个相近帧)" if s.get("frame_count", 1) > 1 else ""
        mark = " ⚡转场" if s.get("transition") else ""
        lines.append(
            f"段{s['index']} {tc(s['start'])}-{tc(s['end'])}: {s['shot']};主体: {s['subject']};"
            f"细节: {s['detail']}{multi}{mark}"
        )
    return "\n".join(lines)


def _transcript_text(transcript: list[dict]) -> str:
    if not transcript:
        return "(无有效台词)"
    return "\n".join(f"[{tc(t['start'])}] {t['text']}" for t in transcript)


def _tc_to_sec(s: str) -> float | None:
    parts = s.strip().split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    return None


def _snap_time(sec: float, anchors: list[float], tolerance: float = 5.0) -> float:
    """吸附到最近的时间锚(台词起点/段起点/转场点);超出容差保持原值。"""
    if not anchors:
        return sec
    nearest = min(anchors, key=lambda a: abs(a - sec))
    return nearest if abs(nearest - sec) <= tolerance else sec


def _parse(text: str, n_segs: int, anchors: list[float]) -> dict | None:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group())
    except json.JSONDecodeError:
        return None
    chapters = []
    for ch in data.get("chapters", []):
        try:
            f, t = int(ch["from"]), int(ch["to"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= f <= t < n_segs:
            chapters.append({
                "from": f, "to": t,
                "title": str(ch.get("title", ""))[:20],
                "summary": str(ch.get("summary", "")),
            })
    highlights = []
    for h in data.get("highlights", []):
        sec = _tc_to_sec(str(h.get("time", "")))
        if sec is None:
            continue
        try:
            score = max(1, min(3, int(h.get("score", 2))))
        except (TypeError, ValueError):
            score = 2
        sec = _snap_time(sec, anchors)
        highlights.append({
            "time": tc(sec),
            "type": str(h.get("type", "")) if str(h.get("type", "")) in TYPES else "画面",
            "score": score,
            "reason": str(h.get("reason", ""))[:60],
        })
    highlights.sort(key=lambda x: _tc_to_sec(x["time"]) or 0)
    return {
        "overview": str(data.get("overview", "")),
        "one_liner": str(data.get("one_liner", "")),
        "tags": [str(x) for x in data.get("tags", [])][:6],
        "chapters": chapters,
        "highlights": highlights[:10],
    }


def run_analyze(client, cfg: dict, duration: float, segments: list[dict],
                transcript: list[dict], transitions: list[float] | None = None) -> dict | None:
    """返回结构化分析;失败返回 None(场记单降级为纯段表+台词)。"""
    transitions = transitions or []
    anchors = sorted({*[t["start"] for t in transcript],
                      *[s["start"] for s in segments], *transitions})
    prompt = PROMPT_TMPL.format(
        duration=tc(duration), n_segs=len(segments), mark="⚡",
        segments_text=_segments_text(segments), transcript_text=_transcript_text(transcript),
        transitions_text="、".join(tc(t) for t in transitions) if transitions else "(未检测到)",
        types="、".join(TYPES),
    )
    text = chat(client, cfg["api"]["text_model"],
                [{"role": "user", "content": prompt}],
                max_tokens=1500, temperature=0.4)
    analysis = _parse(text, len(segments), anchors)
    if analysis is None:
        print("[警告] 场记分析 JSON 解析失败,场记单将不含概述/分段")
    return analysis
