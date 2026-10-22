#!/usr/bin/env python3
"""静态发布检查：白名单文件、技能元数据、JSON、Python、凭证/私有路径候选。"""
from pathlib import Path
import ast
import json
import re
import sys
ROOT=Path(__file__).resolve().parent.parent
EXCLUDED={'_internal','out','logs','__pycache__','.git','.venv','dist','.workbuddy','.codebuddy'}
ALLOWED_ROOTS={'.codebuddy-plugin','README.md','INSTALL.md','.gitignore','requirements-optional.txt','docs','scripts','tests','_shared','.github','LICENSE','LICENSE.md'}
PATTERNS={
 'private-key':re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----'),
 'token':re.compile(r'(?<![\w])[a-fA-F0-9]{64}(?![\w])'),
 'personal-path':re.compile(r'/(?:Users|home)/[\w.-]+/'),
 'private-endpoint':re.compile(r'https?://[^\s\"<>`]*(?:alpha|localhost|127\.0\.0\.1|meiyunji)[^\s\"<>`]*'),
 'assigned-secret':re.compile(r'''(?:X-MCP-Key|APP_SECRET|api_key|access_token)["']?\s*[:=]\s*["']([A-Za-z0-9_-]{24,})["']'''),
}
def publish_files():
 for p in sorted(ROOT.rglob('*')):
  rel=p.relative_to(ROOT)
  if set(rel.parts)&EXCLUDED or p.name in {'local.yaml','.DS_Store'} or p.suffix in {'.bak','.pyc'} or p.name.startswith('.env'):continue
  if p.is_file():yield p

def audit():
 errors=[];count=0;skill_names=[]
 for p in publish_files():
  rel=p.relative_to(ROOT);count+=1
  if rel.parts[0] not in ALLOWED_ROOTS and not re.fullmatch(r'(?:0[1-9]|1[0-4])-[a-z-]+',rel.parts[0]):errors.append(f'{rel}: 非发布白名单文件')
  if p.is_symlink():errors.append(f'{rel}: 不发布符号链接');continue
  try:s=p.read_text(encoding='utf-8')
  except UnicodeError:errors.append(f'{rel}: 未审核的二进制文件');continue
  for n,line in enumerate(s.splitlines(),1):
   for label,pattern in PATTERNS.items():
    if pattern.search(line):errors.append(f'{rel}:{n}: {label} 候选（不输出敏感值）')
  try:
   if p.suffix=='.py':ast.parse(s,filename=str(rel))
   if p.suffix=='.json':json.loads(s)
  except (SyntaxError,ValueError) as exc:errors.append(f'{rel}: 解析失败 {exc}')
  if p.name=='SKILL.md':
   match=re.match(r'\A---\n(.*?)\n---\n',s,re.S)
   if not match:errors.append(f'{rel}: 缺少 frontmatter');continue
   fields=dict(re.findall(r'^(name|description):\s*(.+)$',match[1],re.M))
   name=fields.get('name','');description=fields.get('description','')
   if not re.fullmatch(r'[a-z][a-z0-9-]{0,63}',name) or not description or len(description)>1024:errors.append(f'{rel}: 元数据不合规')
   if len(s.splitlines())>500:errors.append(f'{rel}: 入口过长')
   skill_names.append(name)
   for ref in re.findall(r'`((?:references|config|scripts|adapters)/[\w./-]+\.(?:md|py|yaml|json))`',s):
    if not (p.parent/ref).exists():errors.append(f'{rel}: 引用缺失 {ref}')
 if len(skill_names)!=14 or len(set(skill_names))!=14:errors.append('技能数量或命名不正确')
 manifest=json.loads((ROOT/'.codebuddy-plugin/plugin.json').read_text(encoding='utf-8'))
 expected={'./'+p.relative_to(ROOT).as_posix() for p in ROOT.glob('[0-9][0-9]-*/SKILL.md')}
 if set(manifest['skills'])!=expected or len(manifest['skills'])!=len(expected):errors.append('插件清单与技能目录不一致')
 print(f'检查 {count} 个发布候选文件、{len(skill_names)} 个技能。')
 for error in errors:print('FAIL',error)
 if not (ROOT/'LICENSE').exists() and not (ROOT/'LICENSE.md').exists():print('NOTE 未选择开源许可证；发布者需决定授权条款，不能宣称已采用 MIT/Apache。')
 print('PASS 发布静态检查' if not errors else f'FAIL {len(errors)} 项')
 return bool(errors)
if __name__=='__main__':sys.exit(audit())
