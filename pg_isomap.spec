# -*- mode: python ; coding: utf-8 -*-
import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Get configuration from environment
codesign_identity = os.getenv('CODESIGN_IDENTITY', '')
bundle_id = os.getenv('BUNDLE_ID', 'com.pitchgrid.isomap')
app_name = os.getenv('APP_NAME', 'PGIsomap')
app_version = os.getenv('APP_VERSION', '0.1.0')

# Get paths
frontend_dist = 'frontend/dist'
controller_config = 'controller_config'

# Find scalatrix binary
scalatrix_binaries = []
try:
    import scalatrix
    scalatrix_path = scalatrix.__file__
    scalatrix_binaries = [(scalatrix_path, '.')]
    print(f"Found scalatrix at: {scalatrix_path}")
except ImportError:
    # If we can't import, try to find it manually
    site_packages = os.path.join(sys.prefix, 'lib', 'python3.12', 'site-packages')
    scalatrix_so = os.path.join(site_packages, 'scalatrix.so')
    scalatrix_dylib = os.path.join(site_packages, 'scalatrix.dylib')
    if os.path.exists(scalatrix_so):
        scalatrix_binaries = [(scalatrix_so, '.')]
        print(f"Found scalatrix.so at: {scalatrix_so}")
    elif os.path.exists(scalatrix_dylib):
        scalatrix_binaries = [(scalatrix_dylib, '.')]
        print(f"Found scalatrix.dylib at: {scalatrix_dylib}")
    else:
        print("WARNING: Could not find scalatrix binary!")

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
        'webview.platforms.cocoa',
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

pyz = PYZ(a.pure)

# Onedir mode EXE - don't bundle binaries/datas into executable
exe = EXE(
    pyz,
    a.scripts,
    [],  # Don't include binaries here for onedir mode
    exclude_binaries=True,  # Required for COLLECT
    name=app_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,  # We sign manually in sign_app.sh
    entitlements_file=None,
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

# Create the .app bundle from the collected files
app = BUNDLE(
    coll,
    name=f'{app_name}.app',
    icon=f'{app_name}.icns',
    bundle_identifier=bundle_id,
    info_plist={
        'NSHighResolutionCapable': 'True',
        'CFBundleDisplayName': 'PG Isomap',
        'CFBundleName': app_name,
        'CFBundleVersion': app_version,
        'CFBundleShortVersionString': app_version,
        'NSRequiresAquaSystemAppearance': 'False',
        'LSMinimumSystemVersion': '10.15',
    },
)
