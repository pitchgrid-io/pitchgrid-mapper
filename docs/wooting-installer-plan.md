# Wooting Bridge — Installer Plan

## Goal

Ship pitchgrid-mapper as a single signed installer per platform such that a
fresh user can:

1. Download one file (DMG / EXE / DEB / AppImage).
2. Open it.
3. Plug in their Wooting and play — no plugin install, no `sudo`, no
   `/usr/local` writes.

Today the Wooting bridge requires:
- `libwooting_analog_sdk.dylib` at `/usr/local/lib/`.
- The analog plugin under `/usr/local/share/WootingAnalogPlugins/<plugin>/`.
- The Wooting RGB SDK + `hidapi` from Homebrew.

All of that has to disappear behind the `.app` bundle.

## Linchpin: replace runtime dlopen with compile-time linking

The Wooting Analog SDK exposes only `wooting_analog_initialise()` via C ABI,
which loads plugins from a hardcoded default directory. The runtime override
`WOOTING_ANALOG_SDK_PLUGINS_PATH` is `option_env!()` — compile-time only,
not runtime. The Rust API has `AnalogSDK::initialise_with_plugin_path(dir,
nested)` which is what we need, but it isn't surfaced via FFI.

So step zero is:

- **Drop `libloading`-based loading of the Analog SDK dylib.**
- **Add `wooting-analog-sdk = "0.9"` (and `wooting-analog-common`) as a
  Cargo dependency** of `wooting_bridge`.
- Replace `wooting_bridge/src/analog.rs` with thin Rust calls to the crate
  API; pass a runtime plugin-dir argument all the way from Python settings.
- Drop the `wooting_analog_dylib_path` setting; replace with
  `wooting_analog_plugin_dir` (the bundled per-app plugin dir).

The Wooting RGB SDK is a C library; we keep `libloading` for now, **but**
we bundle the dylib in the `.app` and rewrite its `install_name` so it
resolves via `@loader_path/`.

## File-level plan

### Modified

- **`wooting_bridge/Cargo.toml`** — add deps `wooting-analog-sdk = "0.9"`,
  `wooting-analog-common = "0.9"`. Remove `libloading` (or keep for RGB
  only). Add `[[bin]]` later for the recorder (separate task).
- **`wooting_bridge/src/analog.rs`** — rewrite. Public surface stays the
  same (`AnalogSdk::open(plugin_dir) -> Self`, `initialise()`,
  `read_full_buffer()`, `get_connected_devices()`, `uninitialise()`),
  internals now call the upstream Rust API.
- **`wooting_bridge/src/lib.rs`** — `Bridge::new` takes
  `analog_plugin_dir: String` instead of `analog_sdk_path`.
- **`src/pg_isomap/wooting/bridge.py`** — pass `settings.wooting_plugin_dir`
  (resolved relative to `_MEIPASS` when frozen) instead of dylib path.
- **`src/pg_isomap/config.py`** — add `wooting_plugin_dir: Path`. In
  development, default to `<repo>/wooting_plugins/`. In a frozen bundle,
  resolve to `Path(sys._MEIPASS) / 'wooting_plugins'`.
- **`pg_isomap.spec`** — add bundled binaries:
  ```python
  binaries=[
      *scalatrix_binaries,
      ('wooting_plugins/abiv1-pluswooting.dylib',
        'wooting_plugins/universal-analog-plugin-with-wooting-device-support'),
      ('vendor/wooting/libwooting-rgb-sdk.dylib', '.'),
      ('vendor/wooting/libhidapi.dylib', '.'),
  ],
  ```
  and the maturin-built `pg_wooting_bridge.abi3.so` (PyInstaller picks it
  up automatically as a regular Python dependency).
- **`pg_isomap_win.spec`** — analogous, using `.dll`.
- **`sign_app.sh`** — add codesign + `install_name_tool -change` lines for
  the new dylibs so they resolve via `@executable_path/...` or
  `@loader_path/...`. Re-sign after install_name rewriting.
- **`run_dev.sh`** — set `PGISOMAP_WOOTING_PLUGIN_DIR=$(pwd)/wooting_plugins`
  so dev runs use the in-repo plugin dir, not the system `/usr/local`
  install. Falls back gracefully if missing.

### New

- **`vendor/wooting/`** — vendored prebuilt binaries:
  - `libwooting-rgb-sdk.dylib` (built once from `wooting-rgb-sdk/`)
  - `libhidapi.dylib` (Homebrew artifact, copied + install_name rewritten)
  - `aarch64/` and `x86_64/` subfolders if shipping per-arch DMGs, or a
    single `universal2/` produced by `lipo`.
- **`wooting_plugins/<plugin-name>/abiv1-pluswooting.dylib`** — the
  AnalogSense plugin built from source (already exists in
  `/Users/peter/dev/PitchGrid/universal-analog-plugin/` — drop the built
  artifact here). Same per-arch story as the RGB SDK.
- **`docs/wooting-build.md`** — short doc explaining how to rebuild the
  vendored binaries (the `sun` build for the plugin, `make` for the RGB
  SDK, `lipo` to merge architectures).

### Removed

- `settings.wooting_analog_dylib_path` — superseded by `wooting_plugin_dir`.

## Per-platform recipe

### macOS arm64 (current setup; what you're running today)

1. Apply the linchpin refactor.
2. Drop the AnalogSense plugin build (`abiv1-pluswooting.dylib`) into
   `wooting_plugins/universal-analog-plugin-with-wooting-device-support/`.
3. Build the RGB SDK as a dylib via `wooting-rgb-sdk/mac/Makefile`, then:
   ```
   install_name_tool -id @loader_path/libwooting-rgb-sdk.dylib vendor/wooting/libwooting-rgb-sdk.dylib
   install_name_tool -change /opt/homebrew/.../libhidapi.dylib @loader_path/libhidapi.dylib vendor/wooting/libwooting-rgb-sdk.dylib
   cp /opt/homebrew/opt/hidapi/lib/libhidapi.dylib vendor/wooting/
   install_name_tool -id @loader_path/libhidapi.dylib vendor/wooting/libhidapi.dylib
   ```
4. Run the existing `make build && pyinstaller pg_isomap.spec && bash sign_app.sh`.
5. Notarize via `notarytool` (already set up in `sign_app.sh` if creds are
   in env).

### macOS x86_64

Identical to arm64 with `BUILD_ARCH=x86_64` in env. The plugin and RGB SDK
both compile cleanly for Intel. Two options:
- Ship two DMGs (`-arm64`, `-x86_64`) — pattern the project already uses
  per the `*-arm64.dmg`/`*-x86_64.dmg` filenames in repo root.
- Ship one universal2 DMG: build wheels with `--target=universal2-apple-darwin`,
  build dylibs for both arches and `lipo -create` them, set PyInstaller
  `target_arch=universal2`.

Recommendation: stick with two DMGs (already plumbed). Less to debug.

### Windows

| Concern | Approach |
|---|---|
| Plugin install | Wootility on Windows already drops the plugin in `C:\Program Files\WootingAnalogPlugins\`. Ship a fallback copy of `abiv1-pluswooting.dll` in the app dir and pass that path to the SDK if the system path is empty. |
| Cross-compile | Build the bridge on Windows directly (`cargo build --target x86_64-pc-windows-msvc`) via GitHub Actions. PyO3 handles MSVC. |
| Virtual MIDI | **Pre-existing limitation**: needs loopMIDI or similar. README already documents this. Installer should detect missing virtual MIDI and link to loopMIDI download. |
| Codesigning | `pg_isomap_win.spec` and `Installers/Windows/` already produce a signed installer. Add the new DLLs to the asset list. |

### Linux

| Concern | Approach |
|---|---|
| Plugin install | Same as macOS — bundle in app dir, pass path to SDK at startup. |
| HID access | Ship a `udev` rule (Wooting publishes one) granting non-root HID access to the keyboard. Drop into `/usr/lib/udev/rules.d/70-wooting.rules` via the `.deb` postinst. |
| ALSA MIDI | Works out of the box — pre-existing limitation: user must be in `audio` group. |
| Distro packaging | Ship a `.deb` (Ubuntu/Debian/Pop!_OS/Mint) and an AppImage (everything else). |
| `hidapi` | Statically link `hidapi-libusb` into the RGB SDK build to avoid a runtime dep. |

## Verification per platform

A successful install means:

1. User opens the bundle, app starts.
2. App shows "Wooting 60HE v2" in the controller dropdown.
3. Selecting it activates the bridge with no log errors about missing
   plugins / dylibs.
4. Pressing keys produces MPE-shaped output on the **PitchGrid Mapper**
   virtual MIDI port.
5. Color schemes paint the keyboard correctly.

A regression test set per release:
- Install on a clean macOS user account (no Homebrew, no Wootility).
- Install on a clean Windows account (no Wootility).
- Install on a clean Ubuntu LTS.

## Estimated effort

| Task | Effort |
|---|---|
| Linchpin refactor (analog.rs to Rust crate) | 2–4 h |
| macOS arm64 bundling + sign + notarize | 4 h |
| macOS x86_64 (delta from arm64) | 1 h |
| Windows build pipeline + installer | 6–8 h |
| Linux .deb + AppImage + udev | 6–8 h |
| Cross-platform CI (GitHub Actions matrix) | 4–8 h |
| **Total** | **3–4 days** |

## Risks / known unknowns

- **Wooting RGB SDK redistribution**: confirm with Wooting (or read their
  license) whether we can redistribute the RGB SDK dylib in our installers.
  If not, the user has to install it separately — falls back to existing
  plan but uglier UX.
- **AnalogSense plugin redistribution**: project is MPL-2.0. We're allowed
  to redistribute, but should include the LICENSE.
- **macOS notarization** requires Apple Developer ID. Already plumbed for
  prior releases per `sign_app.sh`.
- **HID exclusive access on macOS**: untested whether enabling RGB locks
  out normal HID typing while the bridge runs. Needs a real-hardware test
  early in the install pipeline.
- **PyO3 abi3 wheel + universal2**: PyInstaller's `target_arch=universal2`
  combined with a maturin-built abi3 wheel — needs validation. Fallback
  is per-arch DMGs (already the project default).

## Critical files

- [pg_isomap.spec](pg_isomap.spec) — macOS PyInstaller spec.
- [pg_isomap_win.spec](pg_isomap_win.spec) — Windows spec.
- [sign_app.sh](sign_app.sh) — codesign / notarize.
- [Installers/Windows/](Installers/Windows/) — Inno Setup or NSIS scripts.
- [run_dev.sh](run_dev.sh), [run_prod.sh](run_prod.sh) — dev/prod launchers.
- [wooting_bridge/Cargo.toml](wooting_bridge/Cargo.toml)
- [wooting_bridge/src/analog.rs](wooting_bridge/src/analog.rs)
- [wooting_bridge/src/lib.rs](wooting_bridge/src/lib.rs)
- [src/pg_isomap/wooting/bridge.py](src/pg_isomap/wooting/bridge.py)
- [src/pg_isomap/config.py](src/pg_isomap/config.py)
