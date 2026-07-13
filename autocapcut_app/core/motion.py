from __future__ import annotations


def normalize_motion(value: str | None) -> str:
    return (value or "none").strip().lower().replace("-", "_").replace(" ", "_")


def export_motion_filter(motion: str | None, duration: float) -> str:
    normalized = normalize_motion(motion)
    frames = max(1, int(max(0.1, duration) * 30))
    base = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
    if normalized in {"ken_burns_zoom_in", "zoom_in", "kenburns"}:
        return (
            f"{base},"
            f"zoompan=z='min(1+on*0.25/{frames},1.25)':"
            "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            "d=1:s=1080x1920:fps=30,setsar=1,format=yuv420p"
        )
    if normalized in {"ken_burns_pan_right", "pan_right"}:
        return (
            f"{base},"
            f"zoompan=z='1.14':x='(iw-iw/zoom)*on/{frames}':"
            "y='ih/2-(ih/zoom/2)':d=1:s=1080x1920:fps=30,setsar=1,format=yuv420p"
        )
    if normalized in {"ken_burns_static", "static"}:
        return f"{base},setsar=1,fps=30,format=yuv420p"
    return f"{base},setsar=1,fps=30,format=yuv420p"


def preview_motion_filter(motion: str | None, progress: float) -> str:
    normalized = normalize_motion(motion)
    progress = max(0.0, min(1.0, progress))
    base = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
    if normalized in {"ken_burns_zoom_in", "zoom_in", "kenburns"}:
        zoom = 1.0 + 0.25 * progress
        width = int(1080 * zoom)
        height = int(1920 * zoom)
        return f"{base},scale={width}:{height},crop=1080:1920"
    if normalized in {"ken_burns_pan_right", "pan_right"}:
        width = int(1080 * 1.14)
        height = int(1920 * 1.14)
        max_x = max(0, width - 1080)
        x = int(max_x * progress)
        return f"{base},scale={width}:{height},crop=1080:1920:{x}:(ih-oh)/2"
    return base
