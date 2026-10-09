# Fatal Frame II VR profile kit for PenguinScreen2

Goal: good stereo throughout, and head look in the Camera Obscura (the
first-person viewfinder). The third-person exploration cameras get stereo only.

| File | What it is |
|---|---|
| `SLUS-20766.yaml` | The profile (DRAFT 0.1): stereo only, placeholder convergence. |
| `../gt4vr/gt4cam.py` | The PINE helper from the GT4 kit. Every generic command works here; the GT4-named ones (`hooktest yaw`, `trace-offset`) don't, but `hooktest <address>` does. |
| `../gt4vr/dev/` | The profile validator and the mock PINE server. |

Disc: **SLUS-20766, CRC 9A51B627** (NTSC-U retail, version 1.00, ELF entry
`0x00100008`). The Undub builds have the same serial but different CRCs
(1C6C1B71, 1C6C1DB6), so this profile doesn't apply to them.

## 0. Setup

1. **Profile.** `SLUS-20766.yaml` goes in `Documents\PenguinScreen2\vrprofiles\`.
   On boot the log says `(VR) ProfileDB: SLUS-20766 (CRC 9A51B627) uses the user profile …`.
   Stereo needs that line: with "use game profile" on, a game with no profile
   gets no stereo at all.
2. **Reloading edits.** The emulator rereads the profile folder at boot and
   whenever a VR setting changes (`VR::UpdateSettings` → `ProfileDB::ReloadIfChanged`);
   nothing watches the folder in between. After saving an edit mid-game, flip
   any VR setting (Stereo off and on) and look for
   `profile folder change detected — reloading` in the log.
3. **Tool.** A separate session folder keeps FF2's snapshots apart from GT4's:
   ```powershell
   function f2 { py C:\PS2\vrtools\gt4cam.py --session C:\PS2\vrtools\ff2session @args }
   f2 info      # serial SLUS-20766, CRC 9A51B627, "memory watch available"
   ```
4. **Headset first.** Connect Virtual Desktop before booting; a boot without it
   logs `No HMD system available (-35) … Running flat`, and then there's no
   stereo and nothing for `qhist` to measure.
5. **Patches.** The PCSX2 patch file for this CRC (16:9 widescreen, dither off,
   PAL Y-axis) is off by default. Decide on widescreen before hunting camera
   addresses: it rewrites code words at `0x0013A19C/224/28C/304`,
   `0x0014F72C/79C/7A0`, `0x001E5834..0x001E59B4` and `0x0020C1BC`, so guards
   must avoid those, and the viewfinder overlay may come out stretched.

## 1. Stereo

Start the emulator with the depth histogram armed, writing where Claude can
read it too:

```bat
PenguinScreen2-VR.bat --qhist-live C:\Users\Josh\Documents\PenguinScreen2\qhist
```

The histogram accumulates until flushed, so measure each kind of view on its own:

```powershell
f2 qhist            # flush whatever has built up (ignore this one)
# explore for a couple of minutes: a corridor, a room, outdoors in the village
f2 qhist            # -> exploration numbers
# raise the Camera Obscura and look around for a minute, near and far
f2 qhist            # -> viewfinder numbers
```

If the two suggestions differ a lot, the viewfinder gets its own `scenes` rule,
keyed on a "Camera Obscura raised" flag (found in step 3).

Things to watch for:

- **Full-screen filters floating or doubled** (film grain, the viewfinder tint,
  the flashlight cone): `pinUniformQ: true`.
- **Shadows, decals or glow lifting off surfaces:** `zDrivenDepth: true`.
- **Aiming past the capture circle feels like refocusing:** lower `separation`.
  The circle is a flat overlay, so it sits at the screen surface while the ghost
  is behind it. (`hudCollimate` could later push the circle to ghost depth.)

## 2. Is 0x000F1100 free in this game?

GT4's caves lived below its ELF at `0x000F1100..0x000F11FF`. FF2's ELF also
starts at `0x00100000`, but the space below is only usable if this game leaves
it alone. Check it in exploration and in the viewfinder, through a door load if
possible:

```powershell
f2 cave-check 0x000F1100 0x100 --seconds 60
f2 exec-probe 0x000F1100 0x100 --seconds 60
```

`cave-check` prints "all zero" first if the block is empty (`exec-probe` refuses
to run otherwise).

## 3. Camera Obscura: plan

Unlike GT4's bumper cam, the viewfinder's aim is persistent game state: it holds
still when you let go of the stick, and the game aims the shot with it. That
makes a plain data write the first thing to try:

- **Route A: write the aim angles.** If the viewfinder keeps a yaw and a pitch,
  `writes` with `compose: anchored` add your head on top of the stick, and the
  game's own capture logic (what's in the circle, the shot itself) follows your
  head. With `screen: { follow: head }` the circle then stays where you look.
- **Route B: code hooks on the view matrix build**, as in GT4. Purely visual,
  so the game's aim wouldn't follow your head and the circle would point
  somewhere other than where you look. Kept as a fallback.

Finding them, with the GT4 method:

```powershell
# viewfinder raised, aimed one way; pause the emulator (not the game's menu)
f2 snap vf_a
# turn about 90 degrees, pause
f2 snap vf_b
f2 matrices --snaps vf_a,vf_b
# then, unpaused, viewfinder raised, turning slowly:
f2 watch <matrix address> --mode w
f2 func <writer pc> --listing
```

The listing shows where the camera code loads its angles from (`lwc1` off the
camera or player object); those addresses are route A's targets. The same
listing shows the rotation-helper calls for route B. A "viewfinder raised" flag
for guards comes from snapshot searches:

```powershell
f2 snap up1 ; f2 snap down1 ; f2 snap up2     # raised, lowered, raised (paused each time)
f2 find "up1 == up2 != down1" --snaps up1,down1,up2 --enc u8 --save vf
```

## Leads (unverified)

From PCSX2's patch file for this CRC:

- `0x0033760C` (f32, 1.0) and `0x00337610` (f32, 0.875): gameplay X/Y projection scale.
- `0x0018A05C`: `lui $at, 0x3F00` loads a gameplay zoom constant (0.5), so the
  projection setup is nearby.
- `0x004000DC` / `0x004000E0`: X/Y FOV at runtime, according to a commented-out
  entry.

`watch 0x004000DC --mode r` is a second way into the camera code if `matrices`
returns too many candidates.

## Status

**Verified:** the profile loads with zero issues in the real loader (validator
built from `pcsx2/VR/VRProfileDB.cpp`); the only message is the expected
advisory for `separation: 0.010`.

**Not yet:** stereo numbers, whether `0x000F1100` is free, the camera code.
