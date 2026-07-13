from __future__ import annotations


CINEMATIC_FILTER = (
    "eq=saturation=1.12:contrast=1.12:brightness=0.015:gamma=0.98,"
    "curves=master='0/0.02 0.20/0.16 0.50/0.52 0.80/0.88 1/0.98':"
    "red='0/0.02 0.50/0.56 1/1':"
    "green='0/0 0.50/0.50 1/0.98':"
    "blue='0/0.06 0.50/0.46 1/0.92',"
    "unsharp=5:5:0.35:3:3:0.15"
)


def normalize_grade(value: str | None) -> str:
    return (value or "none").strip().lower().replace("-", "_").replace(" ", "_")


def is_video_grade_enabled(value: str | None) -> bool:
    return normalize_grade(value) not in {"", "none", "off"}


def video_grade_filter(value: str | None) -> str:
    grade = normalize_grade(value)
    if grade == "cinematic":
        return CINEMATIC_FILTER
    return ""
