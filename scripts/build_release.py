#!/usr/bin/env python3
"""构建一个批量安装 ZIP；仅打包发布白名单，不包含运行数据或客户端凭证。"""
from pathlib import Path
import hashlib
import zipfile
from audit_release import audit
ROOT=Path(__file__).resolve().parent.parent
SKIP={'out','logs','__pycache__','_internal','.git','.venv','dist'}
def build():
    if audit():
        raise SystemExit('发布检查失败，停止打包。')
    dest=ROOT/'dist';dest.mkdir(exist_ok=True)
    output=dest/'sellfox-skills.zip'
    roots=[ROOT/n for n in ('.codebuddy-plugin','README.md','INSTALL.md','scripts','docs','_shared','tests','requirements-optional.txt')]
    roots+=sorted(ROOT.glob('[0-9][0-9]-*'))
    with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as z:
        for item in roots:
            for p in sorted(item.rglob('*')) if item.is_dir() else [item]:
                rel=p.relative_to(ROOT)
                if not p.is_file() or p.is_symlink() or set(rel.parts)&SKIP or p.name in {'local.yaml','.DS_Store'} or p.suffix in {'.pyc','.bak','.key','.pem'} or p.name.startswith('.env'):
                    continue
                z.write(p,'sellfox-skills/'+rel.as_posix())
    digest=hashlib.sha256(output.read_bytes()).hexdigest()
    (dest/'SHA256SUMS').write_text(f'{digest}  {output.name}\n',encoding='utf-8')
    print(output)
if __name__=='__main__':build()
