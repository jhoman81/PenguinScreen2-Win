# Eternal Ring VR profile kit for PenguinScreen2

Goal: good stereo throughout, and head look in the first-person view, driven
through the game's own camera angles so that walking, attacks and spells
follow your head.

| File | What it is |
|---|---|
| `SLUS-20015.yaml` | The profile (DRAFT 0.4): stereo with a placeholder convergence, head yaw, pitch and roll through the game's own camera angles, and a flat, lag-matched follow-head screen sized to the game's view. |
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
   function er { py C:\PS2\vrtools\gt4cam.py --session $HOME\Documents\PenguinScreen2\ersession @args }
   er info      # serial SLUS-20015, CRC C7B9F4F7, "memory watch available"
   ```
4. **Headset first.** Connect Virtual Desktop before booting; a boot without it
   runs flat, with no stereo and nothing for `qhist` to measure.
5. **Patches.** PCSX2's patch file for this CRC has two entries, both off by
   default. Decide on them before hunting addresses, because they rewrite
   code words that guards must then avoid:
   - *Widescreen 16:9*: one word, `0x00100FCC` (`lui $at, 0x3F80` → `0x3F40`, the X field-of-view scale).
     With it on, set the profile's `screen.arc` to 80 (section 3).
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

## 2. How the camera works (from the code)

Found from the turning snapshots and a read of the camera code in them:

| Address | What it is |
|---|---|
| `0x001FF340` | **Camera record, the master copy.** Position x, y, z, 1 (y points down; the eye is 150 above the feet), then at `+0x10` rotation **pitch, yaw, roll**, 1, in radians. |
| `0x001FF7A0` | Player record: a copy of the camera record made at the start of each update (last frame's pose), with y moved down to the feet. HP is at `0x001FF894` (two u16s, current and max). |
| `0x001FF400` | Look rates (pitch, yaw), added to the angles each frame and damped. |
| `0x001FF180` | View matrix (world to camera). `0x001FF390` is the camera's own rotation (its transpose). |
| `0x001FF100` | Projection: x focal length 512, y 256 (the game renders fields). `0x001FF200` is projection times view. |

The update at `0x00119FE0` copies camera to player, adds the rates, clamps
pitch to ±1.0 rad (`0x0011AFFC..0x0011B028`), wraps yaw to ±π, then builds
the view at `0x0011B730`: `RotY(π − yaw)`, then `RotX(−pitch)`, then
`RotZ(roll)`, using what look like Sony's libvu0 rotation helpers
(`0x001D7820`, `0x001D7778`, `0x001D76D0`). Yaw first, pitch about the
camera's own axis, roll about the view axis: the right order for a head pose.

So **the engine already has pitch and roll**. The game's own pitch input
only works when a flag bit (`0x08` at `0x001FF454`) is set, which may be why
it seems you can't look up. Nothing needs code patches: all three axes are
plain `writes` to the camera record, and because the game aims along the
camera, walking, attacks and spells follow your head. The sky code at
`0x0010DD2C` even shifts the backdrop by `tan(pitch)`, so looking up and down
should draw properly.

Signs, read from that code: yaw grows when turning right (the left turns in
the snapshots took it from −2.88 to 2.03 to 0.03 rad, each step down), so
`axisSign: -1`. Pitch: +rx tips the view up, so `+1`. Roll: +rz turns the
picture clockwise, as a left head tilt does, so `+1`.

## 3. Field of view, and the Widescreen patch

For head look to feel right, every pixel on the virtual screen has to sit
where the game's camera ray for it points; then a head turn moves the whole
scene across the screen at exactly head speed. The game's picture is a
perspective projection, so that takes a **flat** screen sized to the game's
field of view. A curved screen only matches at the centre and the edges: on
a 64° curve the middle of the scene moves at 0.89× head speed and the edges
at 1.24× (at 80°, 0.84× and 1.42×), a swim that grows toward the edges.

The game sets its projection once, at `0x00100FB0`, with what looks like
libvu0's `sceVu0ViewScreenMatrix` into `0x001FF100`: focal length 512
horizontally, 256 vertically (it renders 224-line fields), centred at
2048,2048 on a 640×224 viewport (scissor 0..639 × 0..223). So the view is
2·atan(320/512) ≈ **64°** wide. The Widescreen 16:9 patch changes the 1.0 at
`0x00100FCC` to 0.75, which scales the horizontal focal length to 384:
2·atan(320/384) ≈ **80°**. (The same register is also the near-plane
argument, so that drops to 0.75 too, harmlessly.)

A flat screen at distance *d* matches when its half width is *d* × tan(half
angle): at 2.0 m, 2.5 m wide at 4:3 and 3.33 m at 16:9. The emulator sets a
flat screen's width from `height` × its aspect ratio setting, which the
patch switches to 16:9, and both cases come out at `height: 1.875`. So the
profile's

```yaml
screen: { follow: head, arc: 0, distance: 2.0, height: 1.875, lagMs: 60 }
```

is right with the patch on or off, on an emulator build from `8184198` on.
Builds before that sized the VR screen from the
aspect *setting* alone, which stays "Auto 4:3/3:2" when the patch asks for
16:9, so the 80° picture was squeezed onto a 4:3 screen: the middle of the
scene then moves at 0.75× head speed and visibly warps as you turn. On an
older build, set this game's Aspect Ratio to 16:9 (game properties →
Graphics → Display) whenever the patch is on. Vertically the game's picture is 7%
squarer than the 4:3 frame shows it (on a TV too), so looking up and down
moves the scene 7% faster than your head at this size; `height: 1.75` makes
pitch exact and yaw 7% slow instead.

**Is the Widescreen patch worth it?** In VR, probably: 80° instead of 64°
is a lot more peripheral view, and with the flat screen it costs no comfort.
What to watch for:

- **The HUD stretches** 4:3 → 16:9 (the patch only changes the 3D projection).
- **Culling at the sides.** If the game culls against its old 4:3 view,
  things would pop in at the left and right edges as you turn. The second
  matrix the game builds there (`0x001FF140`, focal length 20.48, about 25×
  wider than the screen) isn't scaled by the patch, but that looks like a
  guard-band test, not the view. Look along the edges while turning.

`0x001FF100` / `0x001FF114` could also be written as constants for an even
wider view (the projection is only set at load). Untested; check with
`er poke 0x1FF100 320 --hold 5` that the game doesn't rewrite it, and resize
the screen to match.

## 4. Prove the addresses (two minutes, no headset needed)

In the field, unpaused, standing still, with the profile **not** installed
(or VR off):

```powershell
er poke 0x1FF350 0.4 --hold 5    # pitch: the view should tip UP about 23 degrees for 5 s
er poke 0x1FF358 0.3 --hold 5    # roll: the horizon should turn CLOCKWISE about 17 degrees
er poke 0x1FF354 0               # yaw: the view should jump to a new heading and stay there
```

`--hold` puts the old value back afterwards and reports how often the game
overwrote it; for pitch and roll that should be 0%. If one tips the other
way, flip that write's `axisSign`. If roll gets overwritten, comment out the
roll write (the game resets it somewhere) and tell Claude.

## 5. In the headset

Install `SLUS-20015.yaml`, connect Virtual Desktop **before** booting, and
look for `CameraDriver: ARMED` in the log once you're in the field. Recenter
facing forward and level (the hotkey, or hold both thumbsticks). Then:

- **Turning your head left turns the view left**, and the stick still turns you.
- **Looking up looks up**, and stops at 57°, which is the game's own limit.
- **Tilting your head left tilts the horizon clockwise** in the picture, so it stays level in the room.
- **The scene stays put in the room while you turn** (section 5a). If it
  still drags with your head or swings back, tune `screen.lagMs`.

Things that may need work: menus and cutscenes also take your head yaw (a
field-only guard needs a flag from section 6); the game may cull geometry it
thinks is off-screen when you look steeply up or down.

## 5a. Comfort: shimmer and swim

Two things showed up in the first headset test (DRAFT 0.2).

**Shimmer: turn on the No-Interlacing patch.** The game renders 640×224
fields and nudges every other one down by half a line (`daddiu $v0, $v0, 8`
at `0x001CFB44` adds 0.5 px to the GS Y offset; the snapshots hold both
offsets, 1936.0 and 1936.5). On a TV that's interlacing; on a big VR screen
the whole picture bobs by a line every frame. The patch nops that
instruction. Game properties → Patches → *No-Interlacing*. It changes no word
the profile guards.

**Swim: `screen.lagMs` (needs an emulator build with it).** A follow-head
screen moves with your head at once, but the game's picture shows your head
as it was when the camera driver wrote it into the game: the game picks the
angles up on its next update, draws at about 30 fps, and the frame then
queues through the GS thread and the compositor. That's roughly 50–80 ms. So
every head turn drags the scene along with the screen until the game catches
up, and small head movements make it wobble; smoothing the head input would
only add more lag. Instead, the compositor now remembers the head
orientations it located and holds the screen where the head was `lagMs` ago,
once per new game frame, on the axes the camera block drives. The scene then
stays put in the room, and the screen's edges trail your head a little
during fast turns. The log says
`(VR) Screen: lag-matched follow-head screen ON (60 ms; axes yaw pitch roll; …)`
when it engages.

Tune it in steps of 10 (edit, then flip a VR setting to reload):

- the scene still **drags with** your head and catches up → raise `lagMs`;
- it **overshoots** (swings against your head, then settles) → lower it;
- right: turn your head at an even speed and the walls stay still.

The emulator side: `screen.lagMs` in `VRProfileDB`, the driven axes and
recenter reference published by `CameraDriver::GetHeadLookAxes()`, and
`LagMatchedScreenRotation()` in `XRCompositor.cpp`, which takes a "new game
frame" hint from `GSRenderer::VSync` (the privileged-register write flag).

## 6. A field flag for the guards

Not needed yet; useful if head look in menus or cutscenes gets in the way:

```powershell
er snap f1 ; er snap m1 ; er snap f2      # field, a menu, field (paused each time)
er find "f1 == f2 != m1" --snaps f1,m1,f2 --enc u8 --save field
```

Narrow with fresh snapshots and `--within field` until a handful remain, then
`er cands field` in each state.

## Status

**Tested in the headset:** head yaw, pitch and roll work (DRAFT 0.2); the
No-Interlacing patch removes the shimmer.

**Verified:** the profile (DRAFT 0.4) loads with zero issues in the real
loader (validator built from `pcsx2/VR/VRProfileDB.cpp`); the only message is
the separation advisory. The camera record, its update and the view build
were read from the game's own code in the snapshots.

The lag-matching math (history lookup, per-axis matching, holding the pose
between game frames) was checked in a standalone test, and the changed
emulator sources pass a syntax check against the repo's headers.

**Not yet:** `lagMs` in the headset, stereo numbers, a field flag.
