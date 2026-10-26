"""生成演示原始素材:data/demo/A001.mp4
- 180 秒,6 个视觉上完全不同的场景(每段 30 秒,模拟换机位/换景)
- 3 段 TTS 中文台词分别放在约 5s / 65s / 125s(用于验证转写时间码对位)
用法:python scripts/make_demo_footage.py
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.media import ffmpeg_exe  # noqa: E402

SPEECHES = [
    ("clipA.wav", 5,
     "今天开始拍摄第一组素材,主题是新产品发布。先拍产品外观,注意这个角度的光线。"),
    ("clipB.wav", 65,
     "第二部分讨论项目预算,总预算五十万,截止时间是十一月底,大家务必按时完成评审。"),
    ("clipC.wav", 125,
     "最后补拍几个空镜头,傍晚光线不错,素材就到这里,明天开始整理和剪辑。"),
]

SCENES = [
    "testsrc2=size=640x360:rate=10:duration=30",
    "color=c=red:size=640x360:rate=10:duration=30",
    "smptebars=size=640x360:rate=10:duration=30",
    "rgbtestsrc=size=640x360:rate=10:duration=30",
    "color=c=green:size=640x360:rate=10:duration=30",
    "testsrc=size=640x360:rate=10:duration=30",
]


def make_speech(wav_path: Path, text: str) -> None:
    ps1 = wav_path.with_suffix(".ps1")
    ps1.write_text(
        "Add-Type -AssemblyName System.Speech\n"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
        "$zh = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like 'zh*' } | Select-Object -First 1\n"
        "if ($zh) { $s.SelectVoice($zh.VoiceInfo.Name) }\n"
        f"$s.SetOutputToWaveFile('{wav_path}')\n"
        f"$s.Speak('{text}')\n"
        "$s.Dispose()\n",
        encoding="utf-8-sig",
    )
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)],
                   check=True, capture_output=True)
    ps1.unlink()


def build_video(out: Path, scenes: list[str], wavs: list[Path], delays: list[int],
                total: int) -> None:
    cmd = [ffmpeg_exe(), "-y"]
    for scene in scenes:
        cmd += ["-f", "lavfi", "-i", scene]
    for wav in wavs:
        cmd += ["-i", str(wav)]
    fc = ("".join(f"[{i}:v]" for i in range(len(scenes)))
          + f"concat=n={len(scenes)}:v=1:a=0[v];")
    for j, dl in enumerate(delays):
        idx = len(scenes) + j
        fc += (f"[{idx}:a]aresample=44100,aformat=channel_layouts=stereo,"
               f"adelay={dl}:all=1[a{j}];")
    fc += (f"{''.join(f'[a{j}]' for j in range(len(delays)))}"
           f"amix=inputs={max(len(delays), 1)}:duration=longest:normalize=0[a]")
    cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "[a]",
            "-t", str(total), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(out)]
    subprocess.run(cmd, check=True, capture_output=True)
    print("生成", out)


def make_c003(demo_dir: Path) -> None:
    out = demo_dir / "C003.mp4"
    if out.exists():
        return
    text = "明天上午九点开会,讨论剪辑方案,记得带上分镜脚本和音乐小样。"
    wav = demo_dir / "clipD.wav"
    if not wav.exists():
        print("生成 TTS: clipD.wav")
        make_speech(wav, text)
    build_video(out, [
        "color=c=teal:size=640x360:rate=10:duration=30",
        "testsrc=size=640x360:rate=10:duration=30",
    ], [wav], [10000], 60)


def main() -> None:
    demo_dir = ROOT / "data" / "demo"
    demo_dir.mkdir(parents=True, exist_ok=True)
    make_c003(demo_dir)

    out = demo_dir / "A001.mp4"
    if out.exists():
        print("A001.mp4 已存在,跳过")
        return
    wavs = []
    for name, _sec, text in SPEECHES:
        wav = demo_dir / name
        if not wav.exists():
            print("生成 TTS:", name)
            make_speech(wav, text)
        wavs.append(wav)
    delay = {"clipA.wav": 5000, "clipB.wav": 65000, "clipC.wav": 125000}
    build_video(out, SCENES, wavs, [delay[w.name] for w in wavs], 180)


if __name__ == "__main__":
    main()
