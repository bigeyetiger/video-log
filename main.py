"""video-log 视频场记系统(阶段3:场记单 + 素材库检索)

用法:
  python main.py log --video "D:/素材/A001.mp4" [--interval 30] [--dry-run] [--refresh]
  python main.py log --dir "D:/素材"              # 批量入库(已分析的自动跳过)
  python main.py search "预算 截止时间" [--tag 标签] [--after 2026-10-01]
  python main.py list                             # 素材库清单
  python main.py report A001                      # 查看某条场记单

所有中间产物(段帧/描述/转写/转场)按 md5 缓存在 data/cache/,重跑零成本;
分析结果入库 data/shotlog.db,支持全库台词/画面/标签检索。
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from src import config as cfgmod
from src import analyze, asr, media, sheet, shots, store, vision

ROOT = Path(__file__).resolve().parent


def log_one(video_path: Path, cfg: dict, interval: float, dry_run: bool,
            skip_asr: bool, no_summary: bool, model=None,
            conn=None, refresh: bool = False):
    """分析单条视频,生成场记单并入库;返回 (sheet_path, model)。"""
    cache_root = cfgmod.data_dir(ROOT, cfg) / "cache"
    print(f"\n=== {video_path.name} ===")

    v_md5 = shots.file_md5(video_path)
    if conn is not None and not refresh:
        existing = store.get_video(conn, v_md5)
        if existing and existing["analyzed"] and existing["interval"] == interval:
            print("[skip] 已在库中且已分析,跳过(--refresh 强制重跑)")
            return None, model

    info = media.probe(video_path)
    duration = info["duration_sec"] or 0
    if duration <= 0:
        print("[警告] 读不到视频时长,跳过")
        return None, model
    print(f"[probe] 时长 {shots.tc(duration)} | creation_time={info['creation_time']}")

    transitions = shots.detect_transitions(
        str(video_path), v_md5, cfg["shots"]["transition_threshold"], cache_root)
    print(f"[shots] 检测到 {len(transitions)} 个转场点")

    segments = shots.make_segments(
        str(video_path), v_md5, duration, interval,
        cfg["shots"]["frame_max_px"], cache_root, cfg["shots"]["merge_hash_dist"],
        transitions=transitions, snap_ratio=cfg["shots"]["snap_ratio"])
    n_snapped = sum(1 for s in segments if s["transition"])
    print(f"[shots] 抽帧 {sum(s['frame_count'] for s in segments)} 帧 -> {len(segments)} 段"
          f"(合并静止段后,{n_snapped} 段起始于转场)")

    client = None
    if dry_run:
        print("[vision] dry-run:不调用视觉模型(转写仍本地真实执行)")
    elif not cfg["api"]["api_key"]:
        print("错误: 未配置 API key。请设置环境变量 DAILYLOG_API_KEY 或 config.yaml;跑通流程可加 --dry-run")
        sys.exit(2)
    else:
        from src.llm import make_client
        client = make_client(cfg)

    segs = vision.describe_segments(segments, cfg, cache_root / "captions",
                                    dry_run=dry_run, client=client)

    transcript, model = [], None
    if info["has_audio"] and not skip_asr:
        print("[asr] 转写台词 ...")
        transcript, model = asr.run_asr(str(video_path), v_md5, cfg,
                                        cache_root / "asr", model=model)
    elif skip_asr:
        print("[asr] --skip-asr:跳过转写")
    else:
        print("[asr] 该视频无音轨,跳过转写")

    analysis = None
    if client and not no_summary:
        print("[analyze] 生成概述/关键点/分段/标签 ...")
        analysis = analyze.run_analyze(client, cfg, duration, segs, transcript, transitions)

    md = sheet.render_sheet(video_path.name, duration, info["width"], info["height"],
                            segs, transcript, analysis, interval)
    out = cfgmod.sheets_dir(ROOT, cfg) / f"{video_path.stem}_{v_md5[:6]}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(f"[sheet] 场记单已生成: {out}")

    if conn is not None:
        highlights = (analysis or {}).get("highlights", [])
        for h in highlights:
            h["_sec"] = analyze._tc_to_sec(h["time"]) or 0
        video_row = {
            "md5": v_md5, "name": video_path.name, "path": str(video_path.resolve()),
            "duration": duration, "width": info["width"], "height": info["height"],
            "file_mtime": datetime.fromtimestamp(video_path.stat().st_mtime)
            .isoformat(timespec="seconds"),
            "interval": interval, "n_segments": len(segs), "n_lines": len(transcript),
            "overview": (analysis or {}).get("overview"),
            "one_liner": (analysis or {}).get("one_liner"),
            "tags": json.dumps((analysis or {}).get("tags", []), ensure_ascii=False),
            "sheet_path": str(out), "analyzed": 1 if analysis else 0,
        }
        store.save_video(conn, video_row, segs, transcript, highlights, md)
        print(f"[db] 已入库(段 {len(segs)}、台词 {len(transcript)} 行)")
    return out, model


def cmd_log(args) -> int:
    cfg = cfgmod.load_config(ROOT, Path(args.config) if args.config else None)
    interval = args.interval or cfg["shots"]["interval_sec"]

    videos: list[Path] = []
    for v in args.video or []:
        p = Path(v)
        if not p.exists():
            print(f"[警告] 文件不存在: {v}")
            continue
        videos.append(p)
    for d in args.dir or []:
        root = Path(d)
        if not root.exists():
            print(f"[警告] 目录不存在: {d}")
            continue
        exts = {e.lower() for e in cfg["processing"]["video_ext"]}
        videos += [p for p in sorted(root.rglob("*"))
                   if p.is_file() and p.suffix.lower() in exts]
    if not videos:
        print("错误: 请用 --video 指定视频文件,或 --dir 指定素材目录")
        return 2

    conn = store.connect(cfgmod.data_dir(ROOT, cfg) / "shotlog.db")
    model = None
    done = skipped = 0
    for v in videos:
        try:
            out, model = log_one(v, cfg, interval, args.dry_run, args.skip_asr,
                                 args.no_summary, model, conn=conn, refresh=args.refresh)
            if out:
                done += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"[错误] 处理 {v.name} 失败: {e}")
    conn.close()
    print(f"\n完成:{done}/{len(videos)} 条场记单(跳过 {skipped} 条)")
    return 0


def cmd_search(args) -> int:
    cfg = cfgmod.load_config(ROOT, Path(args.config) if args.config else None)
    db_path = cfgmod.data_dir(ROOT, cfg) / "shotlog.db"
    if not db_path.exists():
        print("素材库为空,先用 log 命令入库。")
        return 1
    conn = store.connect(db_path)
    rows = store.search(conn, args.query, tag=args.tag, after=args.after,
                        before=args.before, kind=args.kind, limit=args.limit)
    total = store.video_count(conn)
    names = {r["md5"]: r["name"] for r in conn.execute("SELECT md5, name FROM videos")}
    conn.close()
    print(f'检索 "{args.query}" — 命中 {len(rows)} 条(库内 {total} 条视频)')
    if not rows:
        return 0
    for r in rows:
        md5 = r["video_md5"]
        t = shots.tc(r["t_start"])
        span = f"–{shots.tc(r['t_end'])}" if r["kind"] == "画面" else ""
        print(f"▶ {names.get(md5, md5[:8])} {t}{span} [{r['kind']}] {r['text']}")
    print("\n↳ 用 report <视频名> 查看完整场记单")
    return 0


def cmd_list(args) -> int:
    cfg = cfgmod.load_config(ROOT, Path(args.config) if args.config else None)
    db_path = cfgmod.data_dir(ROOT, cfg) / "shotlog.db"
    if not db_path.exists():
        print("素材库为空,先用 log 命令入库。")
        return 1
    conn = store.connect(db_path)
    rows = store.list_videos(conn)
    conn.close()
    if not rows:
        print("素材库为空。")
        return 0
    print(f"素材库共 {len(rows)} 条视频(按文件时间倒序):\n")
    for r in rows:
        tags = ", ".join(json.loads(r["tags"] or "[]"))
        flag = "✓已分析" if r["analyzed"] else "(未做LLM分析)"
        overview = (r["overview"] or "")[:36]
        print(f"  {r['name']}  {shots.tc(r['duration'])}  段{r['n_segments']}"
              f"/台词{r['n_lines']}  {flag}")
        print(f"    时间 {r['file_mtime'][:10]}  间隔{r['interval']:g}s"
              + (f"  标签: {tags}" if tags else ""))
        if overview:
            print(f"    {overview}")
    return 0


def cmd_report(args) -> int:
    cfg = cfgmod.load_config(ROOT, Path(args.config) if args.config else None)
    db_path = cfgmod.data_dir(ROOT, cfg) / "shotlog.db"
    if not db_path.exists():
        print("素材库为空,先用 log 命令入库。")
        return 1
    conn = store.connect(db_path)
    row = store.get_video(conn, args.video)
    conn.close()
    if row is None:
        print(f"素材库中没有 {args.video}(可用 list 查看库内视频)。")
        return 1
    if not row["sheet_md"]:
        print(f"{row['name']} 尚无场记单。")
        return 1
    print(row["sheet_md"])
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="video-log 视频场记系统(阶段3)")
    sub = parser.add_subparsers(dest="command", required=True)

    log = sub.add_parser("log", help="分析视频,生成场记单并入库")
    log.add_argument("--video", action="append", default=[], help="视频文件,可多次指定")
    log.add_argument("--dir", action="append", default=[], help="素材目录(递归),可多次指定")
    log.add_argument("--interval", type=float, help="切段间隔秒数,默认取 config(shots.interval_sec)")
    log.add_argument("--dry-run", action="store_true", help="不调用视觉模型(转写仍本地执行)")
    log.add_argument("--skip-asr", action="store_true", help="跳过语音转写")
    log.add_argument("--no-summary", action="store_true", help="跳过 LLM 概述/关键点")
    log.add_argument("--refresh", action="store_true", help="已在库中的视频也强制重新分析")
    log.add_argument("--config", help="config.yaml 路径")
    log.set_defaults(func=cmd_log)

    sea = sub.add_parser("search", help="全库检索:台词/画面描述/概述/标签")
    sea.add_argument("query", help="检索词,空格分隔多个词(AND)")
    sea.add_argument("--tag", help="按标签过滤")
    sea.add_argument("--after", help="素材文件时间不早于 YYYY-MM-DD")
    sea.add_argument("--before", help="素材文件时间不晚于 YYYY-MM-DD")
    sea.add_argument("--kind", choices=["台词", "画面", "概述"], help="限定检索范围")
    sea.add_argument("--limit", type=int, default=20)
    sea.add_argument("--config", help="config.yaml 路径")
    sea.set_defaults(func=cmd_search)

    lst = sub.add_parser("list", help="素材库清单")
    lst.add_argument("--config", help="config.yaml 路径")
    lst.set_defaults(func=cmd_list)

    rep = sub.add_parser("report", help="查看某条视频的场记单")
    rep.add_argument("video", help="视频名或 md5(可前缀)")
    rep.add_argument("--config", help="config.yaml 路径")
    rep.set_defaults(func=cmd_report)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
