from pathlib import Path
import subprocess,sys
ROOT=Path(__file__).resolve().parents[1]
subprocess.run([sys.executable,str(ROOT/"scripts/curate.py")],check=True)
subprocess.run([sys.executable,str(ROOT/"scripts/publish.py")],check=True)
subprocess.run([sys.executable,str(ROOT/"scripts/validate.py")],check=True)
