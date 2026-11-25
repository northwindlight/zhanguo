"""Build a private, checksummed handoff from already verified local artifacts."""
from pathlib import Path
import hashlib
import json
import sys
from zipfile import ZipFile, ZIP_STORED, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
STAGE = Path(__file__).resolve().parent
RESULTS = ROOT/'industry-bc-results'


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()


def main():
    inventory=json.loads((RESULTS/'inventory.json').read_text())
    checkpoints=sorted(RESULTS.glob('epoch-[0-9][0-9].pt'))
    latest=checkpoints[-1]
    if sha(latest)!=inventory[latest.name]['sha256']:
        raise ValueError('Latest checkpoint is not verified by the Colab backup inventory')
    if '--evaluation-only' in sys.argv:
        if int(latest.stem.split('-')[1])!=4:raise RuntimeError('All four BC epochs must be complete')
        best=json.loads((RESULTS/'best.json').read_text())
        files={'code.zip':STAGE/'code.zip',
               'original/base.pt':ROOT/'mixed-bc-results/best-batch-mean.pt',
               'original/base-native.pt':ROOT/'mixed-bc-results/best-batch-mean-native.pt'}
        chosen={latest.name,latest.with_suffix('.json').name,best['checkpoint'],best['native'],
                'best.json','baseline.json','epoch-02-eval.json','curriculum.json'}
        for name in chosen:
            path=RESULTS/name
            if name.endswith('.pt') and sha(path)!=inventory[name]['sha256']:raise ValueError('Checkpoint hash mismatch')
            files['work/'+name]=path
        from collections import Counter
        totals=Counter();chunks={}
        for path in sorted((RESULTS/'chunks').glob('chunk-*.zip')):
            chunks[path.name]=inventory['chunks/'+path.name]['sha256']
            with ZipFile(path) as z:summary=json.loads(z.read('summary.json'))
            for key in ('games','expanded','clean','teacher','anchor','starved_units','teacher_short_turns','teacher_deaths','supply_purchases'):
                totals[key]+=summary.get(key,0)
        if totals['games']!=1000:raise ValueError('Incomplete industrial corpus')
        manifest=dict(latest=latest.name,resume_epoch=5,evaluation_only=True,
            collected=dict(totals),source_chunks_sha256=chunks,files={name:sha(p) for name,p in files.items()})
        dest=STAGE/'evaluation-dataset';dest.mkdir(exist_ok=True)
        (dest/'dataset-metadata.json').write_text(json.dumps(dict(id='northwindlight/zhanguo-industry-bc-eval-handoff-20261123',
            title='Zhanguo Industry BC Evaluation Handoff 20261123',licenses=[dict(name='other')]),indent=2))
        with ZipFile(dest/'handoff.bundle','w',ZIP_STORED) as z:
            for name,path in files.items():z.write(path,name)
            z.writestr('handoff-manifest.json',json.dumps(manifest,indent=2))
        (STAGE/'evaluation-handoff.json').write_text(json.dumps(manifest,indent=2))
        print(json.dumps(dict(latest=latest.name,bytes=(dest/'handoff.bundle').stat().st_size,evaluation_only=True)),flush=True)
        return
    if '--checkpoint-only' in sys.argv:
        if int(latest.stem.split('-')[1])<4:
            raise RuntimeError('Fourth epoch is not yet verified locally')
        chosen={latest.name,latest.with_suffix('.json').name,'best.json'}
        for name in [latest.stem+'-native.pt',latest.stem+'-eval.json']:
            if (RESULTS/name).exists():chosen.add(name)
        files={'work/'+name:RESULTS/name for name in chosen}
        for name,path in files.items():
            if name.endswith('.pt') and sha(path)!=inventory[path.name]['sha256']:
                raise ValueError('Supplemental checkpoint checksum mismatch')
        manifest=dict(latest=latest.name,resume_epoch=int(latest.stem.split('-')[1])+1,
                      files={name:sha(path) for name,path in files.items()})
        dest=STAGE/'checkpoint-dataset'
        dest.mkdir(exist_ok=True)
        (dest/'dataset-metadata.json').write_text(json.dumps(dict(
            id='northwindlight/zhanguo-industry-bc-checkpoint-20261123',
            title='Zhanguo Industry BC Checkpoint 20261123',licenses=[dict(name='other')]),indent=2))
        with ZipFile(dest/'checkpoint.bundle','w',ZIP_STORED) as z:
            for name,path in files.items():z.write(path,name)
            z.writestr('handoff-tail.json',json.dumps(manifest,indent=2))
        (STAGE/'checkpoint-handoff.json').write_text(json.dumps(manifest,indent=2))
        print(json.dumps({'checkpoint':latest.name,'files':len(files),'next_epoch':manifest['resume_epoch']}),flush=True)
        return
    best=json.loads((RESULTS/'best.json').read_text())
    with ZipFile(RESULTS/'code.zip') as old, ZipFile(STAGE/'code.zip','w',ZIP_DEFLATED) as new:
        for name in old.namelist():
            data=(ROOT/name).read_bytes() if name=='industry_bc_run.py' else old.read(name)
            new.writestr(name,data)
    files={'code.zip':STAGE/'code.zip',
           'original/base.pt':ROOT/'mixed-bc-results/best-batch-mean.pt',
           'original/base-native.pt':ROOT/'mixed-bc-results/best-batch-mean-native.pt'}
    for path in (ROOT/'mixed-bc-results/chunks').glob('chunk-*.zip'):
        files['original/chunks/'+path.name]=path
    for path in (RESULTS/'chunks').glob('chunk-*.zip'):
        if sha(path)!=inventory['chunks/'+path.name]['sha256']:
            raise ValueError('Demonstration chunk checksum mismatch')
        files['work/chunks/'+path.name]=path
    chosen={latest.name,best['checkpoint'],best['native'],'best.json','baseline.json',
            'epoch-02-eval.json','epoch-02.json',latest.with_suffix('.json').name,'curriculum.json'}
    for name in chosen:
        path=RESULTS/name
        if not path.is_file():raise FileNotFoundError(path)
        if name.endswith('.pt') and sha(path)!=inventory[name]['sha256']:
            raise ValueError('Checkpoint checksum mismatch: '+name)
        files['work/'+name]=path
    manifest=dict(latest=latest.name,resume_epoch=int(latest.stem.split('-')[1])+1,
                  files={name:sha(p) for name,p in files.items()})
    bundle=STAGE/'dataset/handoff.bundle'
    temporary=bundle.with_suffix('.partial')
    with ZipFile(temporary,'w',ZIP_STORED) as z:
        for name,path in files.items():z.write(path,name)
        z.writestr('handoff-manifest.json',json.dumps(manifest,indent=2))
    temporary.replace(bundle)
    manifest['bundle_sha256']=sha(bundle)
    (STAGE/'handoff.json').write_text(json.dumps(manifest,indent=2))
    print(json.dumps({'latest':latest.name,'next_epoch':manifest['resume_epoch'],
                      'files':len(files),'bytes':bundle.stat().st_size,'sha256':manifest['bundle_sha256']}),flush=True)


if __name__=='__main__':main()
