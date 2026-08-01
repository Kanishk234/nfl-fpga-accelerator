# Assets to capture

The README has one image slot left open. Filling it is the single highest-value change
remaining — every well-regarded README surveyed for this rewrite (micrograd, nanoGPT,
hyperfine, bat, lazygit, httpie, gum, neorv32, projf-explore) puts a picture of the thing
*working* above the fold, and this project currently has none.

## 1. `demo.gif` — required, hero slot

Uncomment the `<img>` block at the top of [`../../README.md`](../../README.md) once this
exists.

**What to record:** the web UI with the board attached. Pick a game, hit Run, let the result
land. 5–10 seconds is plenty. Target ~760 px wide.

**What it has to show:** that a real prediction came back from real hardware. If the UI
displays the raw response bytes, make sure that panel is in frame — the raw bytes are what
separates this from a screenshot of any web app, and they're the whole argument of the
project in one image.

**Capture:** ScreenToGif or ShareX on Windows; `peek` or `wf-recorder` + `gifski` on Linux.
Keep it under ~5 MB so GitHub renders it inline.

## 2. `board.jpg` — strongly recommended

A photo of the Basys 3 mid-run, ideally with the laptop screen showing the UI in the same
frame. This is the one thing no diagram can fake, and it is the reason a reader believes the
rest of the numbers. Good candidate for a second image next to the demo, or as the hero if
the GIF proves awkward.

## 3. `staircase.png` — the best remaining technical image

The `elo_diff` sweep described in the README's third section: 8 discrete levels with flat
plateaus, monotonic, versus the MLP's smooth curve on the same axes.

No script currently generates this — `grep` finds no sweep harness in the repo. If you plot
it, generate it from a source that actually justifies the caption:

- Plotting the **float XGBoost** model shows the staircase but does *not* demonstrate that
  the structure survived to silicon. Caption it accordingly, or
- Plot from the **chain golden** (`gbdt/phase6_sim/make_chain_golden.py`) or from board
  responses, which does support the "survived to silicon intact" claim.

Do not caption a float-XGBoost plot as the on-silicon result — that claim is currently
carried by prose and would become a fabricated figure.

## Already in the repo

`notebooks/training_curves.png` exists (MLP training curves). Not first-screen material, but
it could reasonably go in `mlp/phase2_model/PHASE2_COMPLETE.md` if it isn't referenced there
already.
