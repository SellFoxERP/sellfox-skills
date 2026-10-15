#!/usr/bin/env python3
"""一次安装通用技能与十一个专项技能；升级保留商家配置和运行产物。"""
from __future__ import annotations
import argparse
import json
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHARED_NAME = '_sellfox_shared'
SKIP = {'out', 'logs', '__pycache__', 'evals', '.DS_Store', '_shared'}

def copy_clean(source: Path, target: Path):
    shutil.copytree(source, target, ignore=lambda _, names: [n for n in names if n in SKIP or n.endswith(('.pyc', '.bak')) or n == 'local.yaml'])

def install(destination: Path, update: bool = False):
    destination = destination.expanduser().resolve()
    skills = sorted(ROOT.glob('[0-9][0-9]-*/SKILL.md'))
    manifest = json.loads((ROOT / '.codebuddy-plugin/plugin.json').read_text(encoding='utf-8'))
    expected = {(ROOT / entry).resolve() for entry in manifest['skills']}
    if {entry.resolve() for entry in skills} != expected or not (ROOT / '_shared').is_dir():
        raise ValueError('安装源不完整：技能应与插件清单一致，并包含 _shared，请解压完整发布包。')
    names = [p.parent.name[3:] for p in skills]
    targets = names + [SHARED_NAME]
    conflicts = [n for n in targets if (destination / n).exists()]
    marker = destination / '.sellfox-install.json'
    if conflicts and not update:
        raise ValueError('目标已存在：' + ', '.join(conflicts) + '。升级请使用 --update，配置与 out/logs 将保留。')
    if conflicts:
        managed = json.loads(marker.read_text()).get('skills', []) if marker.exists() else []
        if not managed or not set(managed).issubset(names) or any(n not in managed + [SHARED_NAME] for n in conflicts):
            raise ValueError('目标不是本安装器管理的完整安装；请选择其他目录，避免覆盖已有技能。')
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.sellfox-stage-', dir=destination.parent) as temp:
        stage = Path(temp)
        copy_clean(ROOT / '_shared', stage / SHARED_NAME)
        for entry, name in zip(skills, names):
            copy_clean(entry.parent, stage / name)
            # 唯一共享配置，迁移安装目录后请重新执行安装器。
            if (stage / name / 'config').is_dir():
                (stage / name / 'config/paths.yaml').write_text('shared_root: ' + json.dumps(str(destination / SHARED_NAME), ensure_ascii=False) + '\n', encoding='utf-8')
            entrypoint = stage / name / 'SKILL.md'
            content = entrypoint.read_text(encoding='utf-8')
            content = content.replace('## 运行环境与路径', '## 运行环境与路径\n\n本次批量安装：文中 `_shared/...` 引用指向技能根目录旁的 `../_sellfox_shared/...`。')
            entrypoint.write_text(content, encoding='utf-8')
        for name in conflicts:
            old, new = destination / name, stage / name
            for folder in ('config', 'out', 'logs'):
                if (old / folder).is_dir():
                    shutil.copytree(old / folder, new / folder, dirs_exist_ok=True)
            if name != SHARED_NAME and (new / 'config').is_dir():
                (new / 'config/paths.yaml').write_text('shared_root: ' + json.dumps(str(destination / SHARED_NAME), ensure_ascii=False) + '\n', encoding='utf-8')
        backup = stage / 'backup'
        backup.mkdir()
        moved, installed = [], []
        try:
            for name in targets:
                target = destination / name
                if target.exists():
                    target.rename(backup / name); moved.append(name)
                (stage / name).rename(target); installed.append(name)
            marker.write_text(json.dumps({'skills': names, 'shared': SHARED_NAME}, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        except Exception:
            for name in installed:
                shutil.rmtree(destination / name)
            for name in moved:
                (backup / name).rename(destination / name)
            raise
    print(f'已一次安装全部 {len(names)} 个技能：{destination}')
    print(f'统一配置目录：{destination / SHARED_NAME / "config"}')
    print(f'请在所用客户端中确认 {len(names)} 个技能可用，并在新任务中测试。')

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--skills-dir', type=Path, default=Path.home()/'.workbuddy/skills', help='目标客户端的技能目录；不传时沿用 ~/.workbuddy/skills，其他客户端请显式指定')
    p.add_argument('--update', action='store_true', help='升级本安装器管理的安装，保留配置和运行产物')
    args=p.parse_args()
    try:
        install(args.skills_dir, args.update)
    except (ValueError, OSError) as exc:
        p.exit(1, f'安装失败：{exc}\n')
if __name__=='__main__':
    main()
