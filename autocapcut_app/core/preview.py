from __future__ import annotations

import hashlib
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from autocapcut_app.core.template_config import normalize_template_config
from autocapcut_app.core.motion import preview_motion_filter
from autocapcut_app.core.video_grade import video_grade_filter
from autocapcut_app.paths import ensure_vendor_on_path


def preview_cache_dir() -> Path:
    path = Path.home() / ".autocapcut" / "preview_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def cached_preview_path(video_path: Path, timestamp: float) -> Path:
    raw = f"{video_path.resolve()}::{timestamp:.3f}".encode("utf-8", "replace")
    digest = hashlib.sha1(raw).hexdigest()[:16]
    return preview_cache_dir() / f"{digest}.jpg"


def cached_ass_preview_path(
    video_path: Path,
    timestamp: float,
    segments: list[list[str]],
    kind: str,
    main_y: int,
    addr_y: int,
    template_config: dict[str, Any] | None = None,
) -> Path:
    raw = (
        f"{video_path.resolve()}::{timestamp:.3f}::{segments!r}::{kind}::{main_y}::{addr_y}::{template_config!r}"
    ).encode("utf-8", "replace")
    digest = hashlib.sha1(raw).hexdigest()[:16]
    return preview_cache_dir() / f"{digest}.png"


def cached_hook_preview_path(title: str, niche: str, badge_text: str = "", bg_path: Path | None = None) -> Path:
    bg_key = ""
    if bg_path and bg_path.exists():
        try:
            bg_key = f"{bg_path.resolve()}::{bg_path.stat().st_mtime_ns}"
        except OSError:
            bg_key = str(bg_path)
    raw = f"hook::{title}::{niche}::{badge_text}::{bg_key}".encode("utf-8", "replace")
    digest = hashlib.sha1(raw).hexdigest()[:16]
    return preview_cache_dir() / f"{digest}.png"


def extract_preview_frame(video_path: Path, timestamp: float = 0.0, force: bool = False) -> Path:
    """Extract a preview frame and return the cached JPG path.

    PyAV is used first because it avoids spawning ffmpeg for every scrub. ffmpeg
    remains as a fallback for formats PyAV cannot decode in the current env.
    """
    if not video_path.exists():
        raise FileNotFoundError(f"Clip not found: {video_path}")
    out = cached_preview_path(video_path, timestamp)
    if out.exists() and not force:
        return out

    try:
        return _extract_with_pyav(video_path, timestamp, out)
    except Exception:
        return _extract_with_ffmpeg(video_path, timestamp, out)


def render_ass_preview_frame(
    video_path: Path,
    timestamp: float = 0.0,
    *,
    segments: list[list[str]] | None = None,
    kind: str = "main",
    main_y: int = 1180,
    addr_y: int = 1390,
    video_template: str = "Basic Subtitle",
    template_config: dict[str, Any] | None = None,
    motion: str = "none",
    clip_progress: float = 0.0,
    font_dir: Path | None = None,
    force: bool = False,
) -> Path:
    """Render one preview frame through the same libass path used for export."""
    if not video_path.exists():
        raise FileNotFoundError(f"Clip not found: {video_path}")

    normalized_segments = _normalize_ass_segments(segments or [])
    template_key = video_template or "Basic Subtitle"
    config = normalize_template_config(template_key, template_config)
    out = cached_ass_preview_path(
        video_path,
        timestamp,
        normalized_segments,
        kind,
        main_y,
        addr_y,
        {
            "config": config,
            "motion": motion,
            "progress": round(clip_progress, 3),
            "font_dir": str(font_dir or ""),
            "font_stage": 2,
        },
    )
    if template_key:
        out = out.with_name(out.stem + "_" + hashlib.sha1(template_key.encode("utf-8", "replace")).hexdigest()[:6] + out.suffix)
    if out.exists() and not force:
        return out

    ensure_vendor_on_path()
    import silent_vlog_maker.shorts_vertical as shorts_vertical
    from autocapcut_app.workflows.short_video import _ass_filter

    with tempfile.TemporaryDirectory(prefix="autocapcut_preview_") as tmp:
        tmp_dir = Path(tmp)
        ass_path = tmp_dir / "preview.ass"
        png_path = tmp_dir / "preview.png"

        old_main_pos = shorts_vertical._MAIN_POS
        old_addr_pos = shorts_vertical._ADDR_POS
        old_header = shorts_vertical._HEADER
        try:
            shorts_vertical._MAIN_POS = rf"{{\an5\pos(540,{main_y})}}"
            shorts_vertical._ADDR_POS = rf"{{\an5\pos(540,{addr_y})}}"
            shorts_vertical._HEADER = _ass_header_with_template(shorts_vertical._HEADER, config)
            blocks = [(0.0, 10.0, normalized_segments, kind)] if normalized_segments else []
            shorts_vertical.build_multicolor_ass(blocks, str(ass_path))
        finally:
            shorts_vertical._MAIN_POS = old_main_pos
            shorts_vertical._ADDR_POS = old_addr_pos
            shorts_vertical._HEADER = old_header

        vf = preview_motion_filter(motion, clip_progress)
        grade = video_grade_filter(str(config.get("video", {}).get("grade", "none")))
        if grade:
            vf += f",{grade}"
        if normalized_segments:
            vf += "," + _ass_filter("preview.ass", font_dir, tmp_dir)
        cmd = [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-ss",
            f"{max(0.0, timestamp):.3f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-vf",
            vf,
            str(png_path),
        ]
        result = subprocess.run(
            cmd,
            cwd=tmp_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0 or not png_path.exists():
            raise RuntimeError("Failed to render ASS preview frame: " + (result.stderr or "")[-500:])
        out.write_bytes(png_path.read_bytes())
    return out


def render_hook_preview_frame(
    title: str,
    niche: str = "teaching",
    badge_text: str = "",
    bg_path: Path | None = None,
    force: bool = False,
) -> Path:
    """Render the same hook card image used by No-face/Teaching exports."""
    safe_title = (title or "今天的重點").strip()[:28] or "今天的重點"
    safe_niche = niche or "teaching"
    safe_badge = (badge_text or "").strip()
    out = cached_hook_preview_path(safe_title, safe_niche, safe_badge, bg_path)
    if out.exists() and not force:
        return out

    ensure_vendor_on_path()
    import silent_vlog_maker.shorts_template as shorts_template

    with tempfile.TemporaryDirectory(prefix="autocapcut_hook_preview_") as tmp:
        png_path = Path(tmp) / "hook.png"
        old_badge = None
        if safe_badge and safe_niche in shorts_template.NETGAN_NICHE_PRESETS:
            old_badge = shorts_template.NETGAN_NICHE_PRESETS[safe_niche].get("badge")
            shorts_template.NETGAN_NICHE_PRESETS[safe_niche]["badge"] = safe_badge
        try:
            shorts_template.render_hook_card(
                title=safe_title,
                niche=safe_niche,
                out_path=str(png_path),
                bg_path=str(bg_path) if bg_path and bg_path.exists() else None,
            )
        finally:
            if old_badge is not None:
                shorts_template.NETGAN_NICHE_PRESETS[safe_niche]["badge"] = old_badge
        if not png_path.exists():
            raise RuntimeError("Failed to render hook preview frame")
        out.write_bytes(png_path.read_bytes())
    return out


def _normalize_ass_segments(segments: list[list[str]]) -> list[list[str]]:
    normalized: list[list[str]] = []
    for segment in segments:
        if not segment:
            continue
        text = str(segment[0])
        color = str(segment[1]) if len(segment) > 1 else "w"
        if text:
            normalized.append([text, color])
    return normalized


def _ass_header_without_shadow(header: str) -> str:
    return _ass_header_with_template(header, {"caption": {"shadow": 0}})


def _ass_header_with_template(header: str, template_config: dict[str, Any]) -> str:
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
                parts[14] = "1"
                parts[15] = str(max(0, outline))
                parts[16] = str(max(0, shadow))
                line = ",".join(parts)
        lines.append(line)
    return "\n".join(lines)


def _extract_with_pyav(video_path: Path, timestamp: float, out: Path) -> Path:
    import av

    with av.open(str(video_path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        seek_target = int(max(0.0, timestamp) / float(stream.time_base))
        container.seek(seek_target, any_frame=False, backward=True, stream=stream)
        best = None
        for frame in container.decode(stream):
            pts = float(frame.pts * stream.time_base) if frame.pts is not None else 0.0
            best = frame
            if pts >= timestamp:
                break
        if best is None:
            raise RuntimeError("No video frame decoded")
        best.to_image().save(out, quality=92)
    return out


def _extract_with_ffmpeg(video_path: Path, timestamp: float, out: Path) -> Path:
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-y",
        "-ss",
        f"{max(0.0, timestamp):.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(out),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0 or not out.exists():
        raise RuntimeError("Failed to extract preview frame: " + (result.stderr or "")[-500:])
    return out
