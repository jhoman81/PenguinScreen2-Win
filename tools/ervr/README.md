# Eternal Ring VR profile kit for PenguinScreen2

Goal: good stereo throughout, and head look in the first-person view, driven
through the player's own facing so that walking, attacks and spells follow
your head.

| File | What it is |
|---|---|
| `SLUS-20015.yaml` | The profile (DRAFT 0.1): stereo only, placeholder convergence, a commented camera template. |
| `../gt4vr/gt4cam.py` | The PINE helper from the GT4 kit. Every generic command works here; the GT4-named ones (`hooktest yaw`, `trace-offset`) don't, but `hooktest <address>` does. |
| `../gt4vr/dev/` | The profile validator and the mock PINE server. |

Disc: **SLUS-20015, CRC C7B9F4F7** (NTSC-U retail). PCSX2's patch file for
this CRC confirms the pairing. The ELF's code starts at `0x00100000` (the
widescreen patch rewrites `0x00100FCC`).

## 0. Setup

1. **Profile.** `SLUS-20015.yaml` goes in `Documents\PenguinScreen2\vrprofiles\`.
   On boot the log says `(VR) ProfileDB: SLUS-20015 (CRC C7B9F4F7) uses the user profile …`.
2. **Reloading edits.** The emulator rereads the profile folder at boot and
   whenever a VR setting changes (`VR::UpdateSettings` → `ProfileDB::ReloadIfChanged`);
   nothing watches the folder in between. After saving an edit mid-game, flip
   any VR setting (Stereo off and on) and look for
   `profile folder change detected — reloading` in the log.
3. **Tool.** A separate session folder keeps this game's snapshots apart:
   ```powershell
   function er { py C:\PS2\vrtools\gt4cam.py --session C:\PS2\vrtools\ersession @args }
   er info      # serial SLUS-20015, CRC C7B9F4F7, "memory watch available"
   ```
4. **Headset first.** Connect Virtual Desktop before booting; a boot without it
   runs flat, with no stereo and nothing for `qhist` to measure.
5. **Patches.** PCSX2's patch file for this CRC has two entries, both off by
   default. Decide on them before hunting addresses, because they rewrite
   code words that guards must then avoid:
   - *Widescreen 16:9*: one word, `0x00100FCC` (`lui $at, 0x3F80` → `0x3F40`, the X field-of-view scale).
   - *No-Interlacing*: `0x001CFB44` and `0x001C6EDC` (both nopped). If the image
     shimmers or bobs by a line between frames in the headset, turn this on.
6. **EE rounding.** The game database already sets EE round mode to Nearest
   for this game (it fixes an instant death in Limestone Cave). Leave it.

## 1. Stereo

Start the emulator with the depth histogram armed:

```bat
PenguinScreen2-VR.bat --qhist-live C:\Users\Josh\Documents\PenguinScreen2\qhist
```

The histogram accumulates until flushed, so measure each kind of place on its own:

```powershell
er qhist            # flush whatever has built up (ignore this one)
# walk around indoors for a couple of minutes (a corridor, a room)
er qhist            # -> indoor numbers
# then outdoors, looking at near and far things
er qhist            # -> outdoor numbers
```

If indoors and outdoors want very different numbers, that becomes two `scenes`
rules keyed on something that tells them apart. Things to watch for:

- **Fades or full-screen tints floating or doubled:** `pinUniformQ: true`.
- **Decals or glow lifting off surfaces:** `zDrivenDepth: true`.
- **Doubled or uncomfortable distance:** `separation` is too high.
- **Everything flat:** `convergence` is too high.

## 2. Head look: the plan

This is a first-person game, so the camera *is* the player's eye, and the
player's facing (yaw) and look angle (pitch) are persistent state: they hold
still when you let go of the controls.

- **Route A (try first): write the player's angles.** Like the shipped
  TimeSplitters profile: `writes` with `compose: delta` add each frame's head
  movement to the facing and look angles. The stick still turns you on top,
  and everything the game aims along your view (where you walk, attacks,
  magic) follows your head. With `screen: { follow: head }` the screen stays
  in front of you. Roll has no game state to drive, so it stays off at first.
- **Route B (fallback): code hooks on the view-matrix build**, as in GT4.
  Purely visual: your head would turn the picture but not the player, so
  attacks would go where the body faces. It also needs a free cave, which
  has to pass both `cave-check` and `exec-probe` in this game first.

## 3. Finding the facing angle

Stand somewhere open. Pause the **emulator** each time (its pause hotkey, not
the game's menu):

```powershell
er snap s1        # facing some direction
#   unpause, walk straight forward a few steps WITHOUT turning, pause
er snap s2        # same facing, different place
#   unpause, turn left about 45 degrees on the spot, pause
er snap s3
#   unpause, turn left about 45 degrees more, pause
er snap s4
er find "s1 == s2 and (s1 < s3 < s4 or s1 > s3 > s4)" --snaps s1,s2,s3,s4 --enc f32 --save yawf --show 60
er find "s1 == s2 and (s1 < s3 < s4 or s1 > s3 > s4)" --snaps s1,s2,s3,s4 --enc s16 --save yaws --show 60
er find "s1 == s2 and (s1 < s3 < s4 or s1 > s3 > s4)" --snaps s1,s2,s3,s4 --enc u16 --save yawu --show 60
```

That keeps values that didn't change while walking and moved steadily one way
while turning: the facing angle, and also the sines, cosines and matrix
entries built from it. The units show in the printed values: radians change
by about 0.8 per 45°, degrees by about 45, a 4096-per-turn angle by about
512, a 65536-per-turn angle by about 8192. If a turn crossed the wrap point
(±180°, or 0/360) the angle drops out, so if nothing angle-like survives,
repeat facing another way.

Then the proof: `er poke <address> <its s4 value> --enc <enc>` while facing
the s1 direction. If the view snaps to the s4 direction and the tool says "it
stuck", that's the facing, and Route A will work on it. If the game rewrites
it, it's derived from something else, and the listing below finds the master.

Pitch next, the same way: snap level, walk, look up a little, look up more.

**If the direct search finds nothing**, use the GT4 method, which doesn't care
about units: `er matrices --snaps s1,s3`, then (unpaused, turning slowly)
`er watch <top matrix> --mode w`, then `er func <writer pc> --listing`. The
listing shows where the camera code loads its angles from, and also the
rotation-helper calls Route B would hook.

## 4. A field flag for the guards

Writes must stop in menus and cutscenes. Find a byte that differs:

```powershell
er snap f1 ; er snap m1 ; er snap f2      # field, a menu, field (paused each time)
er find "f1 == f2 != m1" --snaps f1,m1,f2 --enc u8 --save field
```

Narrow with fresh snapshots and `--within field` until a handful remain, then
`er cands field` in each state.

## Leads (unverified)

- `0x001FF894` (u16): player HP, according to an infinite-health code on the
  PCSX2 wiki (region not stated, so it may not be this disc).
- `0x001FF100` (f32, 384.0): a commented-out memory hack in the widescreen
  patch, so probably a projection value.
- Together these suggest the player and camera state are static, somewhere
  around `0x001FF000..0x001FFFFF`. Candidates there are the first to poke.

## Open questions

- How the game looks up and down (which buttons), and whether the view levels
  itself again on its own. A self-centring pitch wants `compose: anchored`
  instead of `delta`.
- 16-bit angles: the loader's `s16.12` encoding saturates instead of wrapping,
  so a 65536-per-turn facing could stick for a frame at ±180°. Only matters
  if that's the unit.

## Status

**Verified:** the profile loads with zero issues in the real loader (validator
built from `pcsx2/VR/VRProfileDB.cpp`), and so does the camera template once
filled in with f32 and s16.12 writes. The only message is the expected
advisory for `separation: 0.010`.

**Not yet:** stereo numbers, the facing and look angles, a field flag.
