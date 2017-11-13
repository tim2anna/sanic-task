#!/usr/bin/python
# -*- coding: utf-8 -*-

from sanic_script import Manager
from .worker import Worker


manager = Manager(usage="sanic-task")


@manager.command
def start():
    worker = Worker(['default'])
    worker.work()


import requests

def count_words_at_url(url):
    resp = requests.get(url)
    print(resp.text.split())
    # raise ValueError('12321')
    import time
    # time.sleep(300)
    return len(resp.text.split())


from .queue import Queue


@manager.command
def add_job():
    q = Queue()
    result = q.enqueue(count_words_at_url, 'http://nvie.com')
    print(result)
