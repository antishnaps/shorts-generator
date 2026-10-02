"""Deterministic, asset-free atmospheric video effects for FFmpeg renders."""

from __future__ import annotations

import hashlib
import random
from typing import Any, Dict, List, Tuple

import ffmpeg


VIDEO_EFFECTS = (
    "none",
    "auto",
    "cinematic_dust",
    "film_grain",
    "old_film",
    "vhs",
    "soft_bloom",
    "light_leak",
)

AUTO_EFFECTS = (
    "cinematic_dust",
    "cinematic_dust",
    "film_grain",
    "film_grain",
    "soft_bloom",
    "light_leak",
    "old_film",
    "vhs",
)


def stable_effect_seed(seed_source: Any) -> int:
    digest = hashlib.sha256(str(seed_source or "contentbot").encode("utf-8")).digest()
    return max(1, int.from_bytes(digest[:4], "big") & 0x7FFFFFFF)


def normalize_effect_settings(settings: Dict[str, Any] | None) -> Dict[str, Any]:
    settings = settings or {}
    effect = str(settings.get("video_effect") or "auto").strip().lower()
    if effect not in VIDEO_EFFECTS:
        effect = "auto"
    try:
        intensity = float(settings.get("video_effect_intensity", 0.30))
    except (TypeError, ValueError):
        intensity = 0.30
    try:
        probability = float(settings.get("video_effect_probability", 0.40))
    except (TypeError, ValueError):
        probability = 0.40
    return {
        "video_effect": effect,
        "video_effect_intensity": max(0.0, min(1.0, intensity)),
        "video_effect_probability": max(0.0, min(1.0, probability)),
    }


def resolve_video_effect(
    settings: Dict[str, Any] | None,
    seed_source: Any,
    duration: float = 0.0,
) -> Tuple[str, float, int]:
    """Resolve ``auto`` once per output so retries remain visually identical."""
    normalized = normalize_effect_settings(settings)
    effect = normalized["video_effect"]
    intensity = normalized["video_effect_intensity"]
    seed = stable_effect_seed(seed_source)

    if intensity <= 0 or effect == "none":
        return "none", intensity, seed
    if effect != "auto":
        return effect, intensity, seed

    # Atmospheric passes are valuable for short-form content but can add hours
    # of rendering to multi-hour videos. Auto mode stays conservative there;
    # an explicitly selected effect is still honoured.
    if float(duration or 0.0) > 600:
        return "none", intensity, seed

    rng = random.Random(seed)
    if rng.random() > normalized["video_effect_probability"]:
        return "none", intensity, seed
    return rng.choice(AUTO_EFFECTS), intensity, seed


def effect_display_name(effect: str) -> str:
    return {
        "none": "None",
        "cinematic_dust": "Cinematic dust",
        "film_grain": "Film grain",
        "old_film": "Old film",
        "vhs": "VHS",
        "soft_bloom": "Soft bloom",
        "light_leak": "Light leak",
    }.get(effect, effect)


def _dust_source(width: int, height: int, fps: int, duration: float, seed: int):
    small_w = max(48, int(width) // 8)
    small_h = max(48, int(height) // 8)
    source = ffmpeg.input(
        f"nullsrc=s={small_w}x{small_h}:r=1:d={max(0.1, float(duration))}",
        f="lavfi",
    ).video
    return (
        source.filter("format", "gray")
        .filter("geq", lum=f"if(gt(random({seed}),0.997),255,0)")
        .filter("loop", loop=-1, size=1, start=0)
        .filter("setpts", f"N/{max(1, int(fps))}/TB")
        .filter(
            "scroll",
            horizontal=0.0007 + (seed % 5) * 0.00015,
            vertical=-(0.0012 + (seed % 7) * 0.00012),
            hpos=(seed % 97) / 97.0,
            vpos=(seed % 89) / 89.0,
        )
        .filter("scale", int(width), int(height), flags="bilinear")
        .filter("gblur", sigma=0.7)
        .filter("fps", max(1, int(fps)))
    )


def apply_video_effect(
    video_stream,
    settings: Dict[str, Any] | None,
    width: int,
    height: int,
    fps: int,
    duration: float,
    seed_source: Any,
):
    """Apply a resolved effect to an ffmpeg-python video stream."""
    effect, intensity, seed = resolve_video_effect(settings, seed_source, duration)
    if effect == "none":
        return video_stream, effect

    if effect == "cinematic_dust":
        dust = _dust_source(width, height, fps, duration, seed)
        opacity = 0.10 + intensity * 0.28
        return ffmpeg.filter(
            [video_stream, dust],
            "blend",
            all_mode="screen",
            all_opacity=opacity,
            shortest=1,
        ), effect

    if effect == "film_grain":
        strength = max(1, int(2 + intensity * 9))
        return (
            video_stream.filter("noise", alls=strength, allf="t+u", all_seed=seed)
            .filter(
                "eq",
                contrast=1.0 + intensity * 0.035,
                saturation=1.0 + intensity * 0.025,
            ),
            effect,
        )

    if effect == "old_film":
        strength = max(2, int(4 + intensity * 12))
        stream = (
            video_stream.filter(
                "eq",
                contrast=1.04 + intensity * 0.08,
                saturation=max(0.35, 0.78 - intensity * 0.25),
                brightness=f"0.004*sin(7*t+{seed % 19})",
                eval="frame",
            )
            .filter(
                "colorbalance",
                rs=0.025 + intensity * 0.035,
                gs=0.008,
                bs=-(0.025 + intensity * 0.035),
            )
            .filter("noise", alls=strength, allf="t+p", all_seed=seed)
            .filter("vignette", angle="PI/6", eval="init")
        )
        return stream, effect

    if effect == "vhs":
        shift = max(1, int(1 + intensity * 5))
        strength = max(2, int(3 + intensity * 10))
        stream = (
            video_stream.filter("chromashift", cbh=shift, crh=-shift, edge="smear")
            .filter("noise", alls=strength, allf="t+u", all_seed=seed)
            .filter("eq", contrast=1.02, saturation=max(0.65, 0.98 - intensity * 0.20))
            .filter(
                "drawgrid",
                x=0,
                y=0,
                w="iw",
                h=max(3, int(7 - intensity * 3)),
                t=1,
                color=f"black@{0.025 + intensity * 0.07:.3f}",
            )
        )
        return stream, effect

    if effect == "soft_bloom":
        split = video_stream.filter_multi_output("split", 2)
        base = split[0]
        glow = split[1].filter("gblur", sigma=2.0 + intensity * 6.0)
        return ffmpeg.filter(
            [base, glow],
            "blend",
            all_mode="screen",
            all_opacity=0.035 + intensity * 0.13,
            shortest=1,
        ), effect

    if effect == "light_leak":
        leak = ffmpeg.input(
            "gradients="
            f"s={int(width)}x{int(height)}:r={max(1, int(fps))}:d={max(0.1, float(duration))}:"
            "c0=black:c1=0xff7043:c2=black:n=3:"
            f"x0=0:y0={max(1, int(height))}:x1={max(1, int(width))}:y1=0:"
            f"type=radial:speed={0.008 + (seed % 5) * 0.003}",
            f="lavfi",
        ).video.filter("gblur", sigma=8.0 + intensity * 16.0)
        return ffmpeg.filter(
            [video_stream, leak],
            "blend",
            all_mode="screen",
            all_opacity=0.025 + intensity * 0.11,
            shortest=1,
        ), effect

    return video_stream, "none"


def build_filter_complex_effect(
    input_label: str,
    output_label: str,
    settings: Dict[str, Any] | None,
    width: int,
    height: int,
    fps: int,
    duration: float,
    seed_source: Any,
) -> Tuple[List[str], str]:
    """Build equivalent filter-complex lines for the optimized renderer."""
    effect, intensity, seed = resolve_video_effect(settings, seed_source, duration)
    if effect == "none":
        return [], effect

    src = str(input_label).strip("[]")
    dst = str(output_label).strip("[]")
    if effect == "cinematic_dust":
        small_w = max(48, int(width) // 8)
        small_h = max(48, int(height) // 8)
        opacity = 0.10 + intensity * 0.28
        lines = [
            (
                f"nullsrc=s={small_w}x{small_h}:r=1:d={max(0.1, float(duration)):.3f},"
                "format=gray,"
                f"geq=lum='if(gt(random({seed}),0.997),255,0)',"
                f"loop=loop=-1:size=1:start=0,setpts=N/{max(1, int(fps))}/TB,"
                f"scroll=horizontal={0.0007 + (seed % 5) * 0.00015:.5f}:"
                f"vertical={-(0.0012 + (seed % 7) * 0.00012):.5f}:"
                f"hpos={(seed % 97) / 97.0:.5f}:vpos={(seed % 89) / 89.0:.5f},"
                f"scale={int(width)}:{int(height)}:flags=bilinear,gblur=sigma=0.7,"
                f"fps={max(1, int(fps))}[contentbot_dust]"
            ),
            f"[{src}][contentbot_dust]blend=all_mode=screen:all_opacity={opacity:.4f}:shortest=1[{dst}]",
        ]
        return lines, effect

    if effect == "film_grain":
        strength = max(1, int(2 + intensity * 9))
        return [
            f"[{src}]noise=alls={strength}:allf=t+u:all_seed={seed},"
            f"eq=contrast={1.0 + intensity * 0.035:.4f}:"
            f"saturation={1.0 + intensity * 0.025:.4f}[{dst}]"
        ], effect

    if effect == "old_film":
        strength = max(2, int(4 + intensity * 12))
        return [
            f"[{src}]eq=contrast={1.04 + intensity * 0.08:.4f}:"
            f"saturation={max(0.35, 0.78 - intensity * 0.25):.4f}:"
            f"brightness='0.004*sin(7*t+{seed % 19})':eval=frame,"
            f"colorbalance=rs={0.025 + intensity * 0.035:.4f}:gs=0.008:"
            f"bs={-(0.025 + intensity * 0.035):.4f},"
            f"noise=alls={strength}:allf=t+p:all_seed={seed},"
            f"vignette=angle=PI/6:eval=init[{dst}]"
        ], effect

    if effect == "vhs":
        shift = max(1, int(1 + intensity * 5))
        strength = max(2, int(3 + intensity * 10))
        return [
            f"[{src}]chromashift=cbh={shift}:crh={-shift}:edge=smear,"
            f"noise=alls={strength}:allf=t+u:all_seed={seed},"
            f"eq=contrast=1.02:saturation={max(0.65, 0.98 - intensity * 0.20):.4f},"
            f"drawgrid=x=0:y=0:w=iw:h={max(3, int(7 - intensity * 3))}:t=1:"
            f"c=black@{0.025 + intensity * 0.07:.3f}[{dst}]"
        ], effect

    if effect == "soft_bloom":
        return [
            f"[{src}]split=2[contentbot_base][contentbot_glow_src]",
            f"[contentbot_glow_src]gblur=sigma={2.0 + intensity * 6.0:.3f}[contentbot_glow]",
            f"[contentbot_base][contentbot_glow]blend=all_mode=screen:"
            f"all_opacity={0.035 + intensity * 0.13:.4f}:shortest=1[{dst}]",
        ], effect

    if effect == "light_leak":
        return [
            "gradients="
            f"s={int(width)}x{int(height)}:r={max(1, int(fps))}:d={max(0.1, float(duration)):.3f}:"
            "c0=black:c1=0xff7043:c2=black:n=3:"
            f"x0=0:y0={max(1, int(height))}:x1={max(1, int(width))}:y1=0:"
            f"type=radial:speed={0.008 + (seed % 5) * 0.003:.4f},"
            f"gblur=sigma={8.0 + intensity * 16.0:.3f}[contentbot_leak]",
            f"[{src}][contentbot_leak]blend=all_mode=screen:"
            f"all_opacity={0.025 + intensity * 0.11:.4f}:shortest=1[{dst}]",
        ], effect

    return [], "none"
