#!/usr/bin/python
# -*- coding: utf-8 -*-

from datetime import datetime
import redis

from functools import total_ordering
from . import TaskManager
from .exceptions import (DequeueTimeout, InvalidJobDependency, NoSuchJobError)
from .job import JobStatus
from .utils import import_attribute, as_text


@total_ordering
class Queue(object):
    redis_queue_namespace_prefix = 'rq:queue:'
    redis_queues_keys = 'rq:queues'  # Redis集合保存所有的队列key
    connection = redis.Redis(connection_pool=TaskManager.settings.REDIS_POOL)

    @classmethod
    def all(cls):
        """ 所有队列 """
        return [cls.from_queue_key(rq_key) for rq_key in cls.connection.smembers(cls.redis_queues_keys)]

    @classmethod
    def from_queue_key(cls, queue_key):
        name = queue_key.split(':')[-1]
        return cls(name)

    def __init__(self, name='default', default_timeout=None, async=True):
        prefix = self.redis_queue_namespace_prefix
        self.name = name
        self._key = '{0}{1}'.format(prefix, name)
        self._default_timeout = default_timeout
        self._async = async
        self.job_class = import_attribute(TaskManager.settings.JOB_CLASS)

    def __len__(self):
        return self.count

    def __bool__(self):
        return True

    def __iter__(self):
        yield self

    @property
    def key(self):
        """Returns the Redis key for this Queue."""
        return self._key

    def empty(self):
        """ 清空队列 """
        script = b"""
            local prefix = "rq:job:"
            local q = KEYS[1]
            local count = 0
            while true do
                local job_id = redis.call("lpop", q)
                if job_id == false then
                    break
                end
                -- Delete the relevant keys
                redis.call("del", prefix..job_id)
                redis.call("del", prefix..job_id..":dependents")
                count = count + 1
            end
            return count
        """
        script = self.connection.register_script(script)
        return script(keys=[self.key])

    def delete(self, delete_jobs=True):
        """ 删除队列 """
        if delete_jobs:
            self.empty()

        with self.connection.pipeline() as pipeline:
            pipeline.srem(self.redis_queues_keys, self._key)
            pipeline.delete(self._key)
            pipeline.execute()

    def is_empty(self):
        """ 队列是否为空 """
        return self.count == 0

    def fetch_job(self, job_id):
        try:
            return self.job_class.fetch(job_id)
        except NoSuchJobError:
            self.remove(job_id)

    def get_job_ids(self, offset=0, length=-1):
        """ 返回队列中所有的Job id """
        start = offset
        end = (offset + length - 1) if length >= 0 else length
        return [job_id for job_id in self.connection.lrange(self.key, start, end)]

    def get_jobs(self, offset=0, length=-1):
        job_ids = self.get_job_ids(offset, length)
        jobs = [self.fetch_job(job_id) for job_id in job_ids]
        return [job for job in jobs if job is not None]

    @property
    def job_ids(self):
        """Returns a list of all job IDS in the queue."""
        return self.get_job_ids()

    @property
    def jobs(self):
        """Returns a list of all (valid) jobs in the queue."""
        return self.get_jobs()

    @property
    def count(self):
        """Returns a count of all messages in the queue."""
        return self.connection.llen(self.key)

    def remove(self, job_or_id):
        """ 从队列中移除Job """
        job_id = job_or_id.id if isinstance(job_or_id, self.job_class) else job_or_id
        return self.connection.lrem(self.key, 0, job_id)

    def push_job_id(self, job_id):
        self.connection.rpush(self.key, job_id)

    def enqueue_call(self, func, args=None, kwargs=None, timeout=None,
                     result_ttl=None, ttl=None, description=None, job_id=None):
        """Creates a job to represent the delayed function call and enqueues
        it.
        It is much like `.enqueue()`, except that it takes the function's args
        and kwargs as explicit arguments.  Any kwargs passed to this function
        contain options for RQ itself.
        """
        timeout = timeout or self._default_timeout
        result_ttl = result_ttl

        job = self.job_class.create(
            func, args=args, kwargs=kwargs,
            result_ttl=result_ttl, ttl=ttl, status=JobStatus.QUEUED,
            description=description, timeout=timeout, id=job_id, origin=self.name)
        job = self.enqueue_job(job)
        return job

    def enqueue_job(self, job):
        """ Job入队 """
        job.status = JobStatus.QUEUED
        job.origin = self.name
        job.enqueued_at = datetime.now()
        job.save()
        self.connection.rpush(self.key, job.id)
        return job

    @classmethod
    def lpop(cls, queue_keys, timeout):
        """ 左边阻塞出队 """
        if timeout == 0:
            raise ValueError('Not support indefinite timeouts. Please pick a timeout value > 0')
        result = cls.connection.blpop(queue_keys, timeout)
        if result is None:
            raise DequeueTimeout(timeout, queue_keys)
        queue_key, job_id = result
        return queue_key, job_id

    @classmethod
    def dequeue_any(cls, queues, timeout):
        """ 出队Job """
        job_class = import_attribute(TaskManager.settings.JOB_CLASS)

        while True:
            queue_keys = [q.key for q in queues]
            result = cls.lpop(queue_keys, timeout)
            if result is None:
                return None
            queue_key, job_id = map(as_text, result)
            queue = cls.from_queue_key(queue_key)
            try:
                job = job_class.fetch(job_id)
            except NoSuchJobError:
                continue
            return job, queue
        return None, None

    # Total ordering defition (the rest of the required Python methods are
    # auto-generated by the @total_ordering decorator)
    def __eq__(self, other):  # noqa
        if not isinstance(other, Queue):
            raise TypeError('Cannot compare queues to other objects')
        return self.name == other.name

    def __lt__(self, other):
        if not isinstance(other, Queue):
            raise TypeError('Cannot compare queues to other objects')
        return self.name < other.name

    def __hash__(self):  # pragma: no cover
        return hash(self.name)

    def __repr__(self):  # noqa  # pragma: no cover
        return '{0}({1!r})'.format(self.__class__.__name__, self.name)

    def __str__(self):
        return '<{0} {1}>'.format(self.__class__.__name__, self.name)
