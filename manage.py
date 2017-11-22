#!/usr/bin/python3
# -*- coding: utf-8 -*-

from runserver import app
from sanic_script import Manager
from sanic_task.command import manager as task_manager

manager = Manager(app)
manager.add_command('task', task_manager)


import requests
from sanic_task.decorators import Task


@Task()
def count_words_at_url(url):
    resp = requests.get(url)
    print(resp.text.split())
    return len(resp.text.split())


@manager.command
def add_job():
    s = count_words_at_url.s('http://nvie.com') | count_words_at_url.s('http://baidu.com')
    s.delay()


if __name__ == "__main__":
    manager.run()
