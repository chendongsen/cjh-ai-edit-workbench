"""Prepare package in the worker, then invoke Resolve. No heavy work in HTTP handlers."""
import subprocess
import sys
from pathlib import Path
from common import ROOT, RESOLVE, read, write
from planner import build

if __name__ == '__main__':
    source = Path(sys.argv[1])
    request = read(source)
    try:
        print('校验剪辑范围并预渲染透明花字', flush=True)
        data = build(request['data'], request['assets'], source.parent, source.parent.name)
        target = source.parent / 'resolve-input.json'
        write(target, data)
        proc = subprocess.run([RESOLVE, str(ROOT / 'backend/resolve_bridge.py'), str(target)])
        sys.exit(proc.returncode)
    except Exception as e:
        print(str(e), file=sys.stderr, flush=True)
        sys.exit(1)
