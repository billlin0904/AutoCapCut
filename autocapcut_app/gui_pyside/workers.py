from __future__ import annotations

import time
import subprocess
import tempfile
import traceback
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtGui import QImage

from autocapcut_app.core.template_config import normalize_template_config
from autocapcut_app.core.video_grade import video_grade_filter
from autocapcut_app.paths import ensure_vendor_on_path
from autocapcut_app.core.preview import extract_preview_frame, render_ass_preview_frame
from autocapcut_app.workflows.short_video import _ass_filter, parse_job, run_short_video_job


class ShortVideoWorker(QObject):
    log = Signal(str)
    progress = Signal(int, str)
    finished = Signal(str)
    failed = Signal(str)

    def __init__(self, job_data: dict | list[dict]):
        super().__init__()
        self.job_data = job_data

    @Slot()
    def run(self) -> None:
        try:
            jobs_data = self.job_data if isinstance(self.job_data, list) else [self.job_data]
            outputs: list[str] = []
            total = len(jobs_data)
            self.progress.emit(0, "Preparing render")
            for index, item in enumerate(jobs_data, start=1):
                base = int(((index - 1) / total) * 100)
                span = 100 / total

                def emit_progress(fraction: float, label: str) -> None:
                    value = int(round(base + span * max(0.0, min(1.0, fraction))))
                    if index < total:
                        value = min(value, int(round((index / total) * 100)) - 1)
                    else:
                        value = min(value, 99)
                    self.progress.emit(max(0, min(99, value)), label)

                def log_with_progress(message: str) -> None:
                    self.log.emit(message)
                    lower = message.lower()
                    if lower.startswith("using ") or lower.startswith("bgm:") or lower.startswith("output:"):
                        emit_progress(0.08, "Preparing assets")
                    elif lower.startswith("video encoder:"):
                        emit_progress(0.14, "Selecting encoder")
                    elif "rendering hook card" in lower:
                        emit_progress(0.22, "Rendering hook card")
                    elif "rendering with video-autopilot-kit" in lower:
                        emit_progress(0.28, "Rendering video")
                    elif lower.startswith("template video:"):
                        emit_progress(0.45, "Applying template effects")
                    elif "render complete" in lower:
                        emit_progress(0.95, "Finalizing output")

                if total > 1:
                    self.log.emit(f"Batch {index}/{total}: {item.get('title') or item.get('output')}")
                emit_progress(0.02, f"Preparing job {index}/{total}")
                job = parse_job(item)
                out: Path = run_short_video_job(job, log=log_with_progress)
                outputs.append(str(out))
                done_value = int(round((index / total) * 100))
                self.progress.emit(min(100, done_value), f"Finished job {index}/{total}")
            self.progress.emit(100, "Render complete")
            self.finished.emit("\n".join(outputs))
        except Exception as exc:
            self.failed.emit(f"{exc}\n\n{traceback.format_exc()}")


class PreviewFrameWorker(QObject):
    log = Signal(str)
    finished = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        clip_path: str,
        timestamp: float,
        *,
        segments: list[list[str]] | None = None,
        kind: str = "main",
        main_y: int = 1180,
        addr_y: int = 1390,
        video_template: str = "Basic Subtitle",
        template_config: dict[str, Any] | None = None,
        motion: str = "none",
        clip_progress: float = 0.0,
        font_dir: str = "",
        accurate: bool = False,
    ):
        super().__init__()
        self.clip_path = clip_path
        self.timestamp = timestamp
        self.segments = segments or []
        self.kind = kind
        self.main_y = main_y
        self.addr_y = addr_y
        self.video_template = video_template
        self.template_config = normalize_template_config(video_template, template_config)
        self.motion = motion
        self.clip_progress = clip_progress
        self.font_dir = Path(font_dir).expanduser() if font_dir else None
        self.accurate = accurate

    @Slot()
    def run(self) -> None:
        try:
            if self.accurate:
                frame = render_ass_preview_frame(
                    Path(self.clip_path),
                    self.timestamp,
                    segments=self.segments,
                    kind=self.kind,
                    main_y=self.main_y,
                    addr_y=self.addr_y,
                    video_template=self.video_template,
                    template_config=self.template_config,
                    motion=self.motion,
                    clip_progress=self.clip_progress,
                    font_dir=self.font_dir,
                )
            else:
                frame = extract_preview_frame(Path(self.clip_path), self.timestamp)
            self.finished.emit(str(frame))
        except Exception as exc:
            self.failed.emit(f"{exc}\n\n{traceback.format_exc()}")


class PyAVTimelinePlaybackWorker(QObject):
    frame_ready = Signal(QImage, float)
    finished = Signal()
    failed = Signal(str)

    def __init__(
        self,
        clips: list[dict],
        start_source_seconds: float,
        fps: float = 30.0,
        width: int = 360,
        height: int = 640,
    ):
        super().__init__()
        self.clips = clips
        self.start_source_seconds = max(0.0, float(start_source_seconds))
        self.fps = max(1.0, float(fps))
        self.width = max(1, int(width))
        self.height = max(1, int(height))
        self._running = True

    @Slot()
    def run(self) -> None:
        try:
            self._play()
        except Exception as exc:
            self.failed.emit(f"{exc}\n\n{traceback.format_exc()}")
        finally:
            self.finished.emit()

    @Slot()
    def stop(self) -> None:
        self._running = False

    def _play(self) -> None:
        import av

        clip_index, clip_offset = self._clip_for_source_time(self.start_source_seconds)
        if clip_index is None:
            return
        frame_interval = 1.0 / self.fps
        next_deadline = time.perf_counter()

        while self._running and clip_index < len(self.clips):
            clip = self.clips[clip_index]
            path = Path(str(clip["path"]))
            if not path.exists():
                clip_index += 1
                clip_offset = 0.0
                continue
            clip_start = float(clip.get("start", 0.0))
            clip_duration = float(clip.get("duration", 0.0))
            timeline_clip_start = sum(max(0.0, float(item.get("duration", 0.0))) for item in self.clips[:clip_index])
            local_start = max(0.0, min(clip_duration, clip_offset))
            source_time = clip_start + local_start
            frame_index = 0

            with av.open(str(path)) as container:
                stream = container.streams.video[0]
                stream.thread_type = "AUTO"
                seek_target = int(max(0.0, source_time) / float(stream.time_base))
                container.seek(seek_target, any_frame=False, backward=True, stream=stream)

                for frame in container.decode(stream):
                    if not self._running:
                        return
                    pts = float(frame.pts * stream.time_base) if frame.pts is not None else source_time
                    if pts + 0.02 < source_time:
                        continue
                    if pts > clip_start + clip_duration:
                        break

                    qimage = self._frame_to_qimage(frame)
                    timeline_seconds = timeline_clip_start + local_start + frame_index * frame_interval
                    if timeline_seconds > timeline_clip_start + clip_duration + 0.001:
                        break
                    self.frame_ready.emit(qimage, timeline_seconds)
                    frame_index += 1

                    next_deadline += frame_interval
                    delay = next_deadline - time.perf_counter()
                    if delay > 0:
                        time.sleep(min(delay, frame_interval))
                    else:
                        next_deadline = time.perf_counter()

            clip_index += 1
            clip_offset = 0.0

    def _clip_for_source_time(self, source_seconds: float) -> tuple[int | None, float]:
        cursor = 0.0
        for index, clip in enumerate(self.clips):
            duration = max(0.0, float(clip.get("duration", 0.0)))
            end = cursor + duration
            if source_seconds <= end or index == len(self.clips) - 1:
                return index, max(0.0, min(duration, source_seconds - cursor))
            cursor = end
        return None, 0.0

    def _frame_to_qimage(self, frame) -> QImage:
        try:
            array = self._cover_crop_rgba(frame)
        except Exception:
            array = frame.to_ndarray(format="rgba")
        height, width, _channels = array.shape
        qimage = QImage(array.data, width, height, width * 4, QImage.Format_RGBA8888)
        return qimage.copy()

    def _cover_crop_rgba(self, frame):
        src_w = max(1, int(frame.width))
        src_h = max(1, int(frame.height))
        scale = max(self.width / src_w, self.height / src_h)
        scaled_w = max(self.width, int(round(src_w * scale)))
        scaled_h = max(self.height, int(round(src_h * scale)))
        frame = frame.reformat(width=scaled_w, height=scaled_h, format="rgba")
        array = frame.to_ndarray()
        top = max(0, (scaled_h - self.height) // 2)
        left = max(0, (scaled_w - self.width) // 2)
        return array[top : top + self.height, left : left + self.width, :]


class AccurateTimelinePlaybackWorker(QObject):
    frame_ready = Signal(QImage, float)
    finished = Signal()
    failed = Signal(str)

    def __init__(
        self,
        clips: list[dict],
        captions: list[dict],
        start_source_seconds: float,
        *,
        main_y: int = 1180,
        addr_y: int = 1390,
        video_template: str = "Basic Subtitle",
        template_config: dict[str, Any] | None = None,
        font_dir: str = "",
        fps: float = 12.0,
    ):
        super().__init__()
        self.clips = clips
        self.captions = captions
        self.start_source_seconds = max(0.0, float(start_source_seconds))
        self.main_y = int(main_y)
        self.addr_y = int(addr_y)
        self.video_template = video_template or "Basic Subtitle"
        self.template_config = normalize_template_config(self.video_template, template_config)
        self.font_dir = Path(font_dir).expanduser() if font_dir else None
        self.fps = max(1.0, float(fps))
        self._running = True
        self._process: subprocess.Popen | None = None

    @Slot()
    def run(self) -> None:
        try:
            self._play()
        except Exception as exc:
            self.failed.emit(f"{exc}\n\n{traceback.format_exc()}")
        finally:
            self._terminate_process()
            self.finished.emit()

    @Slot()
    def stop(self) -> None:
        self._running = False
        self._terminate_process()

    def _play(self) -> None:
        ensure_vendor_on_path()
        clip_index, clip_offset = self._clip_for_source_time(self.start_source_seconds)
        if clip_index is None:
            return

        while self._running and clip_index < len(self.clips):
            clip = self.clips[clip_index]
            path = Path(str(clip["path"]))
            if not path.exists():
                clip_index += 1
                clip_offset = 0.0
                continue

            clip_start = float(clip.get("start", 0.0))
            clip_duration = max(0.0, float(clip.get("duration", 0.0)))
            local_start = max(0.0, min(clip_duration, clip_offset))
            render_duration = max(0.0, clip_duration - local_start)
            if render_duration <= 0:
                clip_index += 1
                clip_offset = 0.0
                continue

            timeline_clip_start = sum(max(0.0, float(item.get("duration", 0.0))) for item in self.clips[:clip_index])
            self._render_clip_stream(path, clip_start + local_start, render_duration, timeline_clip_start + local_start)
            clip_index += 1
            clip_offset = 0.0

    def _render_clip_stream(self, path: Path, source_time: float, duration: float, source_timeline_start: float) -> None:
        ensure_vendor_on_path()
        import silent_vlog_maker.shorts_vertical as shorts_vertical

        with tempfile.TemporaryDirectory(prefix="autocapcut_playback_preview_") as tmp:
            tmp_dir = Path(tmp)
            ass_path = tmp_dir / "preview.ass"
            blocks = self._caption_blocks_for_window(source_timeline_start, duration)
            if blocks:
                old_main_pos = shorts_vertical._MAIN_POS
                old_addr_pos = shorts_vertical._ADDR_POS
                old_header = shorts_vertical._HEADER
                try:
                    shorts_vertical._MAIN_POS = rf"{{\an5\pos(540,{self.main_y})}}"
                    shorts_vertical._ADDR_POS = rf"{{\an5\pos(540,{self.addr_y})}}"
                    shorts_vertical._HEADER = self._ass_header_with_template(shorts_vertical._HEADER)
                    shorts_vertical.build_multicolor_ass(blocks, str(ass_path))
                finally:
                    shorts_vertical._MAIN_POS = old_main_pos
                    shorts_vertical._ADDR_POS = old_addr_pos
                    shorts_vertical._HEADER = old_header

            vf = shorts_vertical._NORMV
            grade = video_grade_filter(str(self.template_config.get("video", {}).get("grade", "none")))
            if grade:
                vf += f",{grade}"
            if blocks:
                vf += "," + _ass_filter("preview.ass", self.font_dir, tmp_dir)
            vf += ",scale=360:640,format=rgb24"

            cmd = [
                "ffmpeg",
                "-v",
                "error",
                "-ss",
                f"{max(0.0, source_time):.3f}",
                "-t",
                f"{max(0.0, duration):.3f}",
                "-i",
                str(path),
                "-vf",
                vf,
                "-r",
                f"{self.fps:g}",
                "-an",
                "-f",
                "image2pipe",
                "-vcodec",
                "ppm",
                "pipe:1",
            ]
            self._process = subprocess.Popen(
                cmd,
                cwd=tmp_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                self._read_ppm_stream(source_timeline_start)
            finally:
                self._terminate_process()

    def _read_ppm_stream(self, source_timeline_start: float) -> None:
        if self._process is None or self._process.stdout is None:
            return
        frame_index = 0
        frame_interval = 1.0 / self.fps
        next_deadline = time.perf_counter()
        stream = self._process.stdout

        while self._running:
            header = stream.readline()
            if not header:
                break
            if header.strip() != b"P6":
                raise RuntimeError("Unexpected preview frame stream format")
            size_line = stream.readline()
            while size_line.startswith(b"#"):
                size_line = stream.readline()
            width_text, height_text = size_line.split()[:2]
            width = int(width_text)
            height = int(height_text)
            max_line = stream.readline()
            if max_line.strip() != b"255":
                raise RuntimeError("Unexpected preview frame color depth")
            frame_size = width * height * 3
            data = stream.read(frame_size)
            if len(data) != frame_size:
                break

            image = QImage(data, width, height, width * 3, QImage.Format_RGB888).copy()
            source_seconds = source_timeline_start + frame_index * frame_interval
            self.frame_ready.emit(image, source_seconds)
            frame_index += 1

            next_deadline += frame_interval
            delay = next_deadline - time.perf_counter()
            if delay > 0:
                time.sleep(min(delay, frame_interval))
            else:
                next_deadline = time.perf_counter()

    def _caption_blocks_for_window(self, source_timeline_start: float, duration: float) -> list[tuple[float, float, list[tuple[str, str]], str]]:
        blocks: list[tuple[float, float, list[tuple[str, str]], str]] = []
        window_end = source_timeline_start + duration
        for caption in self.captions:
            try:
                start = float(caption.get("start", 0.0))
                end = float(caption.get("end", start))
            except (TypeError, ValueError):
                continue
            if end <= source_timeline_start or start >= window_end:
                continue
            segments = []
            for segment in caption.get("segments", []) or []:
                if isinstance(segment, dict):
                    text = str(segment.get("text", ""))
                    color = str(segment.get("color", "w"))
                elif isinstance(segment, (list, tuple)) and segment:
                    text = str(segment[0])
                    color = str(segment[1]) if len(segment) > 1 else "w"
                else:
                    text = str(segment)
                    color = "w"
                if text:
                    segments.append((text, color))
            if not segments:
                continue
            local_start = max(0.0, start - source_timeline_start)
            local_end = min(duration, end - source_timeline_start)
            if local_end > local_start:
                blocks.append((local_start, local_end, segments, str(caption.get("kind", "main"))))
        return blocks

    def _clip_for_source_time(self, source_seconds: float) -> tuple[int | None, float]:
        cursor = 0.0
        for index, clip in enumerate(self.clips):
            duration = max(0.0, float(clip.get("duration", 0.0)))
            end = cursor + duration
            if source_seconds <= end or index == len(self.clips) - 1:
                return index, max(0.0, min(duration, source_seconds - cursor))
            cursor = end
        return None, 0.0

    def _terminate_process(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=1.0)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass

    @staticmethod
    def _ass_header_without_shadow(header: str) -> str:
        lines: list[str] = []
        for line in header.splitlines():
            if line.startswith("Style: "):
                parts = line.split(",")
                if len(parts) > 16:
                    parts[16] = "0"
                    line = ",".join(parts)
            lines.append(line)
        return "\n".join(lines)

    def _ass_header_with_template(self, header: str) -> str:
        caption = self.template_config.get("caption", {}) if isinstance(self.template_config, dict) else {}
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
