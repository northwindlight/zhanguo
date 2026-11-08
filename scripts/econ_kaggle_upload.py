"""Upload the small recovery bundle with fresh task-scoped blob tokens.

Run in the Kaggle CLI venv. Interrupted uploads can retain expired blob tokens;
clear only cache entries for this bundle, leaving account authentication intact.
"""
import argparse
import json
import os
from pathlib import Path
import re


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--folder',type=Path,required=True)
    p.add_argument('--message',required=True)
    args=p.parse_args();folder=args.folder.resolve()
    if os.environ.get('KAGGLE_NO_PROXY')!='1' and not (os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')):
        proxy=os.environ.get('KAGGLE_PROXY','http://192.168.1.1:20171')
        os.environ.update(HTTPS_PROXY=proxy,HTTP_PROXY=proxy,https_proxy=proxy,http_proxy=proxy)
    from kaggle.api.kaggle_api_extended import KaggleApi,ResumableUploadContext
    context=ResumableUploadContext()
    for name in ('runtime.bin','recovery.bin','bundle-manifest.json'):
        Path(context.get_upload_info_file_path(str(folder/name))).unlink(missing_ok=True)
    api=KaggleApi();api.authenticate()
    dataset=json.loads((folder/'dataset-metadata.json').read_text())['id']
    before=json.loads(api.dataset_status(dataset,format='json'))['current_version_number']
    result=api.dataset_create_version(str(folder),args.message,quiet=True,convert_to_csv=False)
    if str(result.status).lower()!='ok':raise RuntimeError('Dataset version was rejected')
    match=re.search(r'/versions/(\d+)',result.url)
    # This API may return only the dataset URL. The controller is the sole
    # writer and verifies this numbered version's manifest before GPU launch.
    version=int(match.group(1)) if match else before+1
    print('DATASET_VERSION '+json.dumps(dict(version=version)))


if __name__=='__main__':main()
