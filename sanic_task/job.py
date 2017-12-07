#!/usr/bin/python
# -*- coding: utf-8 -*-

import json
import inspect
from functools import partial
from uuid import uuid4
from enum import Enum
from datetime import datetime
import pickle
import redis


from . import TaskManager
from .exceptions import NoSuchJobError, UnpickleError
from .local import LocalStack
from .utils import import_attribute, as_text

# Serialize pickle dumps using the highest pickle protocol (binary, default
# uses ascii)
dumps = partial(pickle.dumps, protocol=pickle.HIGHEST_PROTOCOL)
loads = pickle.loads


def decode_redis_hash(h):
    return dict((as_text(k), h[k]) for k in h)


class JobStatus(Enum):
    QUEUED = 'queued'  # 排队中
    FINISHED = 'finished'  # 已完成
    FAILED = 'failed'  # 失败
    STARTED = 'started'  # 已开始
    DEFERRED = 'deferred'  # 延缓
    CANCELED = 'canceled'  # 已取消
    TIMEOUT = 'timeout'  # 超时


# Sentinel value to mark that some of our lazily evaluated properties have not
# yet been evaluated.
UNEVALUATED = object()


def unpickle(pickled_string):
    """Unpickles a string, but raises a unified UnpickleError in case anything
    fails.
    This is a helper method to not have to deal with the fact that `loads()`
    potentially raises many types of exceptions (e.g. AttributeError,
    IndexError, TypeError, KeyError, etc.)
    """
    try:
        obj = loads(pickled_string)
    except Exception as e:
        raise UnpickleError('Could not unpickle', pickled_string, e)
    return obj


def cancel_job(job_id, connection=None):
    """ 取消Job """
    Job.fetch(job_id, connection=connection).cancel()


def get_current_job():
    """ 获取当前正在执行的Job """
    return _job_stack.top


class BaseJob(object):

    def __init__(self, id=None):
        self.id = id
        self.create_time = datetime.now()  # 创建时间
        self._func_name = UNEVALUATED
        self._instance = UNEVALUATED  # 对应的方法
        self._args = UNEVALUATED
        self._kwargs = UNEVALUATED
        self.desc = None  # 描述
        self.origin = None  # 来源队列名称
        self.enqueue_time = None  # 入队时间
        self.start_time = None  # 开始时间
        self.end_time = None  # 结束时间
        self._result = None  # 结果
        self.exc_info = None  # 执行信息
        self.timeout = TaskManager.settings.DEFAULT_WORKER_TTL  # 超时时间
        self.result_ttl = TaskManager.settings.DEFAULT_RESULT_TTL  # 结果保存时间
        self.ttl = TaskManager.settings.DEFAULT_WORKER_TTL  # Job的生命周期
        self._status = None  # 状态
        self.next_job_id = None  # 下个被执行的Job id
        self._next_job = None  # 下个被执行的Job
        self.history = []  # 执行历史记录
        self.retry = TaskManager.settings.DEFAULT_RETRY  # 失败重试次数
        self.retry_delay = TaskManager.settings.DEFAULT_RETRY_DELAY  # 重试时间间隔
        self.retied = 0  # 已重试次数
        self.next_run_at = None  # 下次运行时间

    @property
    def key(self):
        """The Redis key that is used to store job hash under."""
        return 'rq:job:' + self.id

    @property
    def status(self):
        return self._status

    @status.setter
    def status(self, status):
        self._status = status

    @property
    def is_finished(self):
        """ 是否已经完成 """
        return self.status == JobStatus.FINISHED

    @property
    def is_queued(self):
        """ 是否正在排队 """
        return self.status == JobStatus.QUEUED

    @property
    def is_failed(self):
        """ 是否已经失败 """
        return self.status == JobStatus.FAILED

    @property
    def is_started(self):
        """ 是否已经开始 """
        return self.status == JobStatus.STARTED

    @property
    def func(self):
        """ 对应的函数 """
        func_name = self.func_name
        if func_name is None:
            return None
        if self.instance:
            return getattr(self.instance, func_name)
        return import_attribute(self.func_name)

    @property
    def func_name(self):
        return self._func_name

    @func_name.setter
    def func_name(self, value):
        self._func_name = value

    @property
    def instance(self):
        return self._instance

    @instance.setter
    def instance(self, value):
        self._instance = value

    @property
    def args(self):
        return self._args

    @args.setter
    def args(self, value):
        self._args = value

    @property
    def kwargs(self):
        return self._kwargs

    @kwargs.setter
    def kwargs(self, value):
        self._kwargs = value

    @classmethod
    def exists(cls, job_id):
        """ Job是否存在 """
        raise NotImplementedError()

    @classmethod
    def fetch(cls, id):
        """ 通过id获取对应的Job """
        raise NotImplementedError()

    @classmethod
    def create(cls, func, args=(), kwargs={},
               result_ttl=None, ttl=None, status=None, desc=None,
               next_job=None, timeout=None, id=None, origin=None):
        """ 创建一个Job对象 """
        if not isinstance(args, (tuple, list)):
            raise TypeError('{0!r} is not a valid args list'.format(args))
        if not isinstance(kwargs, dict):
            raise TypeError('{0!r} is not a valid kwargs dict'.format(kwargs))

        job = cls()
        if id is not None:
            job.id = id

        if origin is not None:
            job.origin = origin

        # Set the core job tuple properties
        job._instance = None
        if inspect.ismethod(func):
            job._instance = func.__self__
            job._func_name = func.__name__
        elif inspect.isfunction(func) or inspect.isbuiltin(func):
            job._func_name = '{0}.{1}'.format(func.__module__, func.__name__)
        elif isinstance(func, str):
            job._func_name = as_text(func)
        elif not inspect.isclass(func) and hasattr(func, '__call__'):  # a callable class instance
            job._instance = func
            job._func_name = '__call__'
        else:
            raise TypeError('Expected a callable or a string, but got: {0}'.format(func))
        job._args = args
        job._kwargs = kwargs

        job.desc = desc or job.get_call_string()
        job.result_ttl = result_ttl or TaskManager.settings.DEFAULT_RESULT_TTL
        job.ttl = ttl or TaskManager.settings.DEFAULT_WORKER_TTL
        job.timeout = timeout or TaskManager.settings.DEFAULT_WORKER_TTL
        job._status = status

        if next_job is not None:
            job.next_job_id = next_job.id if isinstance(next_job, cls) else next_job
        return job

    def save(self):
        """ 将Job保存起来 """
        raise NotImplementedError()

    @property
    def next_job(self):
        """ 下个被执行的Job """
        if self.next_job_id is None:
            return None
        if self._next_job is not None:
            return self._next_job
        job = self.fetch(self.next_job_id)
        self._next_job = job
        return job

    # def update(self, kwargs):
    #     """ 更新Job属性 """
    #     raise NotImplementedError()

    def to_dict(self):
        obj = {
            'id': self.id,
            'create_time': self.create_time.strftime(TaskManager.settings.DATE_FMT) if isinstance(self.create_time, datetime) else self.create_time,
            'origin': self.origin,
            'desc': self.desc,
            'enqueue_time': self.enqueue_time.strftime(TaskManager.settings.DATE_FMT) if isinstance(self.enqueue_time, datetime) else None,
            'start_time': self.start_time.strftime(TaskManager.settings.DATE_FMT) if isinstance(self.start_time, datetime) else None,
            'end_time': self.end_time.strftime(TaskManager.settings.DATE_FMT) if isinstance(self.end_time, datetime) else None,
            'exc_info': self.exc_info,
            'timeout': self.timeout,
            'result_ttl': self.result_ttl,
            'status': str(self._status),
            'next_job_id': self.next_job_id,
            'ttl': self.ttl,
            'func_name': self.func_name,
            'instance': self.instance,
            'args': self.args,
            'kwargs': self.kwargs,
            'result': self._result,
        }
        return obj

    @classmethod
    def to_obj(cls, _dict):
        job = cls()
        for attr, value in _dict.items():
            setattr(job, attr, value)
        return job

    def cancel(self):
        """ 取消Job """
        from .queue import Queue
        q = Queue(name=self.origin)
        q.remove(self)
        self.status = JobStatus.CANCELED
        self.save()

    def perform(self):
        """ 执行Job """
        _job_stack.push(self)
        try:
            self._result = self._execute()
        finally:
            assert self is _job_stack.pop()
        return self._result

    def _execute(self):
        """ 运行Job """
        return self.func(*self.args, **self.kwargs)

    def get_ttl(self, default_ttl=None):
        """ Job的生命周期 """
        return default_ttl if self.ttl is None else self.ttl

    def get_result_ttl(self, default_ttl=None):
        """ 结果保存期限 """
        return default_ttl if self.result_ttl is None else self.result_ttl

    def get_call_string(self):
        """ 将方法名和调用参数拼接成字符串 """
        if self.func_name is None:
            return None

        arg_list = [as_text(repr(arg)) for arg in self.args]
        kwargs = ['{0}={1}'.format(k, as_text(repr(v))) for k, v in self.kwargs.items()]

        arg_list += sorted(kwargs)
        args = ', '.join(arg_list)

        return '{0}({1})'.format(self.func_name, args)

    def failure(self):
        raise NotImplementedError()

    def success(self):
        raise NotImplementedError()

    @classmethod
    def count(cls, status):
        raise NotImplementedError()


class RedisJob(BaseJob):

    redis_client = redis.Redis(connection_pool=TaskManager.settings.REDIS_POOL)

    save_jobs = 'rq:jobs:'
    failure_jobs = 'rq:failure_jobs:'
    enqueue_jobs = 'rq:enqueue_jobs:'
    success_jobs = 'rq:success_jobs:'

    @classmethod
    def exists(cls, job_id):
        """ Job是否存在 """
        return cls.redis_client.exists(job_id)

    @classmethod
    def fetch(cls, job_id):
        """ 通过id获取对应的Job """
        job = cls.redis_client.get(cls.save_jobs + job_id)
        if job:
            return cls.to_obj(json.loads(job))
        else:
            return None

    def save(self):
        """ 将Job保存起来 """
        if self.id is None:
            self.id = str(uuid4())
        self.redis_client.set(self.save_jobs + self.id, json.dumps(self.to_dict()), self.result_ttl)

    def failure(self):
        self.redis_client.delete(self.enqueue_jobs + self.id)
        self.redis_client.set(self.failure_jobs + self.id, json.dumps(self.to_dict()), self.result_ttl)

    def success(self):
        self.redis_client.delete(self.enqueue_jobs + self.id)
        self.redis_client.set(self.success_jobs + self.id, json.dumps(self.to_dict()), self.result_ttl)

    @classmethod
    def all_jobs(cls, status=None):
        jobs = []
        if status == JobStatus.FAILED:
            keys = cls.redis_client.keys('rq:failure_jobs:*')
            for key in keys:
                value = cls.redis_client.get(key)
                if value:
                    jobs.append(cls.to_obj(json.loads(value)))
        return jobs

    @classmethod
    def count(cls, status=None):
        if status == JobStatus.STARTED:
            return len(_job_stack)
        elif status == JobStatus.QUEUED:
            return len(cls.redis_client.keys(cls.enqueue_jobs + '*'))
        elif status == JobStatus.FINISHED:
            return len(cls.redis_client.keys(cls.success_jobs + '*'))
        elif status == JobStatus.FAILED:
            return len(cls.redis_client.keys(cls.failure_jobs + '*'))
        else:
            return 0


class MongoJob(object):

    def to_dict(self):
        pass


# Job栈：执行Job会入栈；执行完或者失败都会出栈
_job_stack = LocalStack()
