"""Single source of truth for Ally's design tokens. Writes tokens.json, tokens.css, tokens.py
and contrast.md, and fails if any text pair misses WCAG AA.  Re-run: python build_tokens.py"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent

LIGHT = {
    "surface": "#F7F1E7",          # cream from the muzzle / ground shadow
    "surface-raised": "#FFFFFF",
    "surface-sunken": "#EDE3D2",
    "ink": "#2E2523",              # the bear's eyes
    "ink-soft": "#5C504A",
    "outline": "#9FBFCC",          # the bear's outline
    "primary": "#2C7D8F",          # scarf
    "primary-ink": "#FFFFFF",
    "primary-strong": "#1F5F6D",   # scarf folds
    "primary-wash": "#E0EFF2",
    "caution": "#B7701F",          # caramel: risk frame
    "caution-ink": "#7A4A0E",      # eyebrow / caution text
    "caution-wash": "#FBEFD9",
    "danger": "#A63A3A",           # End call only
    "danger-ink": "#FFFFFF",
    "good": "#2E7A4F",
    "good-ink": "#FFFFFF",
    "focus": "#1D4ED8",
    "disabled": "#E4DACA",
    "disabled-ink": "#5C504A",
}
DARK = {
    "surface": "#1E1917",
    "surface-raised": "#2B2421",
    "surface-sunken": "#151110",
    "ink": "#F6EFE8",
    "ink-soft": "#C9BDB4",
    "outline": "#5F7F8C",
    "primary": "#6CBFD0",
    "primary-ink": "#10262C",
    "primary-strong": "#9AD6E2",
    "primary-wash": "#1F3A41",
    "caution": "#E0A04A",
    "caution-ink": "#F0C078",
    "caution-wash": "#3A2C19",
    "danger": "#E07A7A",
    "danger-ink": "#2A0E0E",
    "good": "#6CC48F",
    "good-ink": "#0E2617",
    "focus": "#8FB4FF",
    "disabled": "#3A322E",
    "disabled-ink": "#C9BDB4",
}
RADIUS = {"sm": 12, "md": 20, "lg": 28, "pill": 999}
FONT = '"Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif'
TYPE = {  # px, for older eyes
    "body": 22, "body-lg": 24, "title": 31, "button": 22, "eyebrow": 19, "chip": 20, "small": 20,
}

# (foreground, background, label, size px, bold) -- every text pair we use
TEXT_PAIRS = [
    ("ink", "surface", "Body text on page", 22, False),
    ("ink", "surface-raised", "Body text in bubble", 22, False),
    ("ink-soft", "surface", "Secondary text on page", 22, False),
    ("ink-soft", "surface-raised", "Secondary text in bubble", 22, False),
    ("ink", "caution-wash", "Body text in confirm bubble", 22, False),
    ("ink-soft", "caution-wash", "Secondary text in confirm bubble", 22, False),
    ("caution-ink", "surface-raised", "Eyebrow in bubble", 19, True),
    ("caution-ink", "caution-wash", "Eyebrow in confirm bubble", 19, True),
    ("primary-ink", "primary", "Primary button", 22, True),
    ("primary-ink", "primary-strong", "Primary button, pressed", 22, True),
    ("primary-strong", "surface-raised", "Secondary (outlined) button", 22, True),
    ("primary-strong", "primary-wash", "Call Priya pill", 22, True),
    ("primary-strong", "caution-wash", "Secondary button in confirm bubble", 22, True),
    ("danger-ink", "danger", "End call button", 22, True),
    ("good-ink", "good", "Success chip", 20, True),
    ("disabled-ink", "disabled", "Disabled button", 22, True),
    ("ink", "primary-wash", "Listening chip", 20, False),
]
# non-text UI parts that must be perceivable (3:1)
NONTEXT_PAIRS = [
    ("caution", "surface", "Caution frame vs page"),
    ("caution", "surface-raised", "Caution frame vs bubble"),
    ("primary", "surface-raised", "Secondary button border vs bubble"),
    ("primary", "caution-wash", "Secondary button border vs confirm bubble"),
    ("focus", "surface", "Focus ring vs page"),
    ("focus", "surface-raised", "Focus ring vs bubble"),
]


def lum(h):
    c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def ratio(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def needed(size, bold):
    return 3.0 if size >= 24 or (bold and size >= 19) else 4.5


def check():
    rows, ok = [], True
    for mode, pal in (("Light", LIGHT), ("Dark", DARK)):
        rows.append(f"\n## {mode}: text pairs\n")
        rows.append("| Foreground | Background | Use | Size | Ratio | Needs | Result |")
        rows.append("|---|---|---|---|---|---|---|")
        for fg, bg, label, size, bold in TEXT_PAIRS:
            r, need = ratio(pal[fg], pal[bg]), needed(size, bold)
            good = r >= need
            ok &= good
            rows.append(f"| {fg} `{pal[fg]}` | {bg} `{pal[bg]}` | {label} | {size}px{' bold' if bold else ''} "
                        f"| {r:.2f}:1 | {need}:1 | {'PASS' if good else 'FAIL'} |")
        rows.append(f"\n## {mode}: non-text parts (3:1)\n")
        rows.append("| Part | Background | Use | Ratio | Result |")
        rows.append("|---|---|---|---|---|")
        for fg, bg, label in NONTEXT_PAIRS:
            r = ratio(pal[fg], pal[bg])
            good = r >= 3
            ok &= good
            rows.append(f"| {fg} `{pal[fg]}` | {bg} `{pal[bg]}` | {label} | {r:.2f}:1 | {'PASS' if good else 'FAIL'} |")
    return ok, "\n".join(rows)


def write():
    ok, table = check()
    md = ("# Contrast check\n\nGenerated by `build_tokens.py` (WCAG 2.x relative luminance). Text must reach 4.5:1, "
          "or 3:1 for text 24px and up or bold 19px and up. Non-text parts must reach 3:1.\n\n"
          f"Overall: {'ALL PASS' if ok else 'FAILURES PRESENT'}\n{table}\n\n"
          "## Notes\n\n- `outline` (#9FBFCC) is decorative (bear outline, quiet bubble border). It is about 1.9:1 on white, "
          "so it never carries meaning and never bounds a control. Controls use `primary` borders, which pass.\n"
          "- `danger` is used only for the End call button.\n")
    (HERE / "contrast.md").write_text(md, encoding="utf-8")

    data = {"color": {"light": LIGHT, "dark": DARK}, "radius": RADIUS, "font": FONT, "type": TYPE,
            "touch-target-min": 64}
    (HERE / "tokens.json").write_text(json.dumps(data, indent=2), encoding="utf-8")

    def block(pal, indent):
        return "\n".join(f"{indent}--ally-{k}: {v};" for k, v in pal.items())

    css = ["/* Ally design tokens. Generated by build_tokens.py: edit that file, not this one. */", ":root {",
           block(LIGHT, "  ")]
    css += [f"  --ally-radius-{k}: {v}px;" for k, v in RADIUS.items()]
    css += [f"  --ally-font: {FONT};"]
    css += [f"  --ally-text-{k}: {v}px;" for k, v in TYPE.items()]
    css += ["  --ally-touch-min: 64px;", "}", "", "@media (prefers-color-scheme: dark) {", "  :root {",
            block(DARK, "    "), "  }", "}", ""]
    (HERE / "tokens.css").write_text("\n".join(css), encoding="utf-8")

    py = ['"""Ally design tokens for Tk. Generated by build_tokens.py: edit that file, not this one."""', "",
          "LIGHT = {"]
    py += [f'    "{k.replace("-", "_")}": "{v}",' for k, v in LIGHT.items()] + ["}", "", "DARK = {"]
    py += [f'    "{k.replace("-", "_")}": "{v}",' for k, v in DARK.items()] + ["}", ""]
    py += [f"RADIUS = {RADIUS!r}", f'FONT_FAMILY = ("Segoe UI Variable Text", "Segoe UI")',
           f"TYPE_PX = {TYPE!r}", "TOUCH_MIN = 64", ""]
    py += ["# Tk font sizes are points: px * 0.75 at 96 dpi", "TYPE_PT = {k: round(v * 0.75) for k, v in TYPE_PX.items()}", ""]
    (HERE / "tokens.py").write_text("\n".join(py), encoding="utf-8")
    return ok


if __name__ == "__main__":
    good = write()
    print("ALL PASS" if good else "FAILURES: see contrast.md")
    sys.exit(0 if good else 1)
