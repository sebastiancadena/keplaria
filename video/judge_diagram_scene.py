"""Manim scene: the six-box diagram assembling under the narration.

Renders the architecture beat of the submission video (2:25-2:55). It is the
moving twin of `docs/architecture/judge-diagram.svg` -- same six boxes, same
two walls, same palette, same claim.

    cd ~/dev/git/byteql-video && uv run --group animation scripts/render.py \
        ~/dev/git/keplaria/video/judge_diagram_scene.py JudgeDiagram \
        -o ~/dev/git/keplaria/video/judge-diagram.mp4

WHY IT MAY EXIST. The contest's continuous-unedited requirement binds only
the live segment (0:15-2:25). This clip lives outside it, touches no deployed
code, and if cut the static SVG stands in with no other change.

THE ONE DEVICE, unchanged from the still: two walls, each pierced exactly
once. Motion STATES that rather than decorating it -- the walls are drawn
before any box exists, so the skeleton makes the argument first.

LAYOUT IS COMPUTED, NOT TYPED. The first version hand-placed coordinates as
if the frame were the SVG's pixel grid; boxes overlapped and text overflowed,
because Manim font sizes do not scale like SVG ones. Every box here is sized
to its own contents, the row is laid out by accumulating widths, and the
whole group is scaled to the frame at the end. Nothing is a magic number that
has to be re-tuned when a word changes.
"""

from manim import (
    VGroup, Scene, RoundedRectangle, Text, Line, Arrow,
    Create, FadeIn, Write, config, DOWN, RIGHT,
)

VOID = "#0B1020"
INK = "#111827"
AMBER = "#F59E0B"
AMBER_BRIGHT = "#FBBF24"
STAR = "#F8FAFC"
MUTED = "#64748B"

SG = "Space Grotesk"
INTER = "Inter"
MONO = "JetBrains Mono"

TITLE, BODY, CHIP, ZONE = 26, 17, 16, 19
PAD_X, PAD_Y, GAP = 0.34, 0.28, 0.20

config.background_color = VOID


def chip(label):
    txt = Text(label, font=MONO, font_size=CHIP, color=STAR)
    box = RoundedRectangle(
        width=txt.width + 0.36, height=txt.height + 0.24, corner_radius=0.07,
        fill_color=INK, fill_opacity=0.92,
        stroke_color=MUTED, stroke_opacity=0.55, stroke_width=1.1,
    )
    return VGroup(box, txt.move_to(box.get_center()))


def node(title, subtitle=None, lines=(), chips=(), door=False, solid=False):
    """One box, sized to what is inside it."""
    stack = VGroup(Text(title, font=SG, font_size=TITLE, color=STAR,
                        weight="SEMIBOLD"))
    if subtitle:
        stack.add(Text(subtitle, font=INTER, font_size=BODY, color=MUTED))
    for c in chips:
        stack.add(c)
    # Captions sit under the chips, as they do in the SVG the doctor checks.
    for line in lines:
        stack.add(Text(line, font=INTER, font_size=BODY, color=MUTED))
    stack.arrange(DOWN, buff=GAP, aligned_edge=stack[0].get_left() * 0)

    box = RoundedRectangle(
        width=stack.width + 2 * PAD_X, height=stack.height + 2 * PAD_Y,
        corner_radius=0.16,
        fill_color=INK if solid else STAR,
        fill_opacity=1.0 if solid else 0.030,
        stroke_color=MUTED,
        stroke_opacity=0.55 if (door or solid) else 0.32,
        stroke_width=2.2 if (door or solid) else 1.6,
    )
    return VGroup(box, stack.move_to(box.get_center()))


def arrow(a, b, width=5):
    return Arrow(a, b, buff=0.0, stroke_width=width, color=AMBER,
                 max_tip_length_to_length_ratio=0.4, tip_length=0.18)


class JudgeDiagram(Scene):
    def construct(self):
        triggers = node("Triggers",
                        lines=("supplier packet", "clock event", "certificate"))
        coord = node("Coordinator", subtitle="proposes, never acts")
        gate = node("Policy Gate", subtitle="approve · park · refuse",
                    chips=(chip("fleet.v1"),), door=True)
        spec = node("Specialists", chips=(chip("evidence"), chip("compliance")),
                    lines=("no ERP credential", "no write tools"))
        outbox = node("Outbox", chips=(chip("executor"),), door=True,
                      lines=("scoped ERP role", "sole write path"))
        erp = node("ERP", solid=True)
        gc = node("Ground Control", subtitle="human approval · Cloud IAP")

        row = VGroup(triggers, coord, gate, spec, outbox, erp)
        row.arrange(RIGHT, buff=0.62)
        # Ground Control hangs below the band: an interrupt, not a stage.
        gc.next_to(row, DOWN, buff=1.15).align_to(spec, RIGHT)

        board = VGroup(row, gc)
        board.scale_to_fit_width(12.6).move_to([0, -0.15, 0])

        # -- walls, derived from where the doors actually landed ------------
        top, bottom = 3.05, -3.55
        wall_1 = gate.get_center()[0]
        wall_2 = (outbox.get_right()[0] + erp.get_left()[0]) / 2
        walls = VGroup(
            Line([wall_1, top, 0], [wall_1, gate.get_top()[1], 0]),
            Line([wall_1, gate.get_bottom()[1], 0], [wall_1, bottom, 0]),
            Line([wall_2, top, 0], [wall_2, erp.get_center()[1] + 0.34, 0]),
            Line([wall_2, erp.get_center()[1] - 0.34, 0], [wall_2, bottom, 0]),
        ).set_stroke(color=MUTED, width=3.5, opacity=0.75)

        zones = VGroup(*[
            Text(t, font=SG, font_size=ZONE, color=MUTED, weight="MEDIUM")
            .move_to([x, 3.42, 0])
            for t, x in [("PROPOSE", coord.get_center()[0]),
                         ("DECIDE", wall_1),
                         ("ACT", spec.get_center()[0]),
                         ("RECORD", erp.get_center()[0])]
        ])

        self.play(Create(walls, lag_ratio=0.15), run_time=1.1)
        self.play(FadeIn(zones, shift=DOWN * 0.1), run_time=0.6)

        self.play(FadeIn(triggers), run_time=0.5)
        for src, dst in ((triggers, coord), (coord, gate), (gate, spec),
                         (spec, outbox)):
            self.play(Create(arrow(src.get_right(), dst.get_left())),
                      run_time=0.4)
            self.play(FadeIn(dst), run_time=0.5)

        # -- Ground Control: the case parks, a human releases it ------------
        drop_y = gc.get_center()[1]
        park = VGroup(
            Line(gate.get_bottom(), [gate.get_center()[0], drop_y, 0]),
            Line([gate.get_center()[0], drop_y, 0], [gc.get_left()[0], drop_y, 0]),
        ).set_stroke(color=AMBER, width=4.5)
        parked = Text("parked", font=INTER, font_size=BODY, color=AMBER_BRIGHT)
        parked.next_to(park[0], RIGHT, buff=0.16)
        self.play(Create(park), run_time=0.7)
        self.play(FadeIn(gc), Write(parked), run_time=0.55)

        up_x = outbox.get_center()[0]
        release = VGroup(
            Line(gc.get_right(), [up_x, drop_y, 0]),
            Line([up_x, drop_y, 0], [up_x, outbox.get_bottom()[1], 0]),
        ).set_stroke(color=AMBER, width=4.5)
        released = Text("released", font=INTER, font_size=BODY,
                        color=AMBER_BRIGHT)
        released.next_to(release[1], RIGHT, buff=0.16)
        self.play(Create(release), Write(released), run_time=0.7)

        # -- the only crossing, drawn last ----------------------------------
        self.play(Create(arrow(outbox.get_right(), erp.get_left(), width=9)),
                  run_time=0.7)
        self.play(FadeIn(erp), run_time=0.5)
        self.wait(8.0)
