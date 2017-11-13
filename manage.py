#!/usr/bin/python3
# -*- coding: utf-8 -*-

from monitor import app


from sanic_script import Manager
from sanic_task.command import manager as task_manager

manager = Manager(app)
manager.add_command('task', task_manager)


if __name__ == "__main__":
    manager.run()
