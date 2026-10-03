import argparse
import json
from pathlib import Path
from .evaluation import evaluate
from .reviewer import review
from .server import serve

def main():
    parser=argparse.ArgumentParser(description='PatchPilot: local evidence-linked Python PR review')
    sub=parser.add_subparsers(dest='command')
    web=sub.add_parser('serve',help='Start local web app')
    web.add_argument('--port',type=int,default=8765)
    web.add_argument('--data-dir',default=None)
    check=sub.add_parser('review',help='Review a unified diff')
    check.add_argument('diff',type=Path)
    check.add_argument('--ai',action='store_true')
    bench=sub.add_parser('evaluate',help='Run synthetic development benchmark')
    bench.add_argument('--ai',action='store_true')
    args=parser.parse_args()
    if args.command in (None,'serve'):
        serve(getattr(args,'port',8765),getattr(args,'data_dir',None))
    elif args.command=='review':
        result=review(args.diff.read_text(),use_ai=args.ai)
        result.pop('files',None)
        print(json.dumps(result,indent=2))
    else: print(json.dumps(evaluate(args.ai),indent=2))

if __name__=='__main__': main()
