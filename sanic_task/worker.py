#!/usr/bin/python
# -*- coding: utf-8 -*-

import errno
import os
import signal
import socket
import sys
import traceback
from enum import Enum
import redis
from datetime import datetime

from . import TaskManager
from .exceptions import DequeueTimeout, StopRequested
from .job import JobStatus
from .queue import Queue
from .timeouts import UnixSignalJobTimout, JobTimeoutException
from .utils import as_text, import_attribute
from .logutils import logger


class WorkerStatus(Enum):
    # worker是一个进程，要么没有运行，要么运行中，所有没有stopped状态。
    STARTED = 'started'
    BUSY = 'busy'
    IDLE = 'idle'  # 空闲


class Worker(object):
    redis_worker_namespace_prefix = 'rq:worker:'
    redis_workers_keys = 'rq:workers'
    queue_class = Queue

    # TODO：连接可能需要放在实例变量中去
    connection = redis.Redis(connection_pool=TaskManager.settings.REDIS_POOL)

    @classmethod
    def all(cls, job_class=None, queue_class=None):
        """ 所有worker """

        workers = []
        for key in cls.connection.smembers(cls.redis_workers_keys):
            worker = cls.find_by_key(as_text(key), job_class=job_class, queue_class=queue_class)
            if worker is not None:
                workers.append(worker)

        return workers

    @classmethod
    def find_by_key(cls, worker_key, job_class=None, queue_class=None):
        """ 通过Redis Key返回worker的实例 """
        prefix = cls.redis_worker_namespace_prefix
        if not worker_key.startswith(prefix):
            raise ValueError('Not a valid RQ worker key: {0}'.format(worker_key))

        if not cls.connection.exists(worker_key):
            cls.connection.srem(cls.redis_workers_keys, worker_key)
            return None

        name = worker_key[len(prefix):]
        worker = cls([], name, job_class=job_class, queue_class=queue_class)
        worker.refresh()

        return worker

    def __init__(self, queues: list, name=None, default_result_ttl=None, default_worker_ttl=None,):

        # TODO: 默认结果保存时限，保存在Redis中，以后结果统一保存在MongoDB中
        if default_result_ttl is None:
            default_result_ttl = TaskManager.settings.DEFAULT_RESULT_TTL
        self.default_result_ttl = default_result_ttl  # 结果保存的生命周期

        if default_worker_ttl is None:
            default_worker_ttl = TaskManager.settings.DEFAULT_WORKER_TTL
        self.default_worker_ttl = default_worker_ttl  # worker的默认生命周期

        self.job_class = import_attribute(TaskManager.settings.JOB_CLASS)
        self.queues = [Queue(name=q) for q in queues]
        self._name = name
        self._exc_handlers = []
        self.state = 'starting'  # 状态：开始中
        self._is_fork = False  # worker是否是fork来的
        self._fork_pid = 0
        self._stop_requested = False
        self.last_cleaned_at = None
        self.successful_job_count = 0
        self.failed_job_count = 0
        self.total_working_time = 0

    def validate_queues(self):
        """Sanity check for the given queues."""
        for queue in self.queues:
            if not isinstance(queue, self.queue_class):
                raise TypeError('{0} is not of type {1} or string types'.format(queue, self.queue_class))

    def queue_names(self):
        """Returns the queue names of this worker's queues."""
        return list(map(lambda q: q.name, self.queues))

    def queue_keys(self):
        """Returns the Redis keys representing this worker's queues."""
        return list(map(lambda q: q.key, self.queues))

    @property
    def name(self):
        if self._name is None:
            hostname = socket.gethostname()
            shortname, _, _ = hostname.partition('.')
            self._name = '{0}.{1}'.format(shortname, self.pid)
        return self._name

    @property
    def key(self):
        return self.redis_worker_namespace_prefix + self.name

    @property
    def pid(self):
        """ 当前进程ID """
        return os.getpid()

    def register_birth(self):
        """Registers its own birth."""
        logger.debug('Registering birth of worker {0}'.format(self.name))
        if self.connection.exists(self.key) and \
                not self.connection.hexists(self.key, 'death'):
            msg = 'There exists an active worker named {0!r} already'
            raise ValueError(msg.format(self.name))
        key = self.key
        queues = ','.join(self.queue_names())
        with self.connection.pipeline() as p:
            p.delete(key)
            now_in_string = datetime.now().strftime(TaskManager.settings.DATE_FMT)
            p.hset(key, 'last_heartbeat', now_in_string)
            p.hset(key, 'queues', queues)
            p.sadd(self.redis_workers_keys, key)
            p.expire(key, self.default_worker_ttl)
            p.execute()

    def register_death(self):
        """Registers its own death."""
        logger.debug('Registering death')
        with self.connection.pipeline() as p:
            # We cannot use self.state = 'dead' here, because that would
            # rollback the pipeline
            p.srem(self.redis_workers_keys, self.key)
            p.hset(self.key, 'death', datetime.now().strftime(TaskManager.settings.DATE_FMT))
            p.expire(self.key, 60)
            p.execute()

    def set_shutdown_requested_date(self):
        """ 设置当前worker被请求关闭的时间 """
        self.connection.hset(self.key, 'shutdown_requested_date', datetime.now().strftime(TaskManager.settings.DATE_FMT))

    @property
    def shutdown_requested_date(self):
        """Fetches shutdown_requested_date from Redis."""
        shutdown_requested_timestamp = self.connection.hget(self.key, 'shutdown_requested_date')
        if shutdown_requested_timestamp is not None:
            return datetime.strptime(as_text(shutdown_requested_timestamp), TaskManager.settings.DATE_FMT)

    @property
    def death_date(self):
        """Fetches death date from Redis."""
        death_timestamp = self.connection.hget(self.key, 'death')
        if death_timestamp is not None:
            return datetime.strptime(as_text(death_timestamp), TaskManager.settings.DATE_FMT)

    def set_current_job_id(self, job_id):
        self.connection.hset(self.key, 'current_job', job_id)

    def get_current_job_id(self):
        return self.connection.hget(self.key, 'current_job')

    def get_current_job(self):
        """ 获取当前正在执行的Job """
        job_id = self.get_current_job_id()
        return self.job_class.fetch(job_id) if not job_id else None

    def _install_signal_handlers(self):
        """Installs signal handlers for handling SIGINT and SIGTERM
        gracefully.
        """

        signal.signal(signal.SIGINT, self.request_stop)
        signal.signal(signal.SIGTERM, self.request_stop)

    def kill_fork(self, sig=signal.SIGKILL):
        """ 杀掉fork出的worker """
        try:
            os.kill(self._fork_pid, sig)
        except OSError as e:
            if e.errno == errno.ESRCH:
                logger.debug('Horse already dead')
            else:
                raise

    def request_force_stop(self, signum, frame):
        """ 强制关闭worker """
        logger.warning('Cold shut down')

        if self._fork_pid:
            msg = 'Taking down horse {0} with me'.format(self._fork_pid)
            logger.debug(msg)
            self.kill_fork()
        raise SystemExit()

    def request_stop(self, signum, frame):
        """ 关闭worker """
        logger.debug('Got signal {0}'.format(signal.Signals(signum).name))

        signal.signal(signal.SIGINT, self.request_force_stop)
        signal.signal(signal.SIGTERM, self.request_force_stop)

        logger.warning('Warm shut down requested')

        if self.state == WorkerStatus.BUSY:  # 当前worker正在处理JOb
            self._stop_requested = True
            self.set_shutdown_requested_date()
            logger.debug('Stopping after current horse is finished. Press Ctrl+C again for a cold shutdown.')
        else:
            raise StopRequested()

    def work(self, burst=False):
        """ 开始运行 """
        self._install_signal_handlers()

        did_perform_work = False
        self.register_birth()
        logger.info("RQ worker {0!r} started".format(self.key))
        self.state = WorkerStatus.STARTED

        qnames = self.queue_names()
        logger.info('*** Listening on {0}...'.format(', '.join(qnames)))

        try:
            while True:
                try:
                    if self._stop_requested:
                        logger.info('Stopping on request')
                        break

                    timeout = None if burst else max(1, self.default_worker_ttl)

                    result = self.dequeue_job_and_maintain_ttl(timeout)
                    if result is None:
                        if burst:
                            logger.info("RQ worker {0!r} done, quitting".format(self.key))
                        break

                    job, queue = result
                    self.state = WorkerStatus.BUSY  # 设置worker状态为忙碌
                    self.fork_work(job, queue)
                    self.monitor_work_fork(job)
                    self.state = WorkerStatus.IDLE  # 设置worker状态为空闲

                    did_perform_work = True
                except StopRequested:
                    break
        finally:
            if not self._is_fork:
                self.register_death()
        return did_perform_work

    def dequeue_job_and_maintain_ttl(self, timeout):
        self.state = WorkerStatus.IDLE
        while True:
            try:
                self.heartbeat()
                result = self.queue_class.dequeue_any(self.queues, timeout)
                if result is not None:
                    job, queue = result
                    logger.info('Receive a job: {0} ({1})'.format(queue.name, job.description, job.id))
                break
            except DequeueTimeout:
                pass
        return result

    def heartbeat(self, timeout=0):
        """ 心跳，指定一个新的worker超时时间 """
        timeout = max(timeout, self.default_worker_ttl)
        self.connection.expire(self.key, timeout)
        self.connection.hset(self.key, 'last_heartbeat', datetime.now().strftime(TaskManager.settings.DATE_FMT))
        logger.debug('Sent heartbeat to prevent worker timeout. '
                     'Next one should arrive within {0} seconds.'.format(timeout))

    def refresh(self):
        data = self.connection.hmget(
            self.key, 'queues', 'state', 'current_job', 'last_heartbeat',
            'birth', 'failed_job_count', 'successful_job_count', 'total_working_time'
        )
        queues, state, job_id, last_heartbeat, birth, failed_job_count, successful_job_count, total_working_time = data
        queues = as_text(queues)
        self.state = as_text(state or '?')
        self._job_id = job_id or None
        self.last_heartbeat = datetime.strptime(as_text(last_heartbeat), TaskManager.settings.DATE_FMT)
        if failed_job_count:
            self.failed_job_count = int(as_text(failed_job_count))
        if successful_job_count:
            self.successful_job_count = int(as_text(successful_job_count))
        if total_working_time:
            self.total_working_time = float(as_text(total_working_time))

        if queues:
            self.queues = [self.queue_class(queue,
                                            connection=self.connection,
                                            job_class=self.job_class)
                           for queue in queues.split(',')]

    def fork_work(self, job, queue):
        """ fork出一个子进程执行Job """
        child_pid = os.fork()
        os.environ['RQ_WORKER_ID'] = self.name
        os.environ['RQ_JOB_ID'] = job.id
        if child_pid == 0:
            self.main_work_fork(job, queue)
        else:
            self._fork_pid = child_pid

    def monitor_work_fork(self, job):
        """ 循环监控fork """
        while True:
            try:
                self._monitor_work_fork_tick(job)
                break
            except OSError as e:
                if e.errno != errno.EINTR:
                    raise

    def _monitor_work_fork_tick(self, job):
        """ 监控fork """
        _, ret_val = os.waitpid(self._fork_pid, 0)  # 暂时停止目前进程的执行，直到有信号来到或子进程结束
        if ret_val == os.EX_OK:  # 正常处理退出
            return

        if job.status not in [JobStatus.FINISHED, JobStatus.FAILED, JobStatus.TIMEOUT]:
            if not job.ended_at:
                job.ended_at = datetime.now()
            job.status = JobStatus.FAILED
            job.save()
            job.failure()
            log = 'The job<{0}> failed.(work-horse terminated unexpectedly; waitpid returned {1})'.format(job.id, ret_val)
            logger.warning(log)

    def main_work_fork(self, job, queue):
        """ fork """
        self.setup_work_fork_signals()
        self._is_fork = True
        success = self.perform_job(job, queue)
        os._exit(int(not success))

    def setup_work_fork_signals(self):
        """ 改变信号处理的方式 """
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)

    def prepare_job_execution(self, job):
        self.state = WorkerStatus.BUSY
        self.set_current_job_id(job.id)
        job.status = JobStatus.STARTED
        job.save()

    def handle_job_success(self, job, queue):
        self.set_current_job_id(None)

        result_ttl = job.get_result_ttl(self.default_result_ttl)
        if result_ttl != 0:
            job.status = JobStatus.FINISHED
            job.save()

    def perform_job(self, job, queue):
        """ 执行Job """
        self.prepare_job_execution(job)
        try:
            job.started_at = datetime.now()
            with UnixSignalJobTimout(job.timeout):
                rv = job.perform()
            job.ended_at = datetime.now()
            job._result = rv
            self.handle_job_success(job=job, queue=queue)
            if job.next_job:  # 运行关联Job
                queue.enqueue_job(job.next_job)
            return True
        except JobTimeoutException:
            self.kill_fork()
            job.ended_at = datetime.now()
            job.status = JobStatus.TIMEOUT
            job.save()
            job.failure()
            return False
        except Exception:
            exc_info = sys.exc_info()
            exc_string = ''.join(
                traceback.format_exception_only(*exc_info[:2]) + traceback.format_exception(*exc_info)
            )
            logger.error(exc_string, exc_info=True, extra={
                'func': job.func_name,
                'arguments': job.args,
                'kwargs': job.kwargs,
                'queue': job.origin,
            })

            job.ended_at = datetime.now()
            job.status = JobStatus.FAILED
            job.exc_info = exc_string
            job.save()
            job.failure()

            self.handle_exception(job, *sys.exc_info())
            return False

    def handle_exception(self, job, *exc_info):
        """ 遍历异常处理栈,如果处理函数返回False，就不进行进一步处理了 """
        for handler in reversed(self._exc_handlers):
            logger.debug('Invoking exception handler {0}'.format(handler))
            fallthrough = handler(job, *exc_info)
            if fallthrough is False:
                break

    def push_exc_handler(self, handler_func):
        self._exc_handlers.append(handler_func)

    def pop_exc_handler(self):
        return self._exc_handlers.pop()

    def __eq__(self, other):
        return self.name == other.name

    def __hash__(self):
        return hash(self.name)
