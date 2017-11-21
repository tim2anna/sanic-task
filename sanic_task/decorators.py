#!/usr/bin/python
# -*- coding: utf-8 -*-

from functools import wraps
from . import TaskManager
from .queue import Queue
from .utils import import_attribute


class Signature(object):

    def __init__(self, queue, job):
        self.queue = queue
        self.job = job
        self.dependent = None

    def __or__(self, other):
        self.job.next_job_id = other.job.id
        self.job.save()
        other.dependent = self
        return other

    def delay(self):
        first = self
        while first.dependent:
            first = first.dependent
        self.queue.enqueue_job(first.job)


class Task(object):
    """ 任务装饰器，给函数增加delay方法。调用delay后，会创建一个Job。

    >>> @Task()
    ... def add(x, y):
    ...     return x + y
    ...
    ... add.delay(x, y)
    """

    def __init__(self, queue='default', connection=None, timeout=None, result_ttl=None, ttl=None):
        if result_ttl is None:
            result_ttl = TaskManager.settings.DEFAULT_RESULT_TTL
        self.result_ttl = result_ttl
        if isinstance(queue, str):
            queue = Queue(name=queue)
        self.queue = queue
        self.connection = connection
        self.timeout = timeout
        self.ttl = ttl
        self.job_class = import_attribute(TaskManager.settings.JOB_CLASS)

    def __call__(self, f):
        @wraps(f)
        def delay(*args, **kwargs):
            return self.queue.enqueue_call(f, args=args, kwargs=kwargs)
        f.delay = delay

        @wraps(f)
        def s(*args, **kwargs):
            job = self.job_class.create(f, args=args, kwargs=kwargs)
            job.save()
            return Signature(self.queue, job)

        f.s = s

        return f


