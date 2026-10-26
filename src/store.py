"""素材库:SQLite + FTS5(trigram)全文索引。

中文检索采用混合策略:
- 检索词全部 ≥3 字:FTS5 MATCH(trigram 索引,snippet 高亮)
- 含 <3 字的词(常见双字词):LIKE 兜底(全表扫描,本库量级足够快),手工截取上下文
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
  md5 TEXT PRIMARY KEY, name TEXT, path TEXT,
  duration REAL, width INTEGER, height INTEGER,
  file_mtime TEXT, interval REAL,
  n_segments INTEGER, n_lines INTEGER,
  overview TEXT, one_liner TEXT, tags TEXT,
  sheet_path TEXT, sheet_md TEXT,
  analyzed INTEGER DEFAULT 0, added_at TEXT
);
CREATE TABLE IF NOT EXISTS segments (
  video_md5 TEXT, idx INTEGER, start REAL, end REAL, duration REAL,
  transition INTEGER, frame_count INTEGER,
  shot TEXT, subject TEXT, detail TEXT,
  PRIMARY KEY (video_md5, idx)
);
CREATE TABLE IF NOT EXISTS highlights (
  video_md5 TEXT, time_sec REAL, time TEXT, type TEXT, score INTEGER, reason TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
  text, kind, video_md5 UNINDEXED, t_start UNINDEXED, t_end UNINDEXED,
  tokenize='trigram'
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def save_video(conn: sqlite3.Connection, video: dict, segments: list[dict],
               transcript: list[dict], highlights: list[dict], sheet_md: str) -> None:
    """一条视频的全部分析结果入库;同 md5 重跑覆盖。"""
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO videos (md5, name, path, duration, width, height,"
            " file_mtime, interval, n_segments, n_lines, overview, one_liner, tags,"
            " sheet_path, sheet_md, analyzed, added_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (video["md5"], video["name"], video["path"], video["duration"],
             video["width"], video["height"], video["file_mtime"], video["interval"],
             video["n_segments"], video["n_lines"], video.get("overview"),
             video.get("one_liner"), video.get("tags", "[]"),
             video.get("sheet_path"), sheet_md, video.get("analyzed", 0),
             datetime.now().isoformat(timespec="seconds")),
        )
        conn.execute("DELETE FROM segments WHERE video_md5 = ?", (video["md5"],))
        for s in segments:
            conn.execute(
                "INSERT INTO segments VALUES (?,?,?,?,?,?,?,?,?,?)",
                (video["md5"], s["index"], s["start"], s["end"], s["duration"],
                 int(bool(s.get("transition"))), s.get("frame_count", 1),
                 s.get("shot"), s.get("subject"), s.get("detail")),
            )
        conn.execute("DELETE FROM highlights WHERE video_md5 = ?", (video["md5"],))
        for h in highlights or []:
            conn.execute(
                "INSERT INTO highlights VALUES (?,?,?,?,?,?)",
                (video["md5"], h.get("_sec", 0), h["time"], h["type"], h["score"], h["reason"]),
            )
        conn.execute("DELETE FROM fts WHERE video_md5 = ?", (video["md5"],))
        fts_rows = []
        if video.get("overview"):
            tags = ", ".join(json.loads(video.get("tags") or "[]"))
            fts_rows.append((f"{video['overview']} 标签: {tags}", "概述",
                             video["md5"], 0.0, video["duration"]))
        for s in segments:
            fts_rows.append((f"{s.get('shot', '')} {s.get('subject', '')} {s.get('detail', '')}",
                             "画面", video["md5"], s["start"], s["end"]))
        for t in transcript:
            fts_rows.append((t["text"], "台词", video["md5"], t["start"], t["end"]))
        conn.executemany("INSERT INTO fts (text, kind, video_md5, t_start, t_end)"
                         " VALUES (?,?,?,?,?)", fts_rows)


def get_video(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    """按 md5(可前缀)或文件名查视频。"""
    row = conn.execute("SELECT * FROM videos WHERE md5 = ?", (key,)).fetchone()
    if row:
        return row
    row = conn.execute("SELECT * FROM videos WHERE md5 LIKE ?", (f"{key}%",)).fetchone()
    if row:
        return row
    return conn.execute("SELECT * FROM videos WHERE name = ?", (key,)).fetchone()


def video_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]


def list_videos(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM videos ORDER BY file_mtime DESC").fetchall()


def _video_subfilter(tag: str | None, after: str | None, before: str | None):
    conds, params = [], []
    if tag:
        conds.append("tags LIKE ?")
        params.append(f'%"{tag}"%')
    if after:
        conds.append("file_mtime >= ?")
        params.append(after)
    if before:
        conds.append("file_mtime <= ?")
        params.append(before + "T23:59:59")
    if not conds:
        return "1=1", []
    return " AND ".join(conds), params


def _excerpt(text: str, terms: list[str], radius: int = 30) -> str:
    """LIKE 路径的手工高亮:围绕首个命中截取,括号标出所有命中词。"""
    pos = min((text.find(t) for t in terms if text.find(t) >= 0), default=0)
    lo, hi = max(0, pos - radius), min(len(text), pos + radius + max(len(t) for t in terms))
    piece = text[lo:hi]
    for t in sorted(terms, key=len, reverse=True):
        piece = piece.replace(t, f"[{t}]")
    return ("…" if lo > 0 else "") + piece + ("…" if hi < len(text) else "")


def search(conn: sqlite3.Connection, query: str, tag: str | None = None,
           after: str | None = None, before: str | None = None,
           kind: str | None = None, limit: int = 20) -> list[dict]:
    terms = [t for t in query.split() if t]
    if not terms:
        return []
    vcond, vparams = _video_subfilter(tag, after, before)
    vsub = f"video_md5 IN (SELECT md5 FROM videos WHERE {vcond})"
    kind_cond = "AND kind = ?" if kind else ""
    kind_param = [kind] if kind else []

    if all(len(t) >= 3 for t in terms):
        expr = " AND ".join('"' + t.replace('"', '""') + '"' for t in terms)
        rows = conn.execute(
            f"SELECT video_md5, kind, t_start, t_end,"
            f" snippet(fts, 0, '[', ']', '…', 16) AS snip"
            f" FROM fts WHERE fts MATCH ? AND {vsub} {kind_cond}"
            f" ORDER BY rank LIMIT ?",
            [expr, *vparams, *kind_param, limit]).fetchall()
        return [dict(r) | {"text": r["snip"]} for r in rows]

    like_conds = " AND ".join("text LIKE ?" for _ in terms)
    rows = conn.execute(
        f"SELECT video_md5, kind, t_start, t_end, text FROM fts"
        f" WHERE {like_conds} AND {vsub} {kind_cond} LIMIT ?",
        [*[f"%{t}%" for t in terms], *vparams, *kind_param, limit]).fetchall()
    return [dict(r) | {"text": _excerpt(r["text"], terms)} for r in rows]
