# Gran Turismo 4 VR profile kit for PenguinScreen2

Goal: real stereo depth plus a head-driven camera in GT4's in-car view.

| File | What it is |
|---|---|
| `SCUS-97328.yaml` | The profile (DRAFT 0.5): stereo, head look in the bumper cam (three code hooks), a seat offset and positional tracking. |
| `gt4cam.py` | PINE helper that finds the holes: memory search, memory watch with disassembly, code-hook suggestions and tests, cave checks, depth-histogram tuning. Python 3.8+, standard library only. Most commands work on any game. |
| `dev/build-validator.sh` | Builds `validate`, PenguinScreen2's real profile loader as a command-line checker (Linux). From the repo root: `tools/gt4vr/dev/build/validate bin/resources <your profile folder> SERIAL CRC`. |
| `dev/mock_pine.py` | A fake emulator speaking PINE and the VR extensions, for testing `gt4cam.py` changes without a PS2 (Linux). |

One thing to know up front: **retail GT4 has no interior cockpit.** In-car
interiors first appeared in GT5 Prologue. The closest retail view is the
bumper cam, and that's what this profile targets. See "Spec II" at the end
for an interior option.

## 0. Setup

1. **Profile.** Copy `SCUS-97328.yaml` into your user profiles folder:
   `Documents\PenguinScreen2\vrprofiles\` on Windows (no hyphen, and not
   `bin\resources\vr-profiles`), or
   `~/.var/app/org.penguinvr.penguinscreen2/config/PenguinScreen2/vrprofiles/`
   on Linux. When GT4 boots, the log (`Documents\PenguinScreen2\logs\emulog.txt`)
   should say `(VR) ProfileDB: SCUS-97328 (CRC 77E61C8A) uses the user profile …`.
   If it says `has a VR profile for CRC(s) 77E61C8A, but this disc is …`
   instead, your disc is a different build: tell me the CRC, because the
   GT4-specific addresses below would differ. Edits hot-reload; the log
   prints `profile folder change detected — reloading`.
2. **GT4 settings.** In GT4's Options → Screen, choose Progressive (480p) and
   16:9. Interlaced 480i confuses everything downstream. Optionally, in the
   emulator's game properties → Patches, enable "Autoboot in 480p" and
   "Widescreen 16:9" (the text fix).
3. **PINE.** Turn on **Tools → Show Advanced Settings**, then **Settings →
   Advanced → PINE Settings → Enable** (slot 28011), and restart the emulator.
   If you edit `PenguinScreen2.ini` instead (`[EmuCore]`, `EnablePINE = true`),
   do it with the emulator closed: it writes its in-memory settings back over
   the file. Newer builds log `PINE: listening on 127.0.0.1:28011`.
4. **Running the tool (Windows).** Install Python 3 from python.org. On
   Windows the tool talks to PINE over TCP `127.0.0.1:28011` automatically.
   Put `gt4cam.py` in a folder such as `C:\PS2\vrtools`, then define a shortcut
   in PowerShell:
   ```powershell
   function g4 { py C:\PS2\vrtools\gt4cam.py --session C:\PS2\vrtools\session @args }
   g4 info
   ```
   `g4 info` should report serial `SCUS-97328`, CRC `77E61C8A`, "camera-offset
   site matches", "cave … is all zero", and "memory watch available".

   **On Linux (flatpak)** the PINE socket lives inside the sandbox, so run the
   tool there: put it in `~/PS2-Games/vrtools/` and alias
   `g4='flatpak enter org.penguinvr.penguinscreen2 python3 ~/PS2-Games/vrtools/gt4cam.py --session ~/PS2-Games/vrtools/session'`.
   `flatpak enter` needs unprivileged user namespaces, otherwise `sudo -E`.

Workflow tips: `snap` while the game is **paused** for consistent snapshots.
`watch` and `trace-offset` need the game **running**.

## 1. Stereo tuning (about 15 minutes)

The depth histogram has to be armed at launch. On Windows, create a folder
and start the emulator with it:
`PenguinScreen2-VR.bat --qhist-live C:\PS2\qhist`. On Linux, copy
`launch-vr-session.sh` and end its last `exec flatpak run ... --vr` line with
`--vr --qhist-live "$HOME/PS2-Games/qhist"`.

Drive a lap in bumper view and run `g4 qhist`. It flushes the histogram and
prints a coverage-by-depth chart, the w percentiles, a draw census, and
ready-to-paste `separation` / `convergence` values. If the scene spans many
octaves of depth, it also prints a `map: log` alternative.

- `convergence` is the 5th-percentile depth. Anything nearer sits on the
  screen surface, and everything else goes into it.
- `separation` defaults to 1° at infinity, so the jump from the flat HUD
  (tach, map) to the horizon stays within the usual comfort guideline. The
  loader prints an advisory about this step for any value above about 0.0033.
  The shipped Ace Combat 5 profile gets the same advisory, so treat it as
  information.
- **Paint reflections or decals floating off the bodywork** means a multi-pass
  Q mismatch. Set `zDrivenDepth: true`.
- **Doubled or uncomfortable horizon** means `separation` is too high.
- **Everything flat** means `convergence` is too high. Lower it.

## 2. Guards: in-race flag and view index

The profile's code patches don't need these: they're guarded by GT4's own code
words, and the hooked calls only run while the camera does. You only need
data guards like these for plain `writes` (such as positional parallax).

Camera writes must be gated, or they land in random memory during menus.
To find a byte that changes with the view (cycle views with SELECT):

```sh
# pause in bumper view, snap; switch view, pause, snap; switch back, pause, snap
g4 snap vb1 ; g4 snap vc1 ; g4 snap vb2
g4 find "vb1 == vb2 != vc1" --snaps vb1,vc1,vb2 --enc u8 --save view
# repeat with fresh snaps, narrowing with --within view, until a handful remain
g4 cands view          # then watch the live values while pressing SELECT
```

Find the in-race flag the same way, using a menu snapshot against race snapshots.
Restart the race and confirm the address hasn't moved. If it moves, use
`g4 ptrscan --snaps a,b --targets addrA,addrB` to find a static pointer, then
use `base: { pointer: ... }` with `relative: true` writes.

Note that PenguinScreen2's pointer walk rejects pointer values at or above
32 MB (kseg0 `0x8…`, uncached `0x2…`). `ptrscan` reports those separately.

## 3. Head look: what was tried

**Route A (GT4's own look-back) is a dead end on 1.01.** L1 doesn't set a
persistent look angle: it sets flags at camera+0x26B / +0x269, and the camera
code adds π to the yaw every frame while they're set. `find --preset lookback`
and `bisect` found nothing writable, which matches.

**Route B (code hooks) is what the profile uses.** `matrices` + `watch` traced
the view matrix (camera object + 0x100) back to GT4's camera build function at
`0x0037B020`. It builds the view as a chain of VU0 matrix calls:

| Order | Call | What it is |
|---|---|---|
| 1 | `0x0037B0D4` / `0x0037B0EC` RotX / RotY | probably engine vibration (noise × amplitude) |
| 2 | `0x0037B1B4` / `1BC` / `1C4` RotZ / RotX / RotY | **camera sway** (likely the G-force lean), clamped ±90° |
| 3 | `0x0037B284` translate | sway offset |
| 4 | `0x0037B30C` translate | the camera's offset from the car (`trace-offset`) |
| 5 | `0x0037B448` / `450` / `458` RotZ / RotX / RotY | the car's orientation |
| 6 | `0x0037B4DC` translate | the car's position |

The helpers take degrees in `$f12` (RotZ `0x004A79D8`, RotX `0x004A7988`,
RotY `0x004A79B0`). The sway rotations come before the camera offset, so they
turn the view about the driver's eye; that's the head-look hook. The car
rotations come after it, so hooking those would swing the camera around the
car instead. Steps 1-3 only run in camera modes 0 and 25, and the bumper cam
should be one of them; `hooktest` settles that.

## 4. Head look: prove the hooks, then turn them on

Use DRAFT 0.1 of the profile (or turn off **Head-Tracked Camera** in the VR
settings) while testing, so the emulator isn't hooking the same calls.

In a race, in the **bumper view**, unpaused, run:

```powershell
g4 hooktest yaw          # sweeps the view +/-25 deg left/right for 12 s
g4 hooktest pitch        # same, up/down
g4 hooktest roll         # same, tilting the horizon
```

Each one swaps the `jal` for a jump to a temporary 5-word cave (the same one
PenguinScreen2 builds), sweeps the value added to `$f12`, then puts the
original instruction back. Ctrl+C stops it early and still restores the code.
It also tells you whether the hooked call actually ran ("site running: yes").
What to look for:

- **The view swings about the driver's eye.** That site is good.
- **"never ran"**. The bumper cam isn't camera mode 0 or 25. Try the chase
  view to confirm the tool works, then send me the output.
- **The view orbits around the car.** That's a car-space rotation, so my
  reading of the call order is wrong. Send me the output.

Then find each sign. Run `g4 hooktest yaw --hold 20` (and the same for
`pitch` and `roll`) and follow the "Sign:" line it prints. OpenXR's head yaw
is positive turning **left**, pitch is positive looking **up**, and roll is
positive tilting **left**. Set `axisSign: -1` on any hook whose test went the
other way.

Then install the profile (`SCUS-97328.yaml`). Its camera block:

- **guards** on untouched code words next to the hooks, so nothing is
  patched unless this is GT4 1.01's code;
- **silence** patches that zero GT4's own sway, so the horizon doesn't lean
  under a level head (comment them out to get the lean back);
- three **codeHooks** that add your head yaw, pitch and roll to those calls;
- `screen: { follow: head }` and `tier: immersive`.

When it arms, the log shows `CameraDriver: ARMED`. Recenter with the "VR:
Recenter Head Camera" hotkey while facing forward.

- **One axis goes the wrong way:** flip that hook's `axisSign`.
- **Looking 60° left and then up rolls the view instead of tipping it:** the
  helpers multiply in the other order. Tell me.
- **The view buzzes:** uncomment the two vibration `silence` lines.
- **Chase cam:** the game ignores your head there (no sway block), and the
  screen just stays in front of you.

The patches' code ("caves") and their value words live at `0x000F1100..0x000F11CB`,
below the game (the profile has a map). Before installing the profile, check that block with the head
camera off, in a race:

```powershell
g4 cave-check 0x000F1100 0x100 --seconds 60
g4 exec-probe 0x000F1100 0x100 --seconds 60
```

`cave-check` watches the whole block for game reads and writes, and samples it
for any change (DMA included). It can't see code *running* there, which is
what `exec-probe` is for: it fills the block with an opcode the emulator skips
like a nop but logs when it compiles it, with each word's own address in its
low bits, then restores the zeros and reads `emulog.txt` to see which words ran.

That's the trap DRAFT 0.2 fell into. `0x00495600+` is all zero and the game
never reads or writes it, but GT4 executes it (a run of nops) during races. The
emulator log showed the head angles being executed as instructions, until a
roll of 0.0096° decoded as `lui $sp, 0x224F` and the stack pointer went with it.

Emulator builds from commit `ca60532` on `windows-port` also check every
frame: if the game writes over a cave or value word, the log says so and that
hook is removed instead of the game crashing on it. (That check can't catch
code running there either; only `exec-probe` can.)

## 5. Seat position and positional tracking

The bumper cam sits on the car's centre line, low. Right after the sway
rotations, GT4 moves the eye by a small G-force bob: `jal 0x004A7844`
(translate) at `0x0037B284`, with x/y/z in `$f12/$f13/$f14`. That happens in
the car's frame, so adding to those registers moves the eye around inside the
car. A code hook only adds to one register, so the profile writes a 14-word cave
by hand with `silence` patches (which just mean "write this word while armed,
put the old one back after") and points that `jal` at it. The cave adds two
things per axis:

- **Seat offset:** three `writes` with `source: constant` and a `bias`, in
  what look like metres. GT4's translate moves the world rather than the
  camera, so the signs are inverted: negative y raises you (-0.30 is about
  right), positive x moves you left and positive z forward. Edit the numbers
  while racing; the profile hot-reloads when you save. One value covers every
  car for now, so right- and left-hand-drive cars want opposite x.
- **Positional tracking:** your head position, clamped to ±0.3 m per axis.
  It's measured from where the headset's own recenter put it (hold the Oculus
  button). The emulator's recenter hotkey only resets rotation. All three
  `axisSign`s are -1 for the same inverted-world reason; flip one if leaning
  moves the view the wrong way, or set its `scale: 0` to turn it off.

Commented-out `silence` lines offer a **horizon lock** (drop the car's body
roll and pitch from the view, so the horizon only tilts with your head) and
zero GT4's own G-force bob, the same way the sway rotations are zeroed.

The roll hook is `axisSign: -1` too. Yaw and pitch were measured at -1, and
the same conventions give -1 for roll. With the wrong roll sign, the small
head tilts you never notice roll the horizon the wrong way, and twice as far.

Changes to the `silence` list itself (unlike `writes`) only apply when the
head camera re-arms, so pause and unpause after editing them.

`g4 trace-offset --seconds 6` (press SELECT through the views meanwhile)
still works. It records which offset vector the camera code reads at
`0x0037B304`, if you want the per-view numbers.

## 6. Comfort notes

- **Camera roll.** Retail GT4 leans the bumper cam in corners, and a horizon
  tilting under a level head is a classic nausea trigger. The profile zeroes
  that sway with `silence` patches. The car's own body roll still tilts the
  view, as it would from a real seat.
- **HUD.** GT4's HUD is UV/sprite drawing, so it stays on the screen surface.
  That's correct for text. `hudCollimate` can push small sprites deeper, but
  rule it by size, or full-screen post-process passes move too.
- **Steering wheel.** A virtual wheel you grab with the controllers is in the
  profile, commented out. It feeds the DS2 left stick. A USB Driving Force Pro
  target would allow 900°, but in 1.0-rc2 USB binds only drive one half of an
  axis.

## Spec II (interior view)

The Spec II mod adds roof, bonnet, and interior (silhouette) cameras and
removes the bumper-cam lean. It's built on the Online Public Beta: serial
SCUS-97436, CRC 4CE521F2 after patching.

Every address differs from retail, so make a separate `SCUS-97436.yaml` with
`crcs: [0x4CE521F2]`. The generic commands work unchanged; `trace-offset` is
retail-only.

## What's verified and what isn't

**Verified:**

- The profile loads with zero issues in PenguinScreen2's own loader (v1.0-rc2
  `VRProfileDB.cpp`, built standalone against rapidyaml 0.12.1, the version
  the flatpak pins). Every template block also parses once filled in.
- The disassembler agrees with LLVM's MIPS backend, except where the R5900
  genuinely differs (`sqrt.s` reads `ft`; `c.lt.s` is function 52), which
  PCSX2's FPU code confirms.
- The cave and hook encodings match LLVM's assembler.
- Every command ran end-to-end against a mock server speaking PenguinScreen2's
  PINE wire format, including the memory watch and qhist extensions.

- `hooktest` and the profile's caves use the exact encodings PenguinScreen2's
  CameraDriver self-test checks, and `hooktest` ran end-to-end against the mock
  (sweep, hold, Ctrl+C restore, leftover-trampoline recovery, refusals).

**Unverified until you run GT4:**

- Whether the bumper cam runs the sway block (camera mode 0 or 25), and the
  sign of each axis. `hooktest` answers both.
- That the sway calls compose in eye space in the order the profile assumes.
- Whether `0x000F1100..0x000F11FF` stays free (not read, written or executed) in every part of the game.
- The stereo numbers.
