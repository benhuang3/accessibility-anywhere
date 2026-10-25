# Tier D — GDI text-draw hook (classic-app text enlargement)

Status: **design only, not built.** Windows-only. Scoped demo feature, not a general capability.

## What this is

Ally's text-enlargement ladder has four tiers (see `ally/tools/ui_text_tools.py`):

| Tier | Reaches | Mechanism | State |
|---|---|---|---|
| A | Chrome | extension DOM edit | built |
| A-native | settable fields | UI Automation ValuePattern | built (`win_uia.py`) |
| B | any app | reading-card overlay | blocked on overlay |
| C | any app (pixels) | Magnification API | built |
| **D** | **classic GDI apps** | **in-process text-draw hook** | **this doc** |

Tiers A–C never read a classic app's text *as text*. A Win32 app built on GDI (Notepad, older dialogs, many utilities) draws its labels by calling `ExtTextOutW`/`TextOutW`/`DrawTextW` with the actual string in hand. Tier D is the one path that can see that string and enlarge or reword it at the moment it is drawn, inside the one program the user asked Ally to help with.

This is the same documented technique accessibility software has used for decades: a screen magnifier or reader cooperates with another app's rendering by intercepting the text API. We are not inventing tradecraft; we are using Microsoft's own extension points (`SetWindowsHookEx`, Detours/MinHook) the way they are meant to be used.

## Scope and non-goals (read this first)

This feature is worth building **only** as a narrow, visible, consented demo on **one classic GDI app the user launches** (Notepad is the reference target). It is the "technical depth" showcase, isolated so that if it fails, Tiers A–C still carry the demo.

**In scope**
- The user starts Ally and starts the target app themselves.
- Ally injects a local, first-party DLL into that process to enlarge (and optionally reword) text it draws, for readability.
- Everything is observable: the user sees the text change, and can turn it off.

**Explicitly NOT in scope** (these are the things that make injection malware instead of accessibility, and we do none of them):
- No stealth. We use the standard, visible `LoadLibrary`-via-`CreateRemoteThread` path and a plainly named, unobfuscated DLL. No manual mapping, no process hollowing, no attempts to hide the module or evade antivirus.
- No persistence. The hook lives only while Ally and the target app are running. Nothing is installed, auto-started, or left behind.
- No data exfiltration. The hook reads the string only to re-render it larger on screen; it is never logged, stored, or sent anywhere.
- No targeting of other people's machines or processes the user did not launch. Ally only ever touches apps on the user's own computer, at the user's request.
- No injection into browsers, Office, security software, or any process that handles credentials or payments. The allowlist is one classic GDI app.

If antivirus flags it, that is a signal to keep the feature scoped and consented, not something to work around. The honest demo framing is: "this is deep accessibility engineering on a classic app; here's exactly what it does and how to turn it off."

## Why GDI only

Modern UI stacks render text differently, and the hook has nothing to grab:

- **DirectWrite/Direct2D** (Office, some native apps): text becomes glyph runs (indices + positions), not characters. Resizing is possible; reading back the string is lossy per-font. Out of scope.
- **Skia on GPU** (Chrome, Electron): text is composited into a GPU texture before any hookable call. The string is gone. Use Tier A (the extension) for Chrome; there is no rendering hook worth attempting here.

So Tier D is deliberately limited to apps that still draw through GDI. That is a shrinking set, which is exactly why it is a showcase, not a product pillar.

## Architecture

Three parts: a small native DLL, an injector, and a Python control surface in the Windows adapter.

```
 Ally agent (Python)
   └─ WindowsPlatform.enlarge_gdi_text(pid, factor)
        └─ injector.exe / ctypes CreateRemoteThread   ── loads ──►  ally_texthook.dll
                                                                        │ (inside target process)
                                                            hooks ExtTextOutW / DrawTextW
                                                            scales the font, re-draws larger
```

### 1. The hook DLL (`ally_texthook.dll`, C/C++)

- On `DLL_PROCESS_ATTACH`, install a detour on the GDI text entry points using **MinHook** (MIT-licensed, minimal). Primary target: `ExtTextOutW` in `gdi32.dll`. Secondary: `DrawTextW`, `TextOutW`.
- The detour runs in the target's own draw call. It receives the device context (`HDC`), the string, and the rectangle. To enlarge: select a larger font into the DC (scale the current `HFONT`'s `lfHeight` by `factor`) before calling the original, then restore. To reword (optional, later): substitute the string before the original call.
- Keep the detour tiny, reentrancy-safe, and failure-tolerant: on any error, call the original unchanged. A hook that throws must never crash the host app.
- A single exported `set_factor(float)` and `unhook()` so Ally can adjust or cleanly remove it. `DLL_PROCESS_DETACH` must fully uninstall the detour.

### 2. The injector (ctypes, in Python, or a tiny `injector.exe`)

Standard documented sequence, no stealth:
1. Resolve the target PID (Ally launched the app, so it has the handle; otherwise match the main-window title from an allowlist).
2. `OpenProcess(PROCESS_CREATE_THREAD | VM_OPERATION | VM_WRITE | VM_READ, …)`.
3. `VirtualAllocEx` + `WriteProcessMemory` to place the DLL's full path.
4. `CreateRemoteThread` with `LoadLibraryW` as the start routine.
5. Keep the handle so `unhook()` can be called and the DLL freed.

This is the textbook `LoadLibrary` injection every Windows debugger and accessibility tool uses. It is visible in Process Explorer, which is fine — visibility is the point.

Prefer doing this from Python via `ctypes.windll.kernel32` to match the repo's style (`windows.py` already drives Win32 through `ctypes`), falling back to a small compiled `injector.exe` only if the ctypes path is fiddly for the 64-bit/arch match.

### 3. Python control surface

Add to `WindowsPlatform` (guarded, Windows-only, behind an allowlist and a config flag default-off):

```
def enlarge_gdi_text(self, factor: float, pid: int | None = None) -> str: ...
def clear_gdi_text(self) -> None: ...
```

Wire a new Tier-D branch in `enlarge_text`'s router only when: the front app is on the GDI allowlist, the config flag `tools.gdi_text_hook = true`, and the user consented once. Otherwise the router behaves exactly as today.

## Hard constraints

- **Architecture match.** A 64-bit DLL injects only into a 64-bit process (and 32→32). Notepad on modern Windows is 64-bit; ship an x64 DLL. Mismatched arch injection fails cleanly and must be detected up front.
- **One process at a time** for the demo. No scanning, no bulk injection.
- **DC state hygiene.** Always restore the original `HFONT` and DC state after re-drawing, or the target app's own rendering corrupts.
- **Reentrancy.** `ExtTextOutW` can be called from the detour's own re-draw; guard with a thread-local "in hook" flag so it doesn't recurse.
- **Clean teardown.** `unhook()` removes the detour and frees the DLL; Ally's shutdown path calls it. Leaving a detour installed in a still-running app is a bug.

## Build and test plan (Windows VM)

1. Build `ally_texthook.dll` x64 with MinHook statically linked. Keep the source in `native/texthook/` with a one-command build (MSVC `cl` or CMake).
2. Manual smoke: launch Notepad, type a line, run the injector by PID, confirm the drawn text enlarges and that Notepad stays responsive; call `unhook`, confirm it returns to normal and the DLL unloads.
3. Stability: resize, scroll, open a file dialog; confirm no crash and correct DC restoration.
4. Negativetests: wrong arch → clean failure; target not on allowlist → router skips Tier D; flag off → router skips Tier D.
5. Only after that, wire the router branch and add a `doctor` check reporting whether the DLL is present and whether the flag is on.

Everything above is written and reviewed off-Windows; none of it runs until the VM, same as `win_uia.py`.

## Effort and recommendation

This is the largest and least certain tier: a native DLL, a C toolchain, and injection mechanics, all validated only on the VM, reaching a shrinking set of apps. Build it **last**, after Tiers A–C are solid and the overlay (Tier B) lands. Treat it as a 2-to-4-hour spike that either produces a clean Notepad demo or gets cut without affecting anything else. Its entire value is showing judges one real in-memory text transformation in a classic app; keep it that scoped.
