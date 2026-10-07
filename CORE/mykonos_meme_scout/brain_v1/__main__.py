from __future__ import annotations
import argparse
import json
from pathlib import Path
from . import VERSION


def main(argv=None):
    parser=argparse.ArgumentParser(description='MYKONOS v1 causal autonomous PAPER brain; live authority permanently 0%')
    parser.add_argument('--source-root',default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument('--runtime-dir',default=None)
    sub=parser.add_subparsers(dest='command',required=True)
    train=sub.add_parser('train')
    train.add_argument('--snapshot',action='store_true',help='Use only a quiescent backup source')
    train.add_argument('--dataset-cache')
    train.add_argument('--max-records',type=int,default=24000)
    train.add_argument('--epochs',type=int,default=12)
    train.add_argument('--no-walk-forward',action='store_true')
    run=sub.add_parser('run')
    run.add_argument('--runtime-seconds',type=int,default=0)
    run.add_argument('--collect',action='store_true',help='Collect public market data concurrently')
    run.add_argument('--training-interval-seconds',type=int,default=1800)
    sub.add_parser('simulate-research')
    sub.add_parser('collect')
    sub.add_parser('learn-online')
    refine=sub.add_parser('refine-exits',help='Refine exits from retained training windows; historical diagnostics are not forward evidence')
    refine.add_argument('--dataset-cache',required=True)
    refine.add_argument('--epochs',type=int,default=6)
    sub.add_parser('status')
    sub.add_parser('doctor')
    args=parser.parse_args(argv)
    source=Path(args.source_root).resolve()
    runtime=Path(args.runtime_dir).resolve() if args.runtime_dir else source/'runtime'/'brain-v1'
    if args.command=='train':
        from .pipeline import training_run
        report=training_run(source,runtime,snapshot=args.snapshot,max_records=args.max_records,
            epochs=args.epochs,dataset_cache=args.dataset_cache,walk_forward=not args.no_walk_forward)
        print(json.dumps({'version':VERSION,'report':str(runtime/'latest_report.json'),
            'dataset_splits':report['dataset']['splits'],'test':report['test'],
            'governor':report['governor'],'execution_authority_pct':0},indent=2))
    elif args.command=='run':
        from .runtime import run_service
        print(json.dumps(run_service(source,runtime,runtime_seconds=args.runtime_seconds,
            collect=args.collect,training_interval_seconds=args.training_interval_seconds),indent=2))
    elif args.command=='simulate-research':
        from .simulation_lab import simulate_research
        print(json.dumps(simulate_research(source,runtime),indent=2))
    elif args.command=='collect':
        from .runtime import collect_market
        print(json.dumps(collect_market(runtime),indent=2))
    elif args.command=='learn-online':
        from .runtime import online_learning
        print(json.dumps(online_learning(source,runtime),indent=2))
    elif args.command=='refine-exits':
        from .refinement import refine_exits
        result=refine_exits(args.dataset_cache,runtime,epochs=args.epochs)
        print(json.dumps({'challenger':result['challenger'],'additional_exit_examples':result['additional_exit_examples'],
            'report':str(runtime/'refinement_report.json'),'execution_authority_pct':0},indent=2))
    elif args.command=='status':
        path=runtime/'service_status.json'
        print(path.read_text(encoding='utf-8') if path.exists() else json.dumps({'version':VERSION,'status':'NOT_RUNNING','execution_authority_pct':0}))
    elif args.command=='doctor':
        from .doctor import doctor
        result=doctor(source,runtime)
        print(json.dumps(result,indent=2))
        if not result['ok']:
            raise SystemExit(2)


if __name__=='__main__':
    main()
