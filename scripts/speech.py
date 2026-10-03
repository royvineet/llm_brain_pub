#!/usr/bin/env python3
"""
Local speech-to-text for Telegram voice notes on Apple Silicon via MLX. Audio
never leaves the machine.

Model: `speech.model` in config.yaml —
  mlx-community/parakeet-tdt-0.6b-v2      (default) NVIDIA Parakeet, English only, fastest
  mlx-community/whisper-large-v3-turbo    OpenAI Whisper, multilingual (needs: pip install mlx-whisper)

The model (~1.2 GB) downloads from Hugging Face on first use and is kept loaded
for the life of the process (the Telegram bot), so each note costs only the
transcription itself. Decoding (Opus/OGG from Telegram, m4a, mp3, …) needs
ffmpeg on PATH.

Usage:
    python scripts/speech.py FILE [FILE ...]     # print transcripts (and timing)
"""

import shutil
import sys
import time
from pathlib import Path

import yaml

CONFIG_PATH = Path.home() / "Documents" / "llm_brain" / "config.yaml"
DEFAULT_MODEL = "mlx-community/parakeet-tdt-0.6b-v2"
_model = None


def model_id() -> str:
    try:
        cfg = yaml.safe_load(CONFIG_PATH.read_text()) or {}
        return (cfg.get("speech") or {}).get("model") or DEFAULT_MODEL
    except OSError:
        return DEFAULT_MODEL


def is_whisper() -> bool:
    return "whisper" in model_id().lower()


def available() -> str | None:
    """None if transcription can work here, else the reason it can't."""
    if not shutil.which("ffmpeg"):
        return "ffmpeg isn't installed (brew install ffmpeg)"
    pkg = "mlx_whisper" if is_whisper() else "parakeet_mlx"
    try:
        __import__(pkg)
    except ImportError:
        return f"{pkg} isn't installed (.venv/bin/pip install {pkg.replace('_', '-')})"
    return None


def model():
    """Load once and keep (Whisper's loader caches internally, so nothing to hold for it)."""
    global _model
    if _model is None and not is_whisper():
        from parakeet_mlx import from_pretrained
        _model = from_pretrained(model_id())
    return _model


def transcribe(path: Path) -> str:
    """Transcribe an audio file to text."""
    if is_whisper():
        import mlx_whisper
        text = mlx_whisper.transcribe(str(path), path_or_hf_repo=model_id())["text"]
    else:
        text = model().transcribe(str(path)).text
    return " ".join(text.split())


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    if problem := available():
        sys.exit(problem)
    t0 = time.time()
    model()
    print(f"[model loaded in {time.time() - t0:.1f}s]", file=sys.stderr)
    for f in sys.argv[1:]:
        t0 = time.time()
        text = transcribe(Path(f))
        print(f"{f}: {text}  [{time.time() - t0:.2f}s]")
