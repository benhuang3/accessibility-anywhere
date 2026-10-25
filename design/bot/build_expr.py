"""Builds ally-bear-parts.svg and expr/*.svg from the named parts.
Re-run: python build_expr.py   (the original ally-bear.svg is never touched)."""
from pathlib import Path

HERE = Path(__file__).parent
INK, BROW = "#2e2523", "#4a3530"

SHADOW = '<ellipse cx="100" cy="229" rx="62" ry="6" fill="#e9dccb" stroke="none"/>'
ARMS = ('<ellipse cx="44" cy="150" rx="15" ry="30" transform="rotate(14 44 150)" fill="#fff"/>\n'
        '<ellipse cx="156" cy="150" rx="15" ry="30" transform="rotate(-14 156 150)" fill="#fff"/>')
# worried: paws drawn over the body, raised toward the chest
ARMS_UP = ('<ellipse cx="64" cy="154" rx="13" ry="24" transform="rotate(-24 64 154)" fill="#fff"/>\n'
           '<ellipse cx="136" cy="154" rx="13" ry="24" transform="rotate(24 136 154)" fill="#fff"/>')
BODY = ('<ellipse cx="100" cy="162" rx="50" ry="54" fill="#fff"/>\n'
        '<ellipse cx="100" cy="180" rx="30" ry="28" fill="#eef4f7" stroke="none"/>')
FEET = ('<ellipse cx="74" cy="214" rx="24" ry="13" fill="#fff"/>\n'
        '<ellipse cx="126" cy="214" rx="24" ry="13" fill="#fff"/>\n'
        '<ellipse cx="74" cy="217" rx="10" ry="5" fill="#e3edf2" stroke="none"/>\n'
        '<ellipse cx="126" cy="217" rx="10" ry="5" fill="#e3edf2" stroke="none"/>')


def ears(raise_right=0):
    y = 44 - raise_right
    return (f'<circle cx="54" cy="44" r="17" fill="#fff"/>\n<circle cx="146" cy="{y}" r="17" fill="#fff"/>\n'
            f'<circle cx="54" cy="44" r="9" fill="#dce8ee" stroke="none"/>\n'
            f'<circle cx="146" cy="{y}" r="9" fill="#dce8ee" stroke="none"/>')


HEAD = '<ellipse cx="100" cy="84" rx="58" ry="50" fill="#fff"/>'
CHEEKS = ('<ellipse cx="64" cy="103" rx="9" ry="5.5" fill="#f5cfcf" stroke="none"/>\n'
          '<ellipse cx="136" cy="103" rx="9" ry="5.5" fill="#f5cfcf" stroke="none"/>')
EYE_L = ('<ellipse cx="78" cy="89" rx="5.5" ry="6.5" fill="#2e2523" stroke="none"/>\n'
         '<circle cx="80" cy="86.5" r="1.8" fill="#fff" stroke="none"/>')
EYE_R = ('<ellipse cx="122" cy="89" rx="5.5" ry="6.5" fill="#2e2523" stroke="none"/>\n'
         '<circle cx="124" cy="86.5" r="1.8" fill="#fff" stroke="none"/>')
MUZZLE = '<ellipse cx="100" cy="107" rx="19" ry="14" fill="#f4ede2" stroke="none"/>'
NOSE = '<path d="M93 100 q7 -4 14 0 q-2 6 -7 7 q-5 -1 -7 -7z" fill="#4a3530" stroke="none"/>'
MOUTH = '<path d="M100 107 v3 M93 110 q3.5 4 7 0 q3.5 4 7 0" fill="none" stroke="#4a3530" stroke-width="2" stroke-linecap="round"/>'
SCARF = ('<rect x="114" y="136" width="20" height="40" rx="7" transform="rotate(-5 124 156)" fill="#2c7d8f" stroke="none"/>\n'
         '<rect x="56" y="126" width="88" height="18" rx="9" fill="#2c7d8f" stroke="none"/>\n'
         '<path d="M118 161 h12 M118 167 h12" stroke="#1f5f6d" stroke-width="2" stroke-linecap="round"/>')


def arc(d, w=2.4):
    return f'<path d="{d}" fill="none" stroke="{INK}" stroke-width="{w}" stroke-linecap="round" stroke-linejoin="round"/>'


def brow(d):
    return f'<path d="{d}" fill="none" stroke="{BROW}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>'


def line_mouth(d):
    return f'<path d="{d}" fill="none" stroke="#4a3530" stroke-width="2" stroke-linecap="round"/>'


OPEN_MOUTH = ('<path d="M100 107 v3" fill="none" stroke="#4a3530" stroke-width="2" stroke-linecap="round"/>\n'
              '<path d="M94.5 111 q5.5 -1.5 11 0 q0.5 8 -5.5 8 q-6 0 -5.5 -8z" fill="#4a3530" stroke="#4a3530" stroke-width="1.5" stroke-linejoin="round"/>\n'
              '<ellipse cx="100" cy="116.6" rx="3.2" ry="1.8" fill="#f5cfcf" stroke="none"/>')

EXPR = {
    "idle": {},
    "talk-a": {},  # closed mouth frame (base mouth)
    "talk-b": {"mouth": OPEN_MOUTH},
    "blink": {"eyeL": arc("M72.5 90 q5.5 5 11 0"), "eyeR": arc("M116.5 90 q5.5 5 11 0")},
    "listening": {
        "tilt": 8, "ears": ears(raise_right=4),
        "mouth": '<path d="M100 107 v3" fill="none" stroke="#4a3530" stroke-width="2" stroke-linecap="round"/>\n'
                 '<ellipse cx="100" cy="115" rx="2.8" ry="3.2" fill="#4a3530" stroke="#4a3530" stroke-width="1.5"/>'},
    "happy": {"eyeL": arc("M72 91 q6 -7 12 0"), "eyeR": arc("M116 91 q6 -7 12 0"),
              "mouth": line_mouth("M100 107 v2 M89 111 q11 11 22 0")},
    "concerned": {"brows": brow("M70 79 Q76 76 85 72") + "\n" + brow("M130 79 Q124 76 115 72"),
                  "mouth": line_mouth("M100 107 v3 M94.5 113 q5.5 -1 11 0")},
    "worried": {"brows": brow("M69 81 Q76 75 86 67") + "\n" + brow("M131 81 Q124 75 114 67"),
                "mouth": line_mouth("M100 107 v3 M94 115 q6 -5 12 0"), "arms_up": True},
}


def build(o):
    tilt = f' transform="rotate({o["tilt"]} 100 130)"' if "tilt" in o else ""
    arms = o.get("arms_up")

    def g(i, body):
        return f'<g id="{i}">\n{body}\n</g>'

    parts = [g("shadow", SHADOW)]
    if not arms:
        parts.append(g("arms", ARMS))
    parts += [g("body", BODY), g("feet", FEET)]
    if arms:
        parts.append(g("arms", ARMS_UP))
    head = [g("ears", o.get("ears", ears())), g("head", HEAD), g("cheeks", CHEEKS),
            g("eyeL", o.get("eyeL", EYE_L)), g("eyeR", o.get("eyeR", EYE_R)),
            g("brows", o.get("brows", "")), g("muzzle", MUZZLE), g("nose", NOSE),
            g("mouth", o.get("mouth", MOUTH))]
    parts.append(f'<g id="headGroup"{tilt}>\n' + "\n".join(head) + "\n</g>")
    parts.append(g("scarf", SCARF))
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 240" width="400" height="480" role="img" '
            'aria-label="Ally, a polar bear with a teal scarf">\n'
            '<g stroke="#9fbfcc" stroke-width="3" stroke-linejoin="round">\n' + "\n".join(parts) + "\n</g>\n</svg>\n")


def main():
    (HERE / "expr").mkdir(exist_ok=True)
    (HERE / "ally-bear-parts.svg").write_text(build({}), encoding="utf-8")
    for name, over in EXPR.items():
        (HERE / "expr" / f"{name}.svg").write_text(build(over), encoding="utf-8")
    print("built", len(EXPR) + 1, "svgs")


if __name__ == "__main__":
    main()
