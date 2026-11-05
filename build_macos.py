"""Native macOS build. Includes models/notices before signing; no publication."""
from __future__ import annotations
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

from engine import VERSION
from fetch_models import sha256


def render_spec(source, model, speakers, notices, hooks, arch):
    data = [(str(model), "model"), (str(speakers), "speaker-model"),
            (str(source / "assets"), "assets"), (str(notices / "licenses"), "licenses"),
            (str(notices / "dependencies.json"), "."), (str(source / "macos/ПРОЧТИ МЕНЯ.txt"), ".")]
    return f'''from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs
a = Analysis({[str(source / 'app.py')]!r}, pathex={[str(source)]!r},
    datas={data!r} + collect_data_files('faster_whisper') + collect_data_files('sherpa_onnx'),
    binaries=collect_dynamic_libs('ctranslate2') + collect_dynamic_libs('onnxruntime') + collect_dynamic_libs('av') + collect_dynamic_libs('sherpa_onnx'),
    hookspath={[str(hooks)]!r},
    hiddenimports=['faster_whisper', 'onnxruntime', 'tkinterdnd2', 'sherpa_onnx'],
    excludes=['torch', 'tensorflow', 'transformers', 'scipy', 'matplotlib'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Ushastik',
    console=False, debug=False, strip=False, upx=False, argv_emulation=False,
    target_arch={arch!r}, codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Ushastik')
app = BUNDLE(coll, name='Ушастик.app', icon={str(source / 'assets/ushastik.icns')!r},
    bundle_identifier='ru.kurzemnek.ushastik',
    info_plist={{'CFBundleDisplayName': 'Ушастик', 'CFBundleShortVersionString': {VERSION!r},
        'CFBundleVersion': {VERSION!r}, 'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '15.0',
        'CFBundleDocumentTypes': [{{'CFBundleTypeName': 'Аудио и видео',
            'CFBundleTypeRole': 'Viewer', 'LSHandlerRank': 'Alternate',
            'CFBundleTypeExtensions': ['wav', 'mp3', 'm4a', 'ogg', 'opus', 'flac', 'aac', 'wma', 'mp4', 'webm', 'mkv']}}]}})
'''


def preflight(arch):
    if sys.platform != "darwin":
        raise RuntimeError("Сборка .app требует настоящий macOS runner")
    if sys.version_info[:2] != (3, 11):
        raise RuntimeError("Для воспроизводимой Mac-сборки нужен Python 3.11")
    if platform.machine() != arch:
        raise RuntimeError(f"Python architecture {platform.machine()} does not match {arch}")
    if int(platform.mac_ver()[0].split(".")[0]) < 15:
        raise RuntimeError("Тестовая сборка требует macOS 15+")
    import ctranslate2
    from tkinterdnd2 import TkinterDnD
    root = TkinterDnD.Tk()
    try:
        root.withdraw()
        tk_version = str(root.tk.call("set", "tk_patchLevel"))
        if not tk_version.startswith("8.6."):
            raise RuntimeError(f"Нужен Tcl/Tk 8.6, найден {tk_version}")
        tkdnd = str(root.tk.call("package", "require", "tkdnd"))
    finally:
        root.destroy()
    compute = sorted(ctranslate2.get_supported_compute_types("cpu"))
    if "int8" not in compute:
        raise RuntimeError(f"CPU int8 unavailable: {compute}")
    return {"platform": platform.platform(), "macos": platform.mac_ver()[0], "arch": arch,
            "python": sys.version, "tk": tk_version, "tkdnd": tkdnd, "cpu_compute_types": compute}


def main():
    parser = argparse.ArgumentParser()
    source = Path(__file__).resolve().parent
    parser.add_argument("--arch", choices=("arm64", "x86_64"), default=platform.machine())
    parser.add_argument("--model", type=Path, default=source / "model")
    parser.add_argument("--speakers", type=Path, default=source / "speaker-model")
    parser.add_argument("--work", type=Path, default=source / ".build/macos")
    parser.add_argument("--dist", type=Path, default=source / "dist")
    args = parser.parse_args()
    details = preflight(args.arch)
    args.work = args.work.resolve()
    args.dist = args.dist.resolve()
    args.work.mkdir(parents=True, exist_ok=True)
    args.dist.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(args.work).free < 4 * 1024 ** 3:
        raise RuntimeError("Для сборки нужно не менее 4 ГиБ свободного места")
    model, speakers = args.model.resolve(), args.speakers.resolve()
    for asset in json.loads((source / "macos/models.json").read_text()):
        directory, filename = asset["path"].split("/", 1)
        path = (model if directory == "model" else speakers) / filename
        if not path.is_file() or sha256(path) != asset["sha256"]:
            raise RuntimeError(f"Missing or damaged bundled model: {path}")
    notices = args.work / "notices"
    licenses = notices / "licenses"
    licenses.mkdir(parents=True, exist_ok=True)
    distributions = list(importlib.metadata.distributions())
    dependencies = {p.metadata["Name"]: p.version for p in distributions}
    (notices / "dependencies.json").write_text(json.dumps(dependencies, indent=2))
    for package in distributions:
        for file in package.files or []:
            if any(x in file.name.lower() for x in ("license", "copying", "notice")) and (".dist-info" in str(file) or ".licenses" in str(file)):
                path = package.locate_file(file)
                if path.is_file():
                    shutil.copy2(path, licenses / (package.metadata["Name"] + "-" + "-".join(file.parts[-2:])))
    hooks = args.work / "hooks"
    hooks.mkdir(exist_ok=True)
    # The upstream hook includes other OS/CPU builds. Keep only this Mac architecture.
    tkdnd_folder = "osx-arm64" if args.arch == "arm64" else "osx-x64"
    (hooks / "hook-tkinterdnd2.py").write_text(
        "from PyInstaller.utils.hooks import collect_data_files\n" +
        f"datas = collect_data_files('tkinterdnd2', includes=['tkdnd/{tkdnd_folder}/**'])\n")
    spec = args.work / "Ushastik.spec"
    spec.write_text(render_spec(source, model, speakers, notices, hooks, args.arch), encoding="utf-8")
    environment = dict(os.environ, MACOSX_DEPLOYMENT_TARGET="15.0", PYTHONDONTWRITEBYTECODE="1")
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--distpath", str(args.dist),
                    "--workpath", str(args.work / "pyinstaller"), str(spec)], check=True, env=environment)
    app = args.dist / "Ушастик.app"
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    archive = args.dist / f"Ушастик-macOS-{args.arch}-{VERSION}.zip"
    subprocess.run(["/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(archive)], check=True)
    (args.dist / "build-macos.json").write_text(json.dumps({**details, "version": VERSION, "app": str(app),
        "archive": str(archive), "archive_sha256": sha256(archive), "signing": "ad-hoc; not notarized",
        "dependencies": dependencies}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(archive), flush=True)


if __name__ == "__main__":
    main()
