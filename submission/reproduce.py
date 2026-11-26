"""Run the unchanged official predictor and scorer from any working directory."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--official',type=Path,required=True,help='Extracted official package directory')
    parser.add_argument('--split',choices=['train','validation','test'],default='test')
    parser.add_argument('--config',type=Path,default=Path(__file__).with_name('config.json'))
    parser.add_argument('--output-dir',type=Path,default=Path(__file__).with_name('outputs'))
    args=parser.parse_args()
    source=Path(__file__).resolve().parent
    official=args.official.resolve()
    if not (official/'mp3_sql'/'predict.py').is_file():
        parser.error('--official must point to the directory containing mp3_sql/')
    output=args.output_dir.resolve()
    output.mkdir(parents=True,exist_ok=True)
    prediction=output/f'{args.split}.json'
    score=output/f'{args.split}-score.json'
    env=dict(os.environ,PYTHONPATH=os.pathsep.join([str(source),str(official)]),
             PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='4',
             OPENBLAS_NUM_THREADS='4',MKL_NUM_THREADS='4')
    subprocess.run([sys.executable,'-m','mp3_sql.predict','--split',args.split,'--workers','4',
                    '--method',str(source/'submission.py'),'--checkpoint',str(args.config.resolve()),
                    '--output',str(prediction)],cwd=official,env=env,check=True)
    subprocess.run([sys.executable,'-m','mp3_sql.score','--split',args.split,'--workers','4',
                    '--predictions',str(prediction),'--output',str(score)],
                   cwd=official,env=env,check=True)
    result=json.loads(score.read_text(encoding='utf-8'))
    print(f"Official {args.split}: {result['correct']}/{result['n']} = {result['accuracy']:.4f}%")


if __name__=='__main__':
    main()
