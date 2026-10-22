#!/usr/bin/env python3
"""Use the common processor shipped in the complete skill package."""
from pathlib import Path
import sys
root=Path(__file__).resolve().parents[2]
engine=next((root/name/'scripts' for name in ('10-sellfox-business','sellfox-business') if (root/name/'scripts/process_data.py').is_file()),None)
if engine is None:
    raise SystemExit('缺少 sellfox-business 计算组件，请重新批量安装完整技能包。')
sys.path.insert(0,str(engine))
from process_data import main
if __name__ == '__main__':
    raise SystemExit(main())
