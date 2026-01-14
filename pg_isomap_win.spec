# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for Windows build
import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Get configuration from environment
app_name = os.getenv('APP_NAME', 'PGIsomap')
app_version = os.getenv('APP_VERSION', '0.1.0')

# Get paths
frontend_dist = 'frontend/dist'
controller_config = 'controller_config'

# Find scalatrix binary
scalatrix_binaries = []
scalatrix_so_path = None
try:
    import scalatrix
    scalatrix_so_path = scalatrix.__file__
    print(f"Found scalatrix at: {scalatrix_so_path}")
except ImportError:
    # If we can't import, try to find it manually
    site_packages = os.path.join(sys.prefix, 'Lib', 'site-packages')
    scalatrix_so = os.path.join(site_packages, 'scalatrix.so')
    if os.path.exists(scalatrix_so):
        scalatrix_so_path = scalatrix_so
        print(f"Found scalatrix.so at: {scalatrix_so}")
    else:
        print("WARNING: Could not find scalatrix binary!")

# Add as binary (will be renamed after Analysis)
if scalatrix_so_path:
    scalatrix_binaries = [(scalatrix_so_path, '.')]

a = Analysis(
    ['launcher.py'],
    pathex=['src'],
    binaries=scalatrix_binaries,
    datas=[
        (frontend_dist, 'frontend/dist'),
        (controller_config, 'controller_config'),
    ],
    hiddenimports=[
        'pg_isomap',
        'pg_isomap.app',
        'pg_isomap.config',
        'pg_isomap.desktop_app',
        'pg_isomap.web_api',
        'pg_isomap.midi_handler',
        'pg_isomap.osc_handler',
        'pg_isomap.controller_config',
        'pg_isomap.layouts',
        'pg_isomap.layouts.base',
        'pg_isomap.layouts.isomorphic',
        'pg_isomap.layouts.string_like',
        'pg_isomap.layouts.piano_like',
        'rtmidi',
        'pythonosc',
        'pythonosc.dispatcher',
        'pythonosc.osc_server',
        'pythonosc.udp_client',
        'scalatrix',
        'uvicorn',
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'fastapi',
        'starlette',
        'starlette.routing',
        'starlette.middleware',
        'starlette.middleware.cors',
        'anyio',
        'anyio._backends',
        'anyio._backends._asyncio',
        'webview',
        'webview.platforms',
        'webview.platforms.edgechromium',  # Windows uses Edge WebView2
        'pydantic',
        'pydantic_settings',
        'yaml',
        'colorsys',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

# Rename scalatrix.so to scalatrix.pyd in the binaries list for Windows compatibility
new_binaries = []
for binary_tuple in a.binaries:
    name, src, typecode = binary_tuple
    if name == 'scalatrix.so':
        new_binaries.append(('scalatrix.pyd', src, typecode))
        print(f"Renamed {name} -> scalatrix.pyd in binaries")
    else:
        new_binaries.append(binary_tuple)
a.binaries = new_binaries

pyz = PYZ(a.pure)

# Windows EXE - onedir mode
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=app_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # GUI application, no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=f'{app_name}.ico',  # Windows icon
)

# Collect all files into a directory
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=app_name,
)
