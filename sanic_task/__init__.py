#!/usr/bin/python
# -*- coding: utf-8 -*-

""" worker是执行任务的Python进程。

worker会从Redis队列中无限循环读取Job，如果所有的Job都处理完了，会处于等待状态，直到有新的Job。

每个worker在同一时刻只能处理一个Job，如果需要提高Job的并发处理能力，你可以开启更多的worker进行处理。

Burst模式：默认worker开始运行后，处理完所有的Job后将会是阻塞等待状态。如果是burst模式，当前没有新的Job需要处理，worker将会被销毁。
它适用于过多的Job堆积需要临时提高Job的处理能力的场景。

worker的生命周期：
    1.加载Python环境。
    2.注册自己到系统。
    3.开始监听。一个Job是从Redis队列中取出的，如果是burst模式，队列为空会自动退出；如果不是会等待直到有新的Job。
    4.准备Job执行。worker的状态会设置为busy，并在StartedJobRegistry中注册Job。
    5.fork一个子进程。子进程在fail-safe的上下文中执行真正的任务。
    6.处理任务。
    7.清理Job执行。worker和Job的状态被设置为idle，Job会从StartedJobRegistry移除，成功执行后加入到FinishedJobRegistry中，
    如果失败加入到FailedQueue。根据result_ttl设置结果的过期时间。
    8.从第3步开始下一个循环。

到底是fork前加载Python模块，还是fork后？
    1.fork前加载，能提高性能。
    2.fork后加载，不会导致内存泄漏。

worker命名：
    默认采用主机名+当前PID，你可以通过--name指定名称。

停止worker
    在任何时候，worker接收到SIGINT(via Ctrl+C)或者SIGTERM((via kill)后都会等待当前任务结束后再停止。
    如果在这个等待结束的期间，再一次接收到SIGINT或者SIGTERM，worker将会强制终止子进程。

"""

import redis


class Settings(object):

    def __init__(self, config=None):
        self.JOB_CLASS = getattr(config, 'JOB_CLASS', 'sanic_task.job.RedisJob')

        self.DEFAULT_QUEUE_CLASS = getattr(config, 'DEFAULT_QUEUE_CLASS', 'rq.Queue')
        self.DEFAULT_WORKER_CLASS = getattr(config, 'DEFAULT_WORKER_CLASS', 'rq.Worker')
        self.DEFAULT_CONNECTION_CLASS = getattr(config, 'DEFAULT_CONNECTION_CLASS', 'redis.StrictRedis')
        self.DEFAULT_WORKER_TTL = getattr(config, 'DEFAULT_WORKER_TTL', 10)
        self.DEFAULT_RESULT_TTL = getattr(config, 'DEFAULT_RESULT_TTL', 24*3600)
        self.DEFAULT_RETRY = getattr(config, 'DEFAULT_RETRY', 3)  # 失败重试次数
        self.DEFAULT_RETRY_DELAY = getattr(config, 'DEFAULT_JOB_RETRY', 300)  # 失败重试时间间隔

        self.DATE_FMT = '%Y-%m-%d %H:%M:%S.%f'

        self.REDIS_HOST = getattr(config, '', 'localhost')
        self.REDIS_PORT = getattr(config, '', '6379')
        self.REDIS_DB = getattr(config, '', 0)
        self.REDIS_PASSWORD = getattr(config, '', '')
        self.REDIS_POOL = redis.ConnectionPool(
            host=self.REDIS_HOST,
            port=self.REDIS_PORT,
            password=self.REDIS_PASSWORD,
            db=self.REDIS_DB,
        )


class TaskManager(object):

    settings = Settings()
    app = None

    @classmethod
    def init_app(cls, app):
        cls.app = app
        cls.settings = Settings(app.config)
