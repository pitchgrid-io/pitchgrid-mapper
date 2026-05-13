# Vendored Wooting binaries — provenance

The shared libraries in this directory and in `wooting_plugins/` are prebuilt
and checked in for self-contained reproducible release builds. Rebuild them
from the sources below if you need a different arch (e.g. x86_64 or
universal2 via `lipo -create`).

## `wooting-rgb-sdk64.dll` (Windows x64)

- License: MPL-2.0 (see `LICENSE.wooting-rgb-sdk`)
- Upstream: https://github.com/WootingKb/wooting-rgb-sdk
- Source: official GitHub release artifact
  `wooting-rgb-sdk-v1.8.0-win-x64.zip` from
  https://github.com/WootingKb/wooting-rgb-sdk/releases/tag/v1.8.0
- Notes: hidapi is statically linked into the Windows DLL upstream, so no
  separate `hidapi.dll` is required on Windows (in contrast to macOS).

## `libwooting-rgb-sdk.dylib`

- License: MPL-2.0 (see `LICENSE.wooting-rgb-sdk`)
- Upstream: https://github.com/WootingKb/wooting-rgb-sdk
- Fork (use this): git@github.com:pitchgrid-io/wooting-rgb-sdk.git
- Build:
  ```
  cd wooting-rgb-sdk/mac && make
  cp libwooting-rgb-sdk.dylib <repo>/vendor/wooting/
  install_name_tool -id @loader_path/libwooting-rgb-sdk.dylib \
      <repo>/vendor/wooting/libwooting-rgb-sdk.dylib
  install_name_tool -change /opt/homebrew/opt/hidapi/lib/libhidapi.0.dylib \
      @loader_path/libhidapi.0.dylib <repo>/vendor/wooting/libwooting-rgb-sdk.dylib
  codesign --force --sign - <repo>/vendor/wooting/libwooting-rgb-sdk.dylib
  ```

## `libhidapi.0.dylib`

- License: BSD-3-Clause (libusb/hidapi triple-licensed; we elect BSD-3)
- Upstream: https://github.com/libusb/hidapi
- Pragma: copied from Homebrew's `hidapi` formula, install_name rewritten
- Refresh:
  ```
  cp /opt/homebrew/opt/hidapi/lib/libhidapi.0.15.0.dylib \
      <repo>/vendor/wooting/libhidapi.0.dylib
  install_name_tool -id @loader_path/libhidapi.0.dylib \
      <repo>/vendor/wooting/libhidapi.0.dylib
  codesign --force --sign - <repo>/vendor/wooting/libhidapi.0.dylib
  ```

## `wooting_plugins/universal-analog-plugin-with-wooting-device-support/abiv1.dll` (Windows x64)

- License: MIT (Calamity, Inc. — see the directory's `LICENCE`)
- Upstream: https://github.com/AnalogSense/universal-analog-plugin
- Source: official GitHub release artifact `Windows.zip` from
  https://github.com/AnalogSense/universal-analog-plugin/releases/tag/0.3.5
  (path inside zip:
  `universal-analog-plugin-with-wooting-device-support/abiv1.dll`)
- Notes: the upstream zip also ships `abiv0.dll` and a wooting-less
  `universal-analog-plugin/abiv1.dll`; we only vendor the wooting-enabled
  `abiv1.dll`, which is what the Analog SDK auto-discovers in our plugin
  directory.

## `wooting_plugins/universal-analog-plugin-with-wooting-device-support/abiv1.dylib`

- License: MIT (Calamity, Inc. — see the directory's `LICENCE`)
- Upstream: https://github.com/AnalogSense/universal-analog-plugin
- Fork (use this): git@github.com:pitchgrid-io/universal-analog-plugin.git
- Build: requires Calamity's `sun` build tool. Build the
  `abiv1-pluswooting.sun` target, then install the resulting
  `libabiv1-pluswooting.dylib` as `abiv1.dylib` in this directory
  (the Wooting Analog SDK loader expects the `abiv1` name).
  ```
  cd universal-analog-plugin && ./build.sh
  cp libabiv1-pluswooting.dylib \
      <repo>/wooting_plugins/universal-analog-plugin-with-wooting-device-support/abiv1.dylib
  install_name_tool -id @loader_path/abiv1.dylib \
      <repo>/wooting_plugins/universal-analog-plugin-with-wooting-device-support/abiv1.dylib
  codesign --force --sign - \
      <repo>/wooting_plugins/universal-analog-plugin-with-wooting-device-support/abiv1.dylib
  ```

## Verifying install_names

After any rebuild, confirm everything resolves via `@loader_path`:

```
otool -L vendor/wooting/libwooting-rgb-sdk.dylib
otool -L vendor/wooting/libhidapi.0.dylib
otool -L wooting_plugins/universal-analog-plugin-with-wooting-device-support/abiv1.dylib
```

No path should reference `/opt/homebrew/...`, `/usr/local/...`, or the
absolute build directory. If something does, repeat the
`install_name_tool` step for that dependency.
