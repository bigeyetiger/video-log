# video-log — 视频场记系统

输入视频素材,自动产出"拍了什么、主要内容分段、关键点时点"的**场记单**,并形成可全文检索的素材库,服务剪辑选材与素材查找。针对**原始拍摄素材**(长镜头多、切换少)设计:固定间隔切段为主骨架,ffmpeg 场景检测标记转场并吸附切分边界。

- **转写本地跑**(faster-whisper,零 token),画面理解走 OpenAI 兼容多模态 API(默认智谱免费档)
- 输出纯 Markdown 场记单
- 与 daily-log 同栈独立项目,复用其 media/llm 模块

## 功能

- **场记单**:概述 + 一句话总结、关键点(★ 三档评分 + 时间码 + 类型 + 理由)、内容分段、段表(入出点/转场标记/画面/主体/细节)、完整带时间戳台词
- **转场检测与边界吸附**:固定间隔切段的边界在容差内自动吸附到场景切换点
- **关键点时间码锚定**:LLM 给出的时间码强制吸附到已知时间锚(台词起点/段起点/转场点,±5s),保证跳转命中
- **素材库检索**:台词 / 画面描述 / 概述标签全文索引(FTS5 trigram + 短词 LIKE 兜底),按标签/日期过滤
- 静止段 dHash 合并、转写后 VAD 时间戳校正(修 faster-whisper 跨静音错位)、无音轨视频自动跳过转写
- 全程 md5 缓存:重跑、断点续跑零成本

## 快速开始

```bash
pip install -r requirements.txt        # ffmpeg 随 imageio-ffmpeg 自带

# 1) dry-run 跑通流程(不调视觉模型,转写仍真实执行)
python main.py log --video "D:/素材/A001.mp4" --dry-run

# 2) 完整分析并入库
set DAILYLOG_API_KEY=你的智谱key
python main.py log --dir "D:/素材"              # 批量入库(已分析的自动跳过)
python main.py search "预算 截止时间"            # 找"哪条视频第几分钟说的"
python main.py list                             # 素材库总览
python main.py report A001.mp4                  # 调出场记单
```

场记单输出到 `sheets/<视频名>_<md5前6位>.md`,归档库为 `data/shotlog.db`。

## 命令参考

```bash
python main.py log --video <文件> [--video ...] [--dir <目录> ...]
                  [--interval 30] [--dry-run] [--skip-asr] [--no-summary] [--refresh]
python main.py search <关键词> [--tag 标签] [--after 2026-10-01] [--before 2026-10-31]
                     [--kind 台词|画面|概述] [--limit 20]
python main.py list
python main.py report <视频名或md5前缀>
```

多关键词空格分隔,AND 语义;`--interval` 控制切段密度(粗看 60 / 默认 30 / 细看 15)。

## 中文检索说明

FTS5 trigram 分词器要求检索词 ≥3 字符;含双字词(如"预算""光线")时自动降级 LIKE 兜底,两条路径输出格式一致。建议优先用 ≥3 字的词获得索引加速与标准高亮。

## 配置(config.yaml)

| 配置 | 说明 |
|---|---|
| `shots.interval_sec` | 固定切段间隔(主骨架) |
| `shots.transition_threshold` | 场景检测阈值(0-1,默认 0.3;误报多则调高) |
| `shots.snap_ratio` | 切分边界向转场吸附的最大距离(占间隔比例) |
| `shots.merge_hash_dist` | 相邻段 dHash 合并阈值(纯色帧不参与合并) |
| `asr.model` | faster-whisper 模型 base/small/medium |

## 项目结构

```
main.py              # CLI(log / search / list / report)
config.yaml
src/
  shots.py           # 转场检测、边界吸附、切段、中点抽帧、dHash 合并
  media.py           # ffmpeg 定位、探测(复用 daily-log)
  asr.py             # 本地转写(片内时间码)+ VAD 时间戳校正
  vision.py          # 段代表帧理解(md5 缓存)
  analyze.py         # LLM:概述/一句话/关键点/分段/标签(结构化 JSON + 时间码锚定)
  store.py           # SQLite + FTS5 素材库与检索
  sheet.py           # Markdown 场记单渲染
  llm.py             # OpenAI 兼容调用封装(复用 daily-log)
data/                # cache(frames/hashes/captions/asr/transitions)/ shotlog.db
sheets/              # 场记单输出
scripts/             # 演示素材生成
```

## 已知限制

- LLM 层(画面描述、概述、关键点、标签)尚未用真实 API key 做过质量验收,机制层已全部实测
- dry-run 产生的占位画面描述会入库,正式跑同一条(`--refresh`)会覆盖
- 人物识别依赖 VLM 能力,准确率有限;关键点评分主观,需人工快速复核
