# PenguinScreen2 on Windows (experimental)

The Linux/SteamOS build is the validated one. This Windows port uses the same
VR code: OpenXR bound through `XR_KHR_vulkan_enable2` on the Vulkan renderer.
It builds with upstream PCSX2's CMake + MSVC path. What's specific to Windows:

- **Launcher.** `PenguinScreen2-VR.bat` replaces `launch-vr-session.sh`. There's
  no WiVRn pairing step; your runtime (Virtual Desktop, Link or SteamVR) does
  the streaming.
- **Renderer.** A VR launch always uses Vulkan. On Windows, PCSX2's
  "Automatic" renderer means Direct3D, which would otherwise run a VR launch
  flat without saying why.
- **No second-headset seats.** `--vr-seat` / `--vr-cast` drive extra WiVRn
  instances, which are Linux-only.

## What you need

- Windows 10 or 11, x64, a CPU with SSE4.1, and a GPU with current Vulkan drivers.
- An OpenXR runtime with Vulkan support, and a headset:
  - **Virtual Desktop** with its VDXR runtime (Quest 2/3/Pro). VDXR advertises
    `XR_KHR_vulkan_enable2` and cylinder layers.
  - **Meta Quest Link.**
  - **SteamVR.**
- Your own PS2 BIOS, dumped from your own console.

## Install

1. Download the build: the repository's **Actions** tab → **Windows VR build** →
   the latest green run → the `PenguinScreen2-windows-x64-vr-…` artifact.
   Unzip it anywhere, for example `C:\Games\PenguinScreen2`. The build is
   unsigned, so SmartScreen may warn the first time.
2. Run `pcsx2-qt.exe` once, flat, for first-time setup (BIOS, game folders).
   Your settings and data live in `Documents\PenguinScreen2`.
3. In **Settings → VR**, turn on **Enable VR**.

## Pick the OpenXR runtime

- **Virtual Desktop:** Streamer app → Settings → OpenXR Runtime →
  **VirtualDesktopXR**. Connect the headset to the Streamer before you start a
  game, because VR starts when the game boots.
- **Meta Quest Link:** Meta Quest app → Settings → General → OpenXR Runtime →
  set as active.
- **SteamVR:** SteamVR → Settings → OpenXR → set SteamVR as the OpenXR runtime.

Check what the emulator sees with this command:

```bat
PenguinScreen2-VR.bat -Check
```

It prints the active runtime's manifest from the registry or
`XR_RUNTIME_JSON`, then the emulator's own OpenXR report: runtime, headset,
Vulkan graphics binding, and extensions.

## Play

1. Put the headset on and connect (for Virtual Desktop, be in its desktop view).
2. Double-click `PenguinScreen2-VR.bat`, or pass a game:
   `PenguinScreen2-VR.bat "D:\PS2\Gran Turismo 4.iso"`.
3. Boot the game. The log shows `(VR) VR session active. Put on the headset.`
   Virtual Desktop's performance overlay should read `Runtime: VDXR`.

If the game runs flat on your monitor, it was started without `--vr`
(use the .bat), or no runtime is active (`-Check` says which).

If you're in the headset but get a plain screen fixed in the room, with no
depth and no head camera, no VR profile matched this disc. Profiles match by
serial and CRC, and one serial can cover several builds (TimeSplitters
SLUS-20090 has v1.10 and v2.00). The log says so with a line containing
`has a VR profile for CRC(s)`. Copy the shipped profile into your user
profiles folder and add your CRC to its `crcs:` list: stereo and the
head-following screen work on any build. The head camera's addresses may
not, and its guards keep it off when they don't hold.

## Profiles and tools

- **User VR profiles:** `Documents\PenguinScreen2\vrprofiles` (no hyphen).
  A file there overrides the shipped one for the same serial. Don't add copies
  to `bin\resources\vr-profiles`: a second file with the same serial in that
  folder is skipped as a duplicate. When a game boots, the log names the file
  in effect: `(VR) ProfileDB: SLUS-20090 (CRC B4A004F2) uses the user profile ...`.
  Otherwise profiles work exactly as on Linux; see
  `bin/resources/vr-profiles/README.md`.
- **PINE:** turn on **Tools → Show Advanced Settings**, then **Settings →
  Advanced → PINE Settings → Enable**, and restart the emulator. On Windows
  it listens on TCP `127.0.0.1:28011`, not a Unix socket; the log says
  `PINE: listening on 127.0.0.1:28011`. Don't edit `PenguinScreen2.ini` while
  the emulator is open, because it writes its in-memory settings back over the
  file. The VR extensions (memory watch, depth histogram flush) are there too.
- **Depth histogram:** `PenguinScreen2-VR.bat --qhist-live C:\Users\you\qhist`.

## Building from source

You need Visual Studio 2022 or 2026 with the C++ workload, CMake, Ninja, 7-Zip,
and Git for Windows. From an x64 Native Tools prompt in the repo root:

```bat
.github\workflows\scripts\windows\build-dependencies.bat
cmake . -B build "-DCMAKE_PREFIX_PATH=%cd%\deps" -DQT_BUILD=ON -DCMAKE_BUILD_TYPE=Release -DDISABLE_ADVANCE_SIMD=ON -DENABLE_VR=ON -G Ninja
cmake --build build
cmake --install build
```

The dependency script builds Qt and friends into `deps\`. It takes a long time
the first time. The result lands in `bin\`, next to `PenguinScreen2-VR.bat`.

## Not yet verified on Windows hardware

- Frame pacing at the headset's refresh rate through VDXR.
- The curved (cylinder) screen with each runtime.
- Spatial controls (wheel, throttle) with Quest Pro controllers.

Reports welcome. Please include the `(VR)` log lines and `-Check` output.
