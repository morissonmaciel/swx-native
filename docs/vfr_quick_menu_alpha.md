# BOTW VFR quick-menu alpha guard

This patch addresses the black flash reported when opening the weapon, bow,
shield, or rune quick selector with BOTW 1.9.0 VFR enabled. The visual cause
is documented by the Savage Wind reference for this exact game build: the
`HoldShortCutBg` animation corrects the alpha of `P_Capture_00` only when its
animation frame equals `1.0`. A fractional real-time UI step can cross that
boundary without landing on the exact value, leaving the captured scene
transparent during the transition.

## Patch behavior

`dm_vfr_quick_menu_alpha.patch` changes only the branch at `main+0xe26a38`
in the BOTW 1.9.0 game profile. The original `fcmp s0, s1` compares the
animation frame against `1.0`; the following `b.ne` skips the game's alpha
correction when the values differ. The guard preserves that exact comparison
when VFR does not own the game clock, a movie owns the frame step, or a
scripted guest override is active. In the VFR-owned path it applies the
existing correction whenever the frame is positive and ordered. Frame zero,
negative values, and NaN retain the skip path. The correction block and its
existing pane lookup remain the game's own code.

No UI clock, simulation ratio, queue policy, display cadence, environment
variable, or RomFS asset changes. In particular, the VFR UI step continues to
read the measured ratio from state `+0xA0`.

## Validation before gameplay

Check the three original instructions in the exact BOTW 1.9.0 image:

| Address | Required word | Meaning |
|---|---:|---|
| `main+0xe26a30` | `0x1e2e1001` | `fmov s1, #1.0` |
| `main+0xe26a34` | `0x1e212000` | `fcmp s0, s1` |
| `main+0xe26a38` | `0x540000e1` | `b.ne main+0xe26a54` |

Validate that the rewriter emits the guard in available stub space, preserves
the original stock branch semantics and reaches exactly `main+0xe26a3c` for
the correction and `main+0xe26a54` to skip it. Check that temporary registers
remain dead through both original branch destinations. Apply the patch in a
clean scratch core, verify its sentinel, run the normalizer a second time, and
build the BOTW host. Gameplay is still required to prove the visual result.

## Visual A/B protocol

Compare two builds with the same corrected VFR cadence. The candidate includes
this alpha guard; the baseline omits only this patch. Use the same BOTW 1.9.0
save, panel, resolution, route, settings, and warm-up. Keep `SWX_VFR=relaxed`
and `SWX_VFR_HZ=auto` identical in both builds. In each build, open and close
the weapon, bow, shield, and rune selectors repeatedly. Capture the first
half-second at each open, selection changes, and close. Record whether the
scene capture, alpha transition, icons, and surrounding world remain visible.

The expected result is that the recurring 0.25–0.5 s black flash disappears
with the guard while the selectors and stock path behave as before. Compare
frame cadence, queue counters, long frames, `[RED]`, and visible pacing as
regression checks. Do not claim the guard fixes a black background that
persists independently of the alpha transition. If that occurs, investigate
the quick-menu scene-capture production and sampling path as a separate cause.

Repeat a stock control with `SWX_VFR=0` to verify the original equality path.
The integrated series places this patch after the present worker and queue
policy. The installer supports an append-only upgrade from the worker-only
stack and from the corrected queue-policy stack, while retaining the
historical display-rate migration when its credit-fix preconditions hold.

## Gameplay result (2026-11-18)

The player completed two gameplay runs with `SWX_VFR=relaxed` and
`SWX_VFR_HZ=auto`, reporting that the recurring quick-menu black flash was
absent in both. This is a pass for the observed VFR use case. The logs confirm
the settings and normal host exits, but do not identify individual quick-menu
openings. The second run included one perceived hitch. Its strongest logged
candidate is an upload burst at frame 776 (15.68 s): 275 first uploads totaling
37.8 MB in a 53.6 ms frame, with 5.5 ms measured in uploads. The log cannot
prove that burst caused the perceived hitch or link it to a quick-menu
opening. Its reporting window also contained two eligible empty ticks and one
oldest drop. Preserved second-run log:
`/tmp/swx-vfr-gameplay-20261118/alpha-on-130740.log`.

These two runs validate the reported alpha-guard behavior in this setup; they
do not complete the broader VFR steady-rate Gate 2, the full selector matrix,
or the stock-control comparison described above.
