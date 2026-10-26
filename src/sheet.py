"""场记单 Markdown 渲染。"""
from .shots import tc


def _fmt_dur(sec: float) -> str:
    s = int(round(sec))
    return f"{s // 60}分{s % 60:02d}秒" if s >= 60 else f"{s}秒"


def render_sheet(video_name: str, duration: float, width: int, height: int,
                 segments: list[dict], transcript: list[dict],
                 analysis: dict | None, interval: float) -> str:
    n_lines = len(transcript)
    head = [
        f"# {video_name} 场记单",
        "",
        f"> 时长 {tc(duration)} | 分辨率 {width}x{height} | 切段 {len(segments)} 段"
        f"(间隔 {interval:g}s,合并静止段后)| 台词 {n_lines} 句",
        "",
    ]

    if analysis:
        head += ["## 概述", "", analysis["overview"], ""]
        if analysis.get("one_liner"):
            head += [f"**一句话:** {analysis['one_liner']}", ""]
        if analysis.get("tags"):
            head += ["标签:" + " ".join(f"`#{t}`" for t in analysis["tags"]), ""]
        if analysis.get("highlights"):
            head += ["## 关键点", ""]
            for h in analysis["highlights"]:
                star = "★" * h["score"] + "☆" * (3 - h["score"])
                head.append(f"- {star} **{h['time']}** [{h['type']}] {h['reason']}")
            head.append("")
        if analysis.get("chapters"):
            head += ["## 内容分段", ""]
            for ch in analysis["chapters"]:
                s, e = segments[ch["from"]], segments[ch["to"]]
                head.append(f"- **{tc(s['start'])}–{tc(e['end'])} {ch['title']}"
                            f"(段 {ch['from']}–{ch['to']})**:{ch['summary']}")
            head.append("")
    else:
        head += ["## 概述", "", "_(未生成 LLM 分析:未配置 API key,或使用了 --dry-run / --no-summary)_", ""]

    seg_table = ["## 段表", "", "| # | 入点 | 出点 | 时长 | 转场 | 画面 | 主体 | 细节 |",
                 "|---|---|---|---|---|---|---|---|"]
    for s in segments:
        seg_table.append(
            f"| {s['index']} | {tc(s['start'])} | {tc(s['end'])} | {_fmt_dur(s['duration'])} "
            f"| {'⚡' if s.get('transition') else ''} "
            f"| {s['shot']} | {s['subject']} | {s['detail']} |"
        )
    parts = ["\n".join(head), "\n".join(seg_table), ""]

    script = ["## 完整台词", ""]
    if transcript:
        script += [f"- [{tc(t['start'])}] {t['text']}" for t in transcript]
    else:
        script += ["_(无有效台词或未转写)_"]
    script.append("")
    parts.append("\n".join(script))
    return "\n".join(parts)
