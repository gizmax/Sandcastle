#!/usr/bin/env python3
"""UGC prompt autoresearch - tune a mode's Layer-3 prompt recipe against a vision metric.

Karpathy-style loop: one mechanical metric (avg vision-judge overall_score), one atomic
change per iteration (swap a single recipe "knob"), auto-revert on regression, history log
as memory. Generation uses the nano-banana CLI; judging uses the gemini.mjs connector - the
same primitives the `tool` step calls inside Sandcastle.

Usage:
    GEMINI_API_KEY=... python scripts/ugc_autoresearch.py \
        --photo /path/to/product.png --mode raw_ugc --iterations 12 --shots 2

Nothing here is Sandcastle-specific; it is a standalone tuner you point at one product photo.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
NANO_BANANA = HOME / ".bun" / "bin" / "nano-banana"
GEMINI_CONNECTOR = (
    Path(__file__).resolve().parent.parent
    / "src" / "sandcastle" / "engine" / "tools" / "connectors" / "gemini.mjs"
)

# Seed recipe per mode: a base scene + swappable knobs. The loop tries knob variants
# one at a time and keeps whatever raises the judge score.
SEED_RECIPES: dict[str, dict] = {
    "raw_ugc": {
        "scene": "held in a young person's hand at a sunlit cafe table, candid POV",
        "knobs": {
            "camera": [
                "shot on a smartphone main camera, ~24mm, f/1.8, handheld slight tilt",
                "shot on an older phone camera, ~28mm, mild lens softness, handheld",
                "front-facing phone selfie framing, ~23mm, casual angle",
            ],
            "light": [
                "soft natural window light, gentle real shadows",
                "warm late-afternoon sun, long soft shadows, golden glow",
                "overcast diffuse daylight, flat soft shadows",
            ],
            "authenticity": [
                "natural skin with visible pores, light sensor grain, micro-motion softness",
                "very candid, slightly imperfect focus, real skin texture, faint grain",
                "unstaged snapshot feel, true-to-life color, subtle handheld blur",
            ],
        },
    },
    "moody": {
        "scene": "on a dark textured surface with a single shaft of light, minimal scene",
        "knobs": {
            "camera": [
                "50mm f/1.8, shallow depth of field, eye-level",
                "85mm f/2, compressed perspective, tight crop",
            ],
            "light": [
                "single directional key light, deep falloff shadows",
                "low-key chiaroscuro, rich blacks, soft rim light",
            ],
            "authenticity": [
                "cinematic film-like color grade, fine grain",
                "editorial moody grade, desaturated shadows, crisp highlights",
            ],
        },
    },
}

NEGATIVES = (
    "Avoid AI-generated look, advertising gloss, plastic skin, oversaturation, "
    "text overlays, watermarks, logos, extra products."
)

JUDGE_PROMPT = (
    "You are a UGC quality judge. The FIRST image is a generated UGC shot; the SECOND is "
    "the original product reference. Score 1-10: product_accuracy, authenticity, "
    "composition, lighting_quality, platform_fit, and overall_score. "
    'Return ONLY JSON: {"product_accuracy","authenticity","composition",'
    '"lighting_quality","platform_fit","overall_score"}.'
)


def build_prompt(recipe: dict, choice: dict[str, int]) -> str:
    knobs = recipe["knobs"]
    parts = [
        f"Authentic UGC photo of the referenced product, {recipe['scene']}.",
        knobs["camera"][choice["camera"]] + ".",
        knobs["light"][choice["light"]] + ".",
        knobs["authenticity"][choice["authenticity"]] + ".",
        "The product matches the reference exactly in shape, materials and colors.",
        NEGATIVES,
    ]
    return " ".join(parts)


def generate(prompt: str, photo: str, out_dir: Path, tag: str) -> str | None:
    """Generate one image via nano-banana, return its path or None."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(NANO_BANANA), prompt, "-r", photo, "-m", "flash", "-a", "4:5",
        "-o", tag, "-d", str(out_dir),
    ]
    env = {**os.environ}
    env.setdefault("PATH", "")
    env["PATH"] = f"{HOME / '.bun' / 'bin'}:{env['PATH']}"
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=240)
    except (subprocess.TimeoutExpired, OSError) as e:
        print(f"  generate {tag} failed ({type(e).__name__}), skipping", file=sys.stderr)
        return None
    for line in res.stdout.splitlines():
        line = line.strip()
        if line.startswith("+") and "Loaded reference" not in line and line.endswith(
            (".jpeg", ".jpg", ".png", ".webp")
        ):
            return line.lstrip("+ ").strip()
    return None


def judge(image: str, photo: str) -> float:
    """Score one image against the reference via the gemini connector. Returns overall_score."""
    opts = json.dumps({"images": [image, photo], "model": "gemini-2.5-flash"})
    try:
        res = subprocess.run(
            ["node", str(GEMINI_CONNECTOR), "analyze_image", JUDGE_PROMPT, opts],
            capture_output=True, text=True, env=os.environ, timeout=120,
        )
        payload = json.loads(res.stdout)
        return float(payload["result"].get("overall_score", 0))
    except Exception:
        return 0.0


def evaluate(recipe: dict, choice: dict[str, int], photo: str, out_dir: Path,
             shots: int, label: str) -> float:
    """Generate `shots` images with this recipe choice and return the average judge score."""
    total, n = 0.0, 0
    for i in range(shots):
        img = generate(build_prompt(recipe, choice), photo, out_dir, f"{label}-{i}")
        if img:
            total += judge(img, photo)
            n += 1
    return round(total / n, 2) if n else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--photo", required=True)
    ap.add_argument("--mode", default="raw_ugc", choices=list(SEED_RECIPES))
    ap.add_argument("--iterations", type=int, default=12)
    ap.add_argument("--shots", type=int, default=2)
    ap.add_argument("--out", default=str(HOME / "Downloads" / "ugc-autoresearch"))
    args = ap.parse_args()

    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("TOOL_GEMINI_API_KEY")):
        print("Set GEMINI_API_KEY (or TOOL_GEMINI_API_KEY) first.", file=sys.stderr)
        return 1

    recipe = SEED_RECIPES[args.mode]
    out_dir = Path(args.out) / args.mode
    history_path = out_dir / "history.jsonl"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Start from knob index 0 for every knob.
    best_choice = {k: 0 for k in recipe["knobs"]}
    best_score = evaluate(recipe, best_choice, args.photo, out_dir, args.shots, "seed")
    print(f"seed score={best_score}  choice={best_choice}")

    knob_names = list(recipe["knobs"])
    it = 0
    while it < args.iterations:
        # Atomic change: advance one knob to its next untried variant.
        knob = knob_names[it % len(knob_names)]
        n_variants = len(recipe["knobs"][knob])
        trial = dict(best_choice)
        trial[knob] = (best_choice[knob] + 1) % n_variants
        if trial == best_choice:
            it += 1
            continue

        score = evaluate(recipe, trial, args.photo, out_dir, args.shots, f"it{it}")
        kept = score > best_score
        if kept:
            best_choice, best_score = trial, score

        record = {"iteration": it, "knob": knob, "trial": trial,
                  "score": score, "best_score": best_score, "kept": kept}
        with history_path.open("a") as f:
            f.write(json.dumps(record) + "\n")
        print(f"it{it} knob={knob} score={score} best={best_score} {'KEEP' if kept else 'revert'}")
        it += 1

    print("\n=== best recipe ===")
    print(f"score={best_score}")
    print(build_prompt(recipe, best_choice))
    print(f"\nHistory: {history_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
