#!/usr/bin/python
# -*- coding: utf-8 -*-

from sanic_script import Manager
from .worker import Worker
from .decorators import Task


manager = Manager(usage="sanic-task")


@manager.command
def start():
    worker = Worker(['default'])
    worker.work()


import requests


@Task()
def count_words_at_url(url):
    resp = requests.get(url)
    print(resp.text.split())
    # raise ValueError('12321')
    import time
    # time.sleep(300)
    return len(resp.text.split())


@manager.command
def add_job():
    s = count_words_at_url.s('http://nvie.com') | count_words_at_url.s('http://baidu.com')
    s.delay()
