"""py2app configuration for Swap.app."""

from pathlib import Path

from setuptools import setup

ROOT = Path(__file__).resolve().parent
OPTIONS = {
    "argv_emulation": False,
    "iconfile": str(ROOT / "assets" / "Swap.icns"),
    "plist": {
        "CFBundleName": "Swap",
        "CFBundleDisplayName": "Swap",
        "CFBundleIdentifier": "io.bytecode.swap-onboard",
        "CFBundleVersion": "0.1.0",
        "CFBundleShortVersionString": "0.1.0",
        "LSUIElement": False,
        "NSHighResolutionCapable": True,
        "NSInputMonitoringUsageDescription": "Swap reads and switches onboard mouse profiles.",
    },
    "packages": ["swap", "swap.hidpp"],
    "includes": ["hid", "swap.app_mac", "swap.choices"],
}

setup(name="Swap", app=["swap/app.py"], options={"py2app": OPTIONS}, setup_requires=["py2app"])
