# Assets

## Done

- `banner.svg` — README header.
- `nfl-gdbt-demo.gif` (4.1 MB) — hero slot, GBDT bitstream. Note the filename typo
  (`gdbt`); the README links it as spelled, so renaming it means editing the README too.
- `nfl-mlp-demo.gif` (3.1 MB) — MLP bitstream, in the "Two bitstreams, one page" section.

Both show a real prediction coming back from real hardware with the raw response word in
frame, which is what separates them from a screenshot of any web app.

## Still worth capturing

### 1. `board.jpg` — strongly recommended

A photo of the Basys 3 mid-run, ideally with the laptop screen showing the UI in the same
frame. This is the one thing no diagram can fake, and it is the reason a reader believes the
rest of the numbers. Good candidate for the "The hardware" section, which currently opens
with a diagram rather than the object itself.

### 2. `staircase.png` — the best remaining technical image

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
