# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec file for Windows build
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Get configuration from environment
app_name = os.getenv('APP_NAME', 'PGIsomap')
app_version = os.getenv('APP_VERSION', '0.1.0')

# Get paths
frontend_dist = 'frontend/dist'
controller_config = 'controller_config'

a = Analysis(
    ['launcher.py'],
    pathex=['src'],
    binaries=[],
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
