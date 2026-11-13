"""Wait for a recorded stock finale, then extract metrics and run its settlement room."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--settlement-rounds', type=int, default=0)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    args.out.mkdir(parents=True, exist_ok=True)
    previous = None
    while True:
        try:
            data = json.loads(args.candidate.read_text())
            book = data['modules']['stock:portfolio']['accounts']
            actors = data['host']['roster']
            done = all(book[a].get('final_cash') is not None for a in actors)
            period = data['modules']['stock:clock']['round']
            if period != previous or done:
                command = [sys.executable, str(repo/'experiments/stock_compare.py'),
                           '--baseline', str(args.baseline), '--candidate', str(args.candidate),
                           '--out', str(args.out)]
                subprocess.run(command, cwd=repo, check=True)
                previous = period
            if done:
                break
        except (OSError, ValueError) as error:
            print('Waiting for readable save:', str(error), flush=True)
        time.sleep(30)
    before = hashlib.sha256(args.candidate.read_bytes()).hexdigest()
    settlement_status = None
    if args.settlement_rounds:
        report = args.candidate.parent/'结算厅复盘.md'
        if report.exists():
            raise RuntimeError('Refusing to overwrite an existing settlement report')
        command = [sys.executable, str(repo/'settlement.py'), '--save', str(args.candidate),
                   '--config', str(args.config), '--out', str(report),
                   '--rounds', str(args.settlement_rounds)]
        result = subprocess.run(command, cwd=repo, check=False)
        settlement_status = result.returncode
    after = hashlib.sha256(args.candidate.read_bytes()).hexdigest()
    if after != before:
        raise RuntimeError('Final save changed during read-only settlement')
    marker = dict(completed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  final_save_sha256=after, settlement_returncode=settlement_status,
                  report=str(args.out/'对照评估.md'))
    (args.out/'COMPLETE.json').write_text(json.dumps(marker, ensure_ascii=False, indent=2)+'\n')
    print('Comparison pipeline complete:', json.dumps(marker, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
