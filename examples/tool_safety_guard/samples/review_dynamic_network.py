import requests


def get_runtime_url():
    return input()


target_url = get_runtime_url()
requests.get(target_url)
