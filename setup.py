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
        # Branding changes must not change the existing G permission identity.
        "CFBundleIdentifier": "io.bytecode.g-onboard",
        "CFBundleVersion": "0.1.0",
        "CFBundleShortVersionString": "0.1.0",
        "LSUIElement": False,
        "NSHighResolutionCapable": True,
        "NSInputMonitoringUsageDescription": "Swap reads and switches onboard mouse profiles.",
    },
    "packages": ["gui", "gcore", "ghub_presets", "watchdog"],
    "includes": ["hid", "gui.app_mac"],
}

setup(name="Swap", app=["gui/app.py"], options={"py2app": OPTIONS}, setup_requires=["py2app"])
