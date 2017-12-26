#!/usr/bin/python
# -*- coding: utf-8 -*-

from sanic_script import Manager
from .worker import Worker
from .beat import Beat


manager = Manager(usage="sanic-task")


@manager.command
def worker():
    Worker(['default']).run()


@manager.command
def beat():
    beat = Beat()
    beat.run()

