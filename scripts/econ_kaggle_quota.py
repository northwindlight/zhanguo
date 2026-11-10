"""Read accelerator quota through the authenticated Kaggle CLI environment."""
import json
import os


def main():
    if os.environ.get('KAGGLE_NO_PROXY')!='1' and not (os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')):
        proxy=os.environ.get('KAGGLE_PROXY','http://192.168.1.1:20171')
        os.environ.update(HTTPS_PROXY=proxy,HTTP_PROXY=proxy,https_proxy=proxy,http_proxy=proxy)
    from kaggle.api.kaggle_api_extended import KaggleApi
    api=KaggleApi();api.authenticate()
    with api.build_kaggle_client() as client:
        result=client.kernels.kernels_api_client.get_accelerator_quota_statistics()
    def details(quota):
        names=('time_used','time_reserved','total_time_allowed')
        return {name:getattr(quota,name).total_seconds()/3600 if getattr(quota,name) is not None else None
                for name in names}
    print(json.dumps(dict(gpu=details(result.gpu_quota),tpu=details(result.tpu_quota),
                          refresh=result.quota_refresh_time.isoformat() if result.quota_refresh_time else None)))


if __name__=='__main__':main()
