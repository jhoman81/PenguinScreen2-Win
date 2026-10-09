# Eternal Ring VR profile kit for PenguinScreen2

Goal: good stereo throughout, and head look in the first-person view, driven
through the game's own camera angles so that walking, attacks and spells
follow your head.

| File | What it is |
|---|---|
| `SLUS-20015.yaml` | The profile (DRAFT 0.2): stereo with a placeholder convergence, and head yaw, pitch and roll through the game's own camera angles. |
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

## 3. Field of view

For head look to feel right, the virtual screen must cover the same angle as
the game's field of view, or the world swims against your head. The stock
projection gives 2·atan(320/512) ≈ **64°** across a 640-pixel image, so the
profile sets `screen: { follow: head, arc: 64 }`. With the widescreen patch
(x focal length 384) it's ≈ 80°. For a wider game view, the profile has
commented constant writes to `0x001FF100` / `0x001FF114` (untested: check
first with `er poke 0x1FF100 320 --hold 5` that the game doesn't rewrite them).

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
- **Anything swimming** when you turn: the `screen.arc` doesn't match the game's view.

Things that may need work: menus and cutscenes also take your head yaw (a
field-only guard needs a flag from section 6); the game may cull geometry it
thinks is off-screen when you look steeply up or down.

## 6. A field flag for the guards

Not needed yet; useful if head look in menus or cutscenes gets in the way:

```powershell
er snap f1 ; er snap m1 ; er snap f2      # field, a menu, field (paused each time)
er find "f1 == f2 != m1" --snaps f1,m1,f2 --enc u8 --save field
```

Narrow with fresh snapshots and `--within field` until a handful remain, then
`er cands field` in each state.

## Status

**Verified:** the profile (DRAFT 0.2) loads with zero issues in the real
loader (validator built from `pcsx2/VR/VRProfileDB.cpp`); the only message is
the separation advisory. The camera record, its update and the view build
were read from the game's own code in the snapshots.

**Not yet:** the pokes in section 4, the head look in the headset, stereo
numbers, a field flag.
