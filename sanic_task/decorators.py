#!/usr/bin/python
# -*- coding: utf-8 -*-

from functools import wraps
from . import TaskManager
from .queue import Queue


class Task(object):
    """ 任务装饰器，给函数增加delay方法。调用delay后，会创建一个Job。

    >>> @Task()
    ... def add(x, y):
    ...     return x + y
    ...
    ... add.delay(x, y)
    """

    def __init__(self, queue, connection=None, timeout=None, result_ttl=None, ttl=None):
        if result_ttl is None:
            result_ttl = TaskManager.settings.DEFAULT_RESULT_TTL
        self.result_ttl = result_ttl
        if isinstance(queue, str):
            queue = Queue(name=self.queue)
        self.queue = queue
        self.connection = connection
        self.timeout = timeout
        self.ttl = ttl

    def __call__(self, f):
        @wraps(f)
        def delay(*args, **kwargs):
            depends_on = kwargs.pop('depends_on', None)
            at_front = kwargs.pop('at_front', False)  # 放入队列前端，它会立刻被pop出执行。

            return self.queue.enqueue_call(f, args=args, kwargs=kwargs, timeout=self.timeout, ttl=self.ttl,
                                           result_ttl=self.result_ttl, depends_on=depends_on, at_front=at_front)
        f.delay = delay
        return f
