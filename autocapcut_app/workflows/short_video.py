from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from autocapcut_app.core.template_config import normalize_template_config
from autocapcut_app.core.motion import export_motion_filter
from autocapcut_app.core.video_grade import is_video_grade_enabled, video_grade_filter
from autocapcut_app.paths import ensure_vendor_on_path, require_vendor


LogFn = Callable[[str], None]

CAPTION_LEVEL_BY_STYLE = {"clean": 1, "variety": 2, "pop": 3}
CAPTION_COLOR_NAME_TO_CODE = {
    "white": "w",
    "cream": "w",
    "gold": "y",
    "yellow": "y",
    "cyan": "g",
    "lime": "g",
    "green": "g",
    "magenta": "r",
    "red": "r",
    "orange": "o",
}


@dataclass(frozen=True)
class ClipSegment:
    path: Path
    start: float
    duration: float
    motion: str = "none"
    grade: str = "inherit"
    transition: str = "inherit"


@dataclass(frozen=True)
class CaptionSegment:
    text: str
    color: str = "w"


@dataclass(frozen=True)
class CaptionBlock:
    start: float
    end: float
    segments: list[CaptionSegment]
    kind: str = "main"
    style: str = "manual"
    emphasis: list[str] | None = None
    karaoke: str = "off"


@dataclass(frozen=True)
class ShortVideoJob:
    clips: list[ClipSegment]
    captions: list[CaptionBlock]
    bgm: Path | None
    output: Path
    volume: float = 0.42
    fade: float = 1.2
    bgm_start: str | float = "auto"
    main_caption_y: int = 1180
    addr_caption_y: int = 1390
    video_template: str = "Basic Subtitle"
    template_config: dict[str, Any] | None = None
    video_encoder: str = "auto"
    video_quality: str = "fast"
    font_dir: Path | None = None


def _log(log: LogFn | None, message: str) -> None:
    if log:
        log(message)


def _path(value: Any, field: str) -> Path:
    if not value:
        raise ValueError(f"{field} is required")
    return Path(str(value)).expanduser()


def parse_job(data: dict[str, Any]) -> ShortVideoJob:
    clips_raw = data.get("clips") or []
    captions_raw = data.get("captions") or []
    if not clips_raw:
        raise ValueError("At least one clip is required")
    if not captions_raw:
        raise ValueError("At least one caption block is required")

    clips: list[ClipSegment] = []
    for idx, item in enumerate(clips_raw):
        p = _path(item.get("path"), f"clips[{idx}].path")
        start = float(item.get("start", 0))
        duration = float(item.get("duration", 0))
        if duration <= 0:
            raise ValueError(f"clips[{idx}].duration must be > 0")
        clips.append(
            ClipSegment(
                p,
                start,
                duration,
                str(item.get("motion", "none") or "none"),
                str(item.get("grade", "inherit") or "inherit"),
                str(item.get("transition", "inherit") or "inherit"),
            )
        )

    captions: list[CaptionBlock] = []
    for idx, item in enumerate(captions_raw):
        parts_raw = item.get("segments") or []
        if not parts_raw:
            raise ValueError(f"captions[{idx}].segments is required")
        parts: list[CaptionSegment] = []
        for part in parts_raw:
            if isinstance(part, str):
                parts.append(CaptionSegment(part, "w"))
            else:
                text = str(part[0] if isinstance(part, (list, tuple)) else part.get("text", ""))
                color = str(part[1] if isinstance(part, (list, tuple)) and len(part) > 1 else part.get("color", "w"))
                if text:
                    parts.append(CaptionSegment(text, color))
        start = float(item.get("start", 0))
        end = float(item.get("end", 0))
        if end <= start:
            raise ValueError(f"captions[{idx}].end must be greater than start")
        emphasis_raw = item.get("emphasis", [])
        if isinstance(emphasis_raw, str):
            emphasis = [part.strip() for part in re.split(r"[,，\n]", emphasis_raw) if part.strip()]
        elif isinstance(emphasis_raw, list):
            emphasis = [str(part).strip() for part in emphasis_raw if str(part).strip()]
        else:
            emphasis = []
        captions.append(
            CaptionBlock(
                start,
                end,
                parts,
                str(item.get("kind", "main")),
                str(item.get("style", "manual") or "manual"),
                emphasis,
                str(item.get("karaoke", "off") or "off"),
            )
        )

    template_name = str(data.get("video_template") or data.get("effect_template") or "Basic Subtitle")
    template_config = normalize_template_config(template_name, data.get("template_config") if isinstance(data.get("template_config"), dict) else None)
    caption_config = template_config.get("caption", {}) if isinstance(template_config, dict) else {}
    audio_config = template_config.get("audio", {}) if isinstance(template_config, dict) else {}

    bgm_value = data.get("bgm")
    bgm = Path(str(bgm_value)).expanduser() if bgm_value else None

    return ShortVideoJob(
        clips=clips,
        captions=captions,
        bgm=bgm,
        output=_path(data.get("output"), "output"),
        volume=float(data.get("volume", audio_config.get("volume", 0.42))),
        fade=float(data.get("fade", audio_config.get("fade", 1.2))),
        bgm_start=data.get("bgm_start", audio_config.get("bgm_start", "auto")),
        main_caption_y=int(data.get("main_caption_y", caption_config.get("main_y", 1180))),
        addr_caption_y=int(data.get("addr_caption_y", caption_config.get("addr_y", 1390))),
        video_template=template_name,
        template_config=template_config,
        video_encoder=str(data.get("video_encoder") or "auto"),
        video_quality=str(data.get("video_quality") or "fast"),
        font_dir=Path(str(data.get("font_dir"))).expanduser() if data.get("font_dir") else None,
    )


def job_to_vendor_args(job: ShortVideoJob) -> tuple[list[tuple[str, float, float]], list[tuple[float, float, list[tuple[str, str]], str]]]:
    segs = [(str(c.path), c.start, c.duration) for c in job.clips]
    caps = _caption_blocks_to_vendor_caps(job.captions)
    return segs, caps


def _caption_blocks_to_vendor_caps(
    captions: list[CaptionBlock],
) -> list[tuple[float, float, list[tuple[str, str]], str]]:
    out: list[tuple[float, float, list[tuple[str, str]], str]] = []
    for block in captions:
        style = (block.style or "manual").strip().lower()
        karaoke = (block.karaoke or "off").strip().lower()
        if karaoke in {"active", "highlight", "reveal"}:
            out.extend(_karaoke_caps(block, style))
            continue
        out.append((block.start, block.end, _styled_caption_segments(block, style), block.kind))
    return out


def _styled_caption_segments(block: CaptionBlock, style: str | None = None) -> list[tuple[str, str]]:
    normalized_style = (style or block.style or "manual").strip().lower()
    if normalized_style in {"manual", "none", ""}:
        return [(seg.text, seg.color) for seg in block.segments]
    try:
        from silent_vlog_maker.shorts_captions import style_caption

        level = CAPTION_LEVEL_BY_STYLE.get(normalized_style, 2)
        tokens = style_caption(_caption_text(block), level=level, emphasis=block.emphasis or [])
        return [(str(text), CAPTION_COLOR_NAME_TO_CODE.get(str(color).lower(), "w")) for text, color, _size in tokens]
    except Exception:
        return [(seg.text, seg.color) for seg in block.segments]


def _karaoke_caps(block: CaptionBlock, style: str) -> list[tuple[float, float, list[tuple[str, str]], str]]:
    text = _caption_text(block)
    if not text:
        return []
    try:
        from silent_vlog_maker.shorts_captions import chunk_caption, style_chunks_active

        chunks = chunk_caption(text, phrases_per_chunk=2)
        level = CAPTION_LEVEL_BY_STYLE.get((style or "variety").strip().lower(), 2)
        styled = style_chunks_active(chunks, level=level, karaoke=True)
    except Exception:
        chunks = [text[i : i + 4] for i in range(0, len(text), 4)] or [text]
        styled = [
            (
                chunk,
                [(part, "gold" if idx == active_idx else "white", 1.0) for idx, part in enumerate(chunks)],
            )
            for active_idx, chunk in enumerate(chunks)
        ]

    duration = max(0.05, block.end - block.start)
    step = duration / max(1, len(styled))
    out: list[tuple[float, float, list[tuple[str, str]], str]] = []
    for index, (_active, tokens) in enumerate(styled):
        start = block.start + index * step
        end = block.end if index == len(styled) - 1 else start + step
        segs = [(str(text), CAPTION_COLOR_NAME_TO_CODE.get(str(color).lower(), "w")) for text, color, _size in tokens]
        out.append((start, end, segs, block.kind))
    return out


def _caption_text(block: CaptionBlock) -> str:
    return "".join(segment.text for segment in block.segments).strip()


def _shift_captions(captions: list[CaptionBlock], seconds: float) -> list[CaptionBlock]:
    return [
        CaptionBlock(
            start=block.start + seconds,
            end=block.end + seconds,
            segments=block.segments,
            kind=block.kind,
            style=block.style,
            emphasis=block.emphasis,
            karaoke=block.karaoke,
        )
        for block in captions
    ]


def _teaching_captions(captions: list[CaptionBlock]) -> list[CaptionBlock]:
    accents = ["y", "g", "o"]
    out: list[CaptionBlock] = []
    for index, block in enumerate(captions):
        text = _caption_text(block)
        if not text:
            out.append(block)
            continue
        split_at = min(max(2, len(text) // 3), 6)
        color = accents[index % len(accents)]
        parts = [CaptionSegment(text[:split_at], color)]
        if text[split_at:]:
            parts.append(CaptionSegment(text[split_at:], "w"))
        out.append(CaptionBlock(block.start, block.end, parts, block.kind, block.style, block.emphasis, block.karaoke))
    return out


def _food_travel_captions(captions: list[CaptionBlock]) -> list[CaptionBlock]:
    accents = ["o", "y", "g"]
    return [
        CaptionBlock(
            block.start,
            block.end,
            [CaptionSegment(_caption_text(block), accents[index % len(accents)])],
            block.kind,
            block.style,
            block.emphasis,
            block.karaoke,
        )
        for index, block in enumerate(captions)
        if _caption_text(block)
    ]


def _apply_caption_strategy(captions: list[CaptionBlock], strategy: str) -> list[CaptionBlock]:
    normalized = (strategy or "manual").strip().lower()
    if normalized == "teaching_accent":
        return _teaching_captions(captions)
    if normalized == "food_travel_accent":
        return _food_travel_captions(captions)
    return captions


def _caption_title(job: ShortVideoJob, fallback: str = "今天的重點") -> str:
    for block in job.captions:
        text = _caption_text(block)
        if text:
            return text[:28]
    return fallback


def _available_ffmpeg_encoders() -> set[str]:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        return set()
    return set(result.stdout.split())


def _resolve_video_encoder(choice: str, log: LogFn | None = None) -> str:
    choice = (choice or "auto").strip().lower()
    label_aliases = {
        "auto gpu": "auto",
        "cpu libx264": "cpu",
        "nvidia nvenc": "h264_nvenc",
        "intel quick sync": "h264_qsv",
        "amd amf": "h264_amf",
    }
    choice = label_aliases.get(choice, choice)
    if choice in {"cpu", "libx264", "x264"}:
        return "libx264"
    encoders = _available_ffmpeg_encoders()
    if choice == "auto":
        for encoder in ("h264_nvenc", "h264_qsv", "h264_amf", "h264_mf"):
            if encoder in encoders:
                _log(log, f"Video encoder: {encoder} (GPU/Hardware)")
                return encoder
        _log(log, "Video encoder: libx264 (CPU fallback)")
        return "libx264"
    if choice in encoders:
        _log(log, f"Video encoder: {choice}")
        return choice
    _log(log, f"Video encoder '{choice}' not available, falling back to libx264")
    return "libx264"


def _normalize_video_quality(value: str | None) -> str:
    quality = (value or "fast").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "draft": "draft_fast",
        "preview": "draft_fast",
        "preset_fast": "fast",
        "quality": "high",
        "high_quality": "high",
    }
    return aliases.get(quality, quality if quality in {"draft_fast", "fast", "balanced", "high"} else "fast")


def _encoder_args(encoder: str, quality: str = "fast") -> list[str]:
    quality = _normalize_video_quality(quality)
    if encoder == "h264_nvenc":
        settings = {
            "draft_fast": ("p1", "30"),
            "fast": ("p2", "26"),
            "balanced": ("p4", "23"),
            "high": ("p5", "19"),
        }[quality]
        return ["-c:v", "h264_nvenc", "-preset", settings[0], "-rc", "vbr", "-cq", settings[1], "-b:v", "0"]
    if encoder == "h264_qsv":
        settings = {
            "draft_fast": ("30", "veryfast"),
            "fast": ("26", "fast"),
            "balanced": ("23", "medium"),
            "high": ("19", "slow"),
        }[quality]
        return ["-c:v", "h264_qsv", "-global_quality", settings[0], "-preset", settings[1]]
    if encoder == "h264_amf":
        settings = {
            "draft_fast": ("speed", "30"),
            "fast": ("speed", "26"),
            "balanced": ("balanced", "23"),
            "high": ("quality", "19"),
        }[quality]
        return ["-c:v", "h264_amf", "-quality", settings[0], "-qp_i", settings[1], "-qp_p", settings[1]]
    if encoder == "h264_mf":
        bitrate = {"draft_fast": "4M", "fast": "6M", "balanced": "8M", "high": "12M"}[quality]
        return ["-c:v", "h264_mf", "-b:v", bitrate]
    settings = {
        "draft_fast": ("28", "veryfast"),
        "fast": ("23", "fast"),
        "balanced": ("20", "medium"),
        "high": ("18", "slow"),
    }[quality]
    return ["-c:v", "libx264", "-crf", settings[0], "-preset", settings[1]]


def _apply_video_encoder(args: list, encoder: str, quality: str = "fast") -> list[str]:
    normalized = [str(arg) for arg in args]
    out: list[str] = []
    i = 0
    while i < len(normalized):
        arg = normalized[i]
        next_arg = normalized[i + 1] if i + 1 < len(normalized) else ""
        if arg == "-c:v" and next_arg == "libx264":
            out.extend(_encoder_args(encoder, quality))
            i += 2
            continue
        if arg in {"-crf", "-preset"} and i + 1 < len(normalized):
            i += 2
            continue
        out.append(arg)
        i += 1
    return out


def _run_ffmpeg(args: list[str], cwd: Path | None = None, video_encoder: str = "libx264", video_quality: str = "fast") -> None:
    args = _apply_video_encoder(args, video_encoder, video_quality)
    result = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or "ffmpeg failed")[-800:])


def _filter_path(path: str | Path) -> str:
    text = str(path).replace("\\", "/")
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace(",", "\\,")
        .replace(" ", "\\ ")
    )


def _relative_filter_path(path: Path, cwd: Path | None) -> str:
    if cwd is not None and path.is_absolute():
        try:
            return _filter_path(Path(os.path.relpath(path, cwd)))
        except ValueError:
            pass
    return _filter_path(path)


def _stage_filter_fonts_dir(fonts_dir: Path | None, cwd: Path | None) -> Path | None:
    if not fonts_dir or not fonts_dir.exists():
        return None
    if cwd is None:
        return fonts_dir
    cwd.mkdir(parents=True, exist_ok=True)
    staged = cwd / "_autocapcut_fonts"
    try:
        if fonts_dir.resolve() == staged.resolve():
            return staged
    except OSError:
        pass
    staged.mkdir(parents=True, exist_ok=True)
    suffixes = {".ttf", ".otf", ".ttc"}
    for source in sorted(fonts_dir.rglob("*")):
        if not source.is_file() or source.suffix.lower() not in suffixes:
            continue
        target = staged / source.name
        if target.exists() and target.stat().st_size == source.stat().st_size:
            continue
        if target.exists():
            target = staged / f"{source.stem}_{abs(hash(str(source))) & 0xffff:x}{source.suffix}"
        shutil.copy2(source, target)
    return staged


def _ass_filter(ass_path: str | Path, fonts_dir: Path | None = None, cwd: Path | None = None) -> str:
    value = "ass=" + _relative_filter_path(Path(ass_path), cwd)
    staged_fonts = _stage_filter_fonts_dir(fonts_dir, cwd)
    if staged_fonts and staged_fonts.exists():
        value += ":fontsdir=" + _relative_filter_path(staged_fonts, cwd)
    return value


def _apply_ass_fontsdir(args: list[str], fonts_dir: Path | None, cwd: Path | None) -> list[str]:
    staged_fonts = _stage_filter_fonts_dir(fonts_dir, cwd)
    if not staged_fonts or not staged_fonts.exists():
        return args
    out: list[str] = []
    for arg in args:
        text = str(arg)
        if text.startswith("ass=") and "fontsdir=" not in text:
            text = text + ":fontsdir=" + _relative_filter_path(staged_fonts, cwd)
        out.append(text)
    return out


@contextmanager
def _patch_shorts_vertical_encoder(shorts_vertical, video_encoder: str, video_quality: str, font_dir: Path | None, cwd: Path | None):
    original_run = shorts_vertical._run
    original_subprocess_run = shorts_vertical.subprocess.run

    def patched_run(args):
        patched = _apply_video_encoder(args, video_encoder, video_quality)
        patched = _apply_ass_fontsdir(patched, font_dir, cwd)
        return original_run(patched)

    def patched_subprocess_run(args, *pargs, **kwargs):
        run_cwd = Path(str(kwargs.get("cwd"))) if kwargs.get("cwd") else cwd
        patched = _apply_video_encoder(args, video_encoder, video_quality)
        patched = _apply_ass_fontsdir(patched, font_dir, run_cwd)
        return original_subprocess_run(patched, *pargs, **kwargs)

    shorts_vertical._run = patched_run
    shorts_vertical.subprocess.run = patched_subprocess_run
    try:
        yield
    finally:
        shorts_vertical._run = original_run
        shorts_vertical.subprocess.run = original_subprocess_run


def _build_hook_clip(
    title: str,
    out: Path,
    duration: float,
    niche: str,
    badge_text: str,
    bg_path: Path | None,
    log: LogFn | None,
    video_encoder: str,
    video_quality: str,
) -> ClipSegment:
    from silent_vlog_maker import shorts_template

    hook_png = out.with_name(out.stem + "_hook.png")
    hook_mp4 = out.with_name(out.stem + "_hook.mp4")
    _log(log, f"Rendering hook card: {title}")
    old_badge = None
    if badge_text and niche in shorts_template.NETGAN_NICHE_PRESETS:
        old_badge = shorts_template.NETGAN_NICHE_PRESETS[niche].get("badge")
        shorts_template.NETGAN_NICHE_PRESETS[niche]["badge"] = badge_text
    try:
        shorts_template.render_hook_card(
            title=title,
            niche=niche,
            out_path=str(hook_png),
            bg_path=str(bg_path) if bg_path and bg_path.exists() else None,
        )
    finally:
        if old_badge is not None:
            shorts_template.NETGAN_NICHE_PRESETS[niche]["badge"] = old_badge
    _run_ffmpeg(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-loop",
            "1",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(hook_png),
            "-vf",
            "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,setsar=1,format=yuv420p",
            "-an",
            "-c:v",
            "libx264",
            "-crf",
            "19",
            "-preset",
            "medium",
            str(hook_mp4),
        ],
        video_encoder=video_encoder,
        video_quality=video_quality,
    )
    return ClipSegment(hook_mp4, 0.0, duration)


def _extract_hook_background_frame(job: ShortVideoJob, out: Path, log: LogFn | None) -> Path | None:
    if not job.clips:
        return None
    clip = job.clips[0]
    if not clip.path.exists():
        return None
    bg_path = out.with_name(out.stem + "_hook_bg.jpg")
    result = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-ss",
            f"{max(0.0, clip.start):.3f}",
            "-i",
            str(clip.path),
            "-frames:v",
            "1",
            str(bg_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0 or not bg_path.exists():
        _log(log, "Hook background frame failed; using template gradient")
        return None
    return bg_path


def _suggest_hook_text(niche: str, formula: str) -> str:
    try:
        from silent_vlog_maker import shorts_template

        suggestion = shorts_template.suggest_hook(niche=niche, formula=formula)
        return str(suggestion.get("example") or "")
    except Exception:
        return ""


def _ass_header_without_shadow(header: str) -> str:
    return _ass_header_with_template(header, {"caption": {"shadow": 0}})


def _ass_header_with_template(header: str, template_config: dict[str, Any] | None) -> str:
    caption = template_config.get("caption", {}) if isinstance(template_config, dict) else {}
    font = str(caption.get("font") or "Noto Sans TC")
    main_size = int(caption.get("main_size", 124) or 124)
    addr_size = int(caption.get("addr_size", 58) or 58)
    outline = int(caption.get("outline", 10) or 0)
    shadow = int(caption.get("shadow", 0) or 0)
    lines: list[str] = []
    for line in header.splitlines():
        if line.startswith("Style: "):
            parts = line.split(",")
            if len(parts) > 16:
                name = parts[0].replace("Style:", "").strip().upper()
                parts[1] = font
                parts[2] = str(addr_size if name == "ADDR" else main_size)
                parts[15] = str(max(0, outline))
                parts[16] = str(max(0, shadow))
                line = ",".join(parts)
        lines.append(line)
    return "\n".join(lines)


def _with_caption_style(shorts_vertical, job: ShortVideoJob, callback) -> None:
    old_main_pos = shorts_vertical._MAIN_POS
    old_addr_pos = shorts_vertical._ADDR_POS
    old_header = shorts_vertical._HEADER
    shorts_vertical._MAIN_POS = rf"{{\an5\pos(540,{job.main_caption_y})}}"
    shorts_vertical._ADDR_POS = rf"{{\an5\pos(540,{job.addr_caption_y})}}"
    shorts_vertical._HEADER = _ass_header_with_template(shorts_vertical._HEADER, job.template_config)
    try:
        callback()
    finally:
        shorts_vertical._MAIN_POS = old_main_pos
        shorts_vertical._ADDR_POS = old_addr_pos
        shorts_vertical._HEADER = old_header


def _render_with_shorts_vertical(job: ShortVideoJob, log: LogFn | None, video_encoder: str, video_quality: str) -> Path:
    if job.bgm is None:
        return _render_food_travel_xfade(job, log, video_encoder, video_quality)

    import silent_vlog_maker.shorts_vertical as shorts_vertical

    segs, caps = job_to_vendor_args(job)

    def render() -> None:
        shorts_vertical.build_one_short(
            segs=segs,
            caps=caps,
            bgm=str(job.bgm),
            out=str(job.output),
            vol=job.volume,
            fade=job.fade,
            bgm_start=job.bgm_start,
        )

    with _patch_shorts_vertical_encoder(shorts_vertical, video_encoder, video_quality, job.font_dir, job.output.parent):
        _with_caption_style(shorts_vertical, job, render)
    _apply_copyright_overlay(job.output, job, log, video_encoder, video_quality)
    return job.output


def _copyright_config(job: ShortVideoJob) -> dict[str, Any]:
    config = job.template_config if isinstance(job.template_config, dict) else {}
    copyright_config = config.get("copyright", {}) if isinstance(config.get("copyright", {}), dict) else {}
    if not bool(copyright_config.get("enabled", False)):
        return {}
    account = str(copyright_config.get("account", "") or "").strip()
    if not account:
        return {}
    return {
        "platform": str(copyright_config.get("platform", "instagram") or "instagram").lower(),
        "account": account,
        "position": str(copyright_config.get("position", "bottom_right") or "bottom_right"),
        "scale": max(0.4, min(2.5, float(copyright_config.get("scale", 1.0) or 1.0))),
        "y_offset": max(-300, min(300, int(copyright_config.get("y_offset", 40) or 0))),
        "opacity": max(0.05, min(1.0, float(copyright_config.get("opacity", 0.85) or 0.85))),
        "logos": copyright_config.get("logos", {}) if isinstance(copyright_config.get("logos", {}), dict) else {},
    }


def _copyright_logo_path(config: dict[str, Any]) -> Path | None:
    platform = str(config.get("platform", "instagram") or "instagram").lower()
    logos = config.get("logos", {}) if isinstance(config.get("logos", {}), dict) else {}
    candidate = logos.get(platform)
    if candidate:
        path = Path(str(candidate)).expanduser()
        if path.exists():
            return path
    default_name = "threads-logo.png" if platform == "threads" else "ig-logo.png"
    path = Path(__file__).resolve().parents[1] / default_name
    return path if path.exists() else None


def _load_overlay_font(size: int):
    from PIL import ImageFont

    for name in ("arial.ttf", "seguisb.ttf", "segoeuib.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _render_copyright_overlay(job: ShortVideoJob, out: Path, log: LogFn | None) -> Path | None:
    config = _copyright_config(job)
    if not config:
        return None
    logo_path = _copyright_logo_path(config)
    if logo_path is None:
        _log(log, "Copyright logo not found; skipping overlay.")
        return None

    from PIL import Image, ImageDraw

    overlay = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    logo = Image.open(logo_path).convert("RGBA")
    scale = float(config["scale"])
    opacity = float(config["opacity"])
    logo_px = max(36, int(72 * scale))
    logo.thumbnail((logo_px, logo_px), Image.LANCZOS)
    font = _load_overlay_font(max(18, int(48 * scale)))
    account = str(config["account"])
    draw = ImageDraw.Draw(overlay)
    bbox = draw.textbbox((0, 0), account, font=font, stroke_width=max(1, int(3 * scale)))
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    gap = int(14 * scale)
    margin = int(58 * scale)
    block_w = logo.width + gap + text_w
    block_h = max(logo.height, text_h)
    position = str(config["position"])
    if position == "bottom_left":
        x = margin
        y = 1920 - margin - block_h
    elif position == "top_right":
        x = 1080 - margin - block_w
        y = margin
    elif position == "top_left":
        x = margin
        y = margin
    elif position == "bottom_center":
        x = (1080 - block_w) // 2
        y = 1920 - margin - block_h
    else:
        x = 1080 - margin - block_w
        y = 1920 - margin - block_h
    y += int(config.get("y_offset", 40) or 0)
    x = max(0, min(1080 - block_w, x))
    y = max(0, min(1920 - block_h, y))

    if opacity < 1.0:
        alpha = logo.getchannel("A").point(lambda value: int(value * opacity))
        logo.putalpha(alpha)
    overlay.alpha_composite(logo, (int(x), int(y + (block_h - logo.height) / 2)))
    text_layer = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    text_draw = ImageDraw.Draw(text_layer)
    tx = x + logo.width + gap
    ty = y + (block_h - text_h) / 2 - bbox[1]
    text_draw.text(
        (tx, ty),
        account,
        font=font,
        fill=(255, 255, 255, int(255 * opacity)),
        stroke_width=max(1, int(4 * scale)),
        stroke_fill=(0, 0, 0, int(180 * opacity)),
    )
    overlay.alpha_composite(text_layer)
    out.parent.mkdir(parents=True, exist_ok=True)
    overlay.save(out)
    return out


def _apply_copyright_overlay(
    video: Path,
    job: ShortVideoJob,
    log: LogFn | None,
    video_encoder: str,
    video_quality: str,
) -> Path:
    config = _copyright_config(job)
    if not config:
        return video
    overlay = video.with_name(video.stem + "_copyright.png")
    rendered = _render_copyright_overlay(job, overlay, log)
    if rendered is None:
        return video
    temp_video = video.with_name(video.stem + "_copyright_tmp.mp4")
    _log(log, f"Applying copyright overlay: {config['platform']} {config['account']}")
    _run_ffmpeg(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(video),
            "-i",
            str(rendered),
            "-filter_complex",
            "[0:v][1:v]overlay=0:0:format=auto[v]",
            "-map",
            "[v]",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-crf",
            "19",
            "-preset",
            "medium",
            "-c:a",
            "copy",
            "-pix_fmt",
            "yuv420p",
            str(temp_video),
        ],
        video_encoder=video_encoder,
        video_quality=video_quality,
    )
    temp_video.replace(video)
    return video


def _clip_has_audio(path: Path) -> bool:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def _build_source_audio_track(
    job: ShortVideoJob,
    audio_out: Path,
    xfade_duration: float,
    log: LogFn | None,
) -> Path:
    _log(log, "No BGM selected; using source clip audio.")
    cmd = ["ffmpeg", "-v", "error", "-y"]
    chains: list[str] = []
    labels: list[str] = []
    for index, clip in enumerate(job.clips):
        duration = max(0.05, float(clip.duration))
        label = f"a{index}"
        labels.append(f"[{label}]")
        if _clip_has_audio(clip.path):
            cmd += ["-i", str(clip.path)]
            chains.append(
                f"[{index}:a]atrim=start={max(0.0, clip.start):.3f}:duration={duration:.3f},"
                f"asetpts=PTS-STARTPTS,aresample=48000[{label}]"
            )
        else:
            cmd += ["-f", "lavfi", "-t", f"{duration:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
            chains.append(f"[{index}:a]asetpts=PTS-STARTPTS,aresample=48000[{label}]")

    if len(labels) == 1:
        filter_complex = ";".join(chains) + f";{labels[0]}anull[aout]"
    elif xfade_duration > 0:
        prev = labels[0]
        for index, label in enumerate(labels[1:], start=1):
            out_label = "[aout]" if index == len(labels) - 1 else f"[ax{index}]"
            chains.append(f"{prev}{label}acrossfade=d={xfade_duration:.3f}:c1=tri:c2=tri{out_label}")
            prev = out_label
        filter_complex = ";".join(chains)
    else:
        filter_complex = ";".join(chains) + ";" + "".join(labels) + f"concat=n={len(labels)}:v=0:a=1[aout]"

    _run_ffmpeg(
        cmd
        + [
            "-filter_complex",
            filter_complex,
            "-map",
            "[aout]",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(audio_out),
        ]
    )
    return audio_out


def _mux_captioned_video(
    cap: Path,
    job: ShortVideoJob,
    total: float,
    xfade_duration: float,
    log: LogFn | None,
) -> None:
    import silent_vlog_maker.shorts_vertical as shorts_vertical

    if job.bgm is not None:
        start = shorts_vertical.find_music_highlight(str(job.bgm), total) if job.bgm_start == "auto" else float(job.bgm_start)
        fade_out_start = max(0.3, total - job.fade)
        comp = "acompressor=threshold=-24dB:ratio=4:attack=15:release=200:makeup=3"
        _run_ffmpeg(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(cap),
                "-ss",
                f"{start:.2f}",
                "-stream_loop",
                "-1",
                "-i",
                str(job.bgm),
                "-filter_complex",
                f"[1:a]{comp},volume={job.volume},afade=t=in:st=0:d=0.3,afade=t=out:st={max(0.3, fade_out_start):.2f}:d={job.fade}[a]",
                "-map",
                "0:v:0",
                "-map",
                "[a]",
                "-t",
                f"{total:.3f}",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                str(job.output),
            ]
        )
        return

    audio = cap.with_name(cap.stem + "_source_audio.m4a")
    _build_source_audio_track(job, audio, xfade_duration, log)
    _run_ffmpeg(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(cap),
            "-i",
            str(audio),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-t",
            f"{total:.3f}",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(job.output),
        ]
    )


def _job_variant(
    job: ShortVideoJob,
    *,
    clips: list[ClipSegment] | None = None,
    captions: list[CaptionBlock] | None = None,
    output: Path | None = None,
) -> ShortVideoJob:
    return ShortVideoJob(
        clips=clips if clips is not None else job.clips,
        captions=captions if captions is not None else job.captions,
        bgm=job.bgm,
        output=output if output is not None else job.output,
        volume=job.volume,
        fade=job.fade,
        bgm_start=job.bgm_start,
        main_caption_y=job.main_caption_y,
        addr_caption_y=job.addr_caption_y,
        video_template=job.video_template,
        template_config=job.template_config,
        video_encoder=job.video_encoder,
        video_quality=job.video_quality,
        font_dir=job.font_dir,
    )


def _cleanup_legacy_intermediates(output: Path) -> None:
    base = output.with_suffix("")
    for path in (
        base.with_name(base.name + "_hook.mp4"),
        base.with_name(base.name + "_hook.png"),
        base.with_name(base.name + "_vis.mp4"),
        base.with_name(base.name + "_cap.mp4"),
        base.with_name(base.name + "_cap_source_audio.m4a"),
        base.with_name(base.name + "_cap_copyright.png"),
        base.with_name(base.name + "_cap_copyright_tmp.mp4"),
        base.with_name(base.name + "_copyright.png"),
        base.with_name(base.name + "_copyright_tmp.mp4"),
        base.with_suffix(".ass"),
    ):
        try:
            if path.exists() and path.resolve().parent == output.resolve().parent:
                path.unlink()
        except OSError:
            pass


def _clip_video_filter(shorts_vertical, clip: ClipSegment, duration: float) -> str:
    motion = (clip.motion or "none").strip().lower()
    if motion != "none":
        return export_motion_filter(motion, duration)
    return shorts_vertical._NORMV


def _clip_grade_name(clip: ClipSegment, project_grade: str) -> str:
    grade = (clip.grade or "inherit").strip().lower()
    if grade == "inherit":
        return project_grade
    return grade


def _job_has_clip_effects(job: ShortVideoJob) -> bool:
    for clip in job.clips:
        if (clip.motion or "none").strip().lower() != "none":
            return True
        if (clip.grade or "inherit").strip().lower() != "inherit":
            return True
        if (clip.transition or "inherit").strip().lower() != "inherit":
            return True
    return False


def _adjust_caps_for_xfade(
    caps: list[tuple[float, float, list[tuple[str, str]], str]],
    original_durations: list[float],
    xfade_duration: float,
) -> list[tuple[float, float, list[tuple[str, str]], str]]:
    boundaries: list[float] = []
    cursor = 0.0
    for duration in original_durations[:-1]:
        cursor += duration
        boundaries.append(cursor)

    adjusted = []
    for start, end, segments, kind in caps:
        offset_start = sum(xfade_duration for boundary in boundaries if start >= boundary)
        offset_end = sum(xfade_duration for boundary in boundaries if end >= boundary)
        new_start = max(0.0, start - offset_start)
        new_end = max(new_start + 0.05, end - offset_end)
        adjusted.append((new_start, new_end, segments, kind))
    return adjusted


def _render_food_travel_xfade(job: ShortVideoJob, log: LogFn | None, video_encoder: str, video_quality: str) -> Path:
    import silent_vlog_maker.effects as effects
    import silent_vlog_maker.shorts_vertical as shorts_vertical

    config = normalize_template_config(job.video_template, job.template_config)
    video_config = config.get("video", {}) if isinstance(config, dict) else {}
    grade_name = str(video_config.get("grade", "none"))
    grade_enabled = is_video_grade_enabled(grade_name)
    transition = str(video_config.get("transition", "hard_cut")).strip().lower()
    configured_xfade_duration = max(0.1, min(3.0, float(video_config.get("transition_duration", 0.8) or 0.8)))

    segs, caps = job_to_vendor_args(job)
    original_caps = caps
    durations = [duration for _, _, duration in segs]
    clip_transition_enabled = any((clip.transition or "inherit").strip().lower() == "xfade" for clip in job.clips)
    usable_xfade = len(segs) > 1 and all(duration > configured_xfade_duration + 0.2 for duration in durations)
    xfade_duration = configured_xfade_duration if usable_xfade and (transition == "xfade" or clip_transition_enabled) else 0.0
    if xfade_duration:
        caps = _adjust_caps_for_xfade(caps, durations, xfade_duration)
    total = sum(durations) - max(0, len(durations) - 1) * xfade_duration
    base = job.output.with_suffix("")
    vis = base.with_name(base.name + "_vis.mp4")
    cap = base.with_name(base.name + "_cap.mp4")
    ass = base.with_suffix(".ass")

    _log(
        log,
        f"Template video: {'cinematic grade' if grade_enabled else 'no grade'} + "
        f"{f'xfade {xfade_duration:.2f}s' if xfade_duration else 'hard cuts'}",
    )
    effect_lines = []
    for index, clip in enumerate(job.clips, start=1):
        motion = (clip.motion or "none").strip().lower()
        grade = (clip.grade or "inherit").strip().lower()
        transition_value = (clip.transition or "inherit").strip().lower()
        if motion != "none" or grade != "inherit" or transition_value != "inherit":
            effect_lines.append(f"#{index}: motion={motion}, grade={grade}, transition={transition_value}")
    if effect_lines:
        _log(log, "Clip effects: " + " | ".join(effect_lines))

    cmd = ["ffmpeg", "-v", "error", "-y"]
    for path, start, duration in segs:
        cmd += ["-ss", str(start), "-t", str(duration), "-i", path]

    def build_visual_filter(use_xfade: bool, use_motion: bool) -> tuple[str, str]:
        chains: list[str] = []
        labels = ""
        for index, ((_path, _start, duration), clip) in enumerate(zip(segs, job.clips)):
            base_filter = _clip_video_filter(shorts_vertical, clip, duration) if use_motion else shorts_vertical._NORMV
            chain = f"[{index}:v]{base_filter},settb=AVTB,trim=duration={duration},setpts=PTS-STARTPTS"
            clip_grade = video_grade_filter(_clip_grade_name(clip, grade_name))
            if clip_grade:
                chain += f",{clip_grade}"
            chain += f",fps=30,settb=AVTB,format=yuv420p[v{index}]"
            chains.append(chain)
            labels += f"[v{index}]"
        if use_xfade and xfade_duration:
            chains.append(effects.build_xfade_concat(len(segs), durations, xfade_duration=xfade_duration, transition="fade"))
        else:
            chains.append(labels + f"concat=n={len(segs)}:v=1:a=0[outv]")
        return ";".join(chains), "[outv]"

    def render_visual(use_xfade: bool, use_motion: bool) -> None:
        filter_complex, map_label = build_visual_filter(use_xfade, use_motion)
        _run_ffmpeg(
            cmd
            + [
                "-filter_complex",
                filter_complex,
                "-map",
                map_label,
                "-an",
                "-c:v",
                "libx264",
                "-crf",
                "19",
                "-preset",
                "medium",
                "-pix_fmt",
                "yuv420p",
                "-r",
                "30",
                str(vis),
            ],
            video_encoder=video_encoder,
            video_quality=video_quality,
        )

    try:
        render_visual(use_xfade=bool(xfade_duration), use_motion=True)
    except RuntimeError as exc:
        message = str(exc)
        if "Cannot allocate memory" not in message:
            raise
        if xfade_duration:
            _log(log, "ffmpeg ran out of memory with XFade; retrying with hard cuts.")
            caps = original_caps
            total = sum(durations)
            xfade_duration = 0.0
            try:
                render_visual(use_xfade=False, use_motion=True)
            except RuntimeError as second_exc:
                if "Cannot allocate memory" not in str(second_exc):
                    raise
                _log(log, "ffmpeg still ran out of memory; retrying without clip motion effects.")
                render_visual(use_xfade=False, use_motion=False)
        else:
            _log(log, "ffmpeg ran out of memory; retrying without clip motion effects.")
            render_visual(use_xfade=False, use_motion=False)

    def render_captions() -> None:
        shorts_vertical.build_multicolor_ass(caps, str(ass))

    _with_caption_style(shorts_vertical, job, render_captions)

    workdir = job.output.parent
    _run_ffmpeg(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            vis.name,
            "-vf",
            _ass_filter(ass.name, job.font_dir, workdir),
            "-c:v",
            "libx264",
            "-crf",
            "19",
            "-preset",
            "medium",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "30",
            cap.name,
        ],
        cwd=workdir,
        video_encoder=video_encoder,
        video_quality=video_quality,
    )

    _apply_copyright_overlay(cap, job, log, video_encoder, video_quality)
    _mux_captioned_video(cap, job, total, xfade_duration, log)
    return job.output


def validate_job(job: ShortVideoJob) -> None:
    require_vendor()
    missing = [str(c.path) for c in job.clips if not c.path.exists()]
    if missing:
        raise FileNotFoundError("Missing clip file(s): " + ", ".join(missing))
    if job.bgm is not None and not job.bgm.exists():
        raise FileNotFoundError(f"Missing BGM file: {job.bgm}")
    job.output.parent.mkdir(parents=True, exist_ok=True)


def run_short_video_job(job: ShortVideoJob, log: LogFn | None = None) -> Path:
    """Render a vertical short using the vendored video-autopilot-kit engine."""
    validate_job(job)
    ensure_vendor_on_path()

    segs, caps = job_to_vendor_args(job)
    total = sum(duration for _, _, duration in segs)
    _log(log, f"Using {len(segs)} clip segment(s), total {total:.2f}s")
    _log(log, f"Using {len(caps)} caption block(s)")
    _log(log, f"BGM: {job.bgm if job.bgm is not None else '(source audio)'}")
    _log(log, f"Output: {job.output}")
    _log(log, f"Caption Y: main={job.main_caption_y}, addr={job.addr_caption_y}")
    _log(log, f"Template: {job.video_template}")
    video_encoder = _resolve_video_encoder(job.video_encoder, log)
    video_quality = _normalize_video_quality(job.video_quality)
    _log(log, f"Output quality: {video_quality}")
    _log(log, "Rendering with video-autopilot-kit...")

    template = job.video_template.strip().lower()
    config = normalize_template_config(job.video_template, job.template_config)
    intro = config.get("intro", {}) if isinstance(config, dict) else {}
    caption = config.get("caption", {}) if isinstance(config, dict) else {}
    video = config.get("video", {}) if isinstance(config, dict) else {}
    intro_enabled = bool(intro.get("enabled", False))
    intro_type = str(intro.get("type", "none")).strip().lower()
    intro_duration = max(0.0, float(intro.get("duration", 0.0) or 0.0))
    intro_niche = str(intro.get("niche", "teaching") or "teaching")
    intro_formula = str(intro.get("formula", "contrarian") or "contrarian")
    intro_hook_text = str(intro.get("hook_text", "") or "")
    intro_badge_text = str(intro.get("badge_text", "") or "")
    caption_strategy = str(caption.get("color_strategy", "manual"))
    video_grade = str(video.get("grade", "none")).strip().lower()
    video_transition = str(video.get("transition", "hard_cut")).strip().lower()
    with tempfile.TemporaryDirectory(prefix=f".{job.output.stem}_", dir=str(job.output.parent)) as temp_dir:
        render_output = Path(temp_dir) / job.output.name
        work_job = _job_variant(job, output=render_output)

        if intro_enabled and intro_type == "hook_card" and intro_duration > 0:
            hook_duration = intro_duration
            hook_title = intro_hook_text or _suggest_hook_text(intro_niche, intro_formula) or _caption_title(job)
            hook_bg = _extract_hook_background_frame(job, render_output, log)
            hook = _build_hook_clip(
                hook_title,
                render_output,
                hook_duration,
                intro_niche,
                intro_badge_text,
                hook_bg,
                log,
                video_encoder,
                video_quality,
            )
            styled = _shift_captions(_apply_caption_strategy(job.captions, caption_strategy), hook_duration)
            render_job = _job_variant(
                work_job,
                clips=[hook, *job.clips],
                captions=styled,
            )
            _render_with_shorts_vertical(render_job, log, video_encoder, video_quality)
        elif is_video_grade_enabled(video_grade) or video_transition == "xfade" or template == "food/travel short" or _job_has_clip_effects(job):
            render_job = _job_variant(work_job, captions=_apply_caption_strategy(job.captions, caption_strategy))
            _render_food_travel_xfade(render_job, log, video_encoder, video_quality)
        else:
            render_job = _job_variant(work_job, captions=_apply_caption_strategy(job.captions, caption_strategy))
            _render_with_shorts_vertical(render_job, log, video_encoder, video_quality)

        if job.output.exists():
            job.output.unlink()
        render_output.replace(job.output)

    _cleanup_legacy_intermediates(job.output)

    _log(log, "Render complete")
    return job.output


def run_from_config(path: Path, log: LogFn | None = print) -> Path:
    data = json.loads(path.read_text(encoding="utf-8"))
    return run_short_video_job(parse_job(data), log=log)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render a vertical short from a job JSON file.")
    parser.add_argument("--config", required=True, help="Path to short-video job JSON.")
    args = parser.parse_args(argv)
    out = run_from_config(Path(args.config))
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
