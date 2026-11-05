"""Run with Windows Python in an isolated environment. No publication."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--model", type=Path, required=True)
parser.add_argument("--work", type=Path, required=True)
parser.add_argument("--dist", type=Path, required=True)
parser.add_argument("--speakers", type=Path, required=True)
args = parser.parse_args()
if sys.platform != "win32":
    raise SystemExit("Сборку Windows нужно запускать Windows Python")
source = Path(__file__).resolve().parent
args.work.mkdir(parents=True, exist_ok=True)
spec = args.work / "Ushastik.spec"
spec.write_text('''from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs
a = Analysis([SOURCE], pathex=[ROOT],
    datas=[(MODEL, 'model'), (SPEAKERS, 'speaker-model'), (ASSETS, 'assets')] + collect_data_files('faster_whisper') + collect_data_files('tkinterdnd2') + collect_data_files('sherpa_onnx'),
    binaries=collect_dynamic_libs('ctranslate2') + collect_dynamic_libs('onnxruntime') + collect_dynamic_libs('av') + collect_dynamic_libs('sherpa_onnx'),
    hiddenimports=['faster_whisper', 'onnxruntime', 'tkinterdnd2', 'sherpa_onnx'],
    excludes=['torch', 'tensorflow', 'transformers', 'scipy', 'matplotlib'],
    noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Ushastik', icon=ICON,
          console=False, debug=False, strip=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Ushastik')
'''.replace("[SOURCE]", repr([str(source / "app.py")])).replace("[ROOT]", repr([str(source)]))
    .replace("MODEL", repr(str(args.model.resolve()))).replace("SPEAKERS", repr(str(args.speakers.resolve())))
    .replace("ASSETS", repr(str(source / "assets"))).replace("ICON", repr(str(source / "assets" / "ushastik.ico"))), encoding="utf-8")
subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm",
                "--distpath", str(args.dist), "--workpath", str(args.work / "pyinstaller"), str(spec)], check=True)
target = args.dist / "Ushastik"
shutil.copy2(source / "ПРОЧТИ МЕНЯ.txt", target)
notices = target / "licenses"
notices.mkdir(exist_ok=True)
for package in importlib.metadata.distributions():
    for f in package.files or []:
        if any(x in f.name.lower() for x in ("license", "copying", "notice")) and (".dist-info" in str(f) or ".licenses" in str(f)):
            real = package.locate_file(f)
            if real.is_file():
                name = package.metadata["Name"] + "-" + "-".join(f.parts[-2:])
                shutil.copy2(real, notices / name)
(target / "dependencies.json").write_text(json.dumps({p.metadata["Name"]: p.version for p in importlib.metadata.distributions()}, indent=2), encoding="utf-8")
print(str(target))
