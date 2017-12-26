#!/usr/bin/python
# -*- coding: utf-8 -*-

import os
import signal
import threading
import redis
import time
from enum import Enum
from datetime import datetime

from . import TaskManager
from .logutils import logger
from .exceptions import StopBeat


class BeatStatus(Enum):
    BUSY = 'busy'
    IDLE = 'idle'  # 空闲
    DEATH = 'death'


def heart():
    beat = Beat()
    beat.fresh()
    print(beat.status)
    if beat.status != BeatStatus.DEATH.value:
        beat.status = BeatStatus.BUSY.value
        beat.heart()
        beat.status = BeatStatus.IDLE.value

        global timer
        timer = threading.Timer(1, heart)
        timer.start()


timer = threading.Timer(1, heart)


class Beat(object):
    """ Beat """

    __instance = None
    connection = redis.Redis(connection_pool=TaskManager.settings.REDIS_POOL)

    def __init__(self):
        self.key = 'rq:beat'
        self._status = BeatStatus.IDLE.value
        self._last_heartbeat = datetime.now()
        self._pid = None
        self.shutdown_time = None

    def __new__(cls, *args, **kwargs):
        if Beat.__instance is None:
            obj = object.__new__(cls, *args, **kwargs)
            Beat.__instance = obj
        return Beat.__instance

    def register_birth(self):
        status = self.connection.hget(self.key, 'status')
        if not status or status == BeatStatus.DEATH.value:
            self.connection.hmset(self.key, {
                'status': self.status,
                'last_heartbeat': self.last_heartbeat,
            })

    @property
    def pid(self):
        """ 当前进程ID """
        return self._pid

    @pid.setter
    def pid(self, pid):
        self._pid = pid
        self.connection.hset(self.key, 'pid', pid)

    @property
    def status(self):
        return self._status

    @status.setter
    def status(self, status):
        self._status = status
        self.connection.hset(self.key, 'status', status)

    @property
    def last_heartbeat(self):
        return self._last_heartbeat

    @last_heartbeat.setter
    def last_heartbeat(self, last_heartbeat):
        self._last_heartbeat = last_heartbeat
        self.connection.hset(self.key, 'last_heartbeat', last_heartbeat)

    def fresh(self):
        data = self.connection.hmget(self.key, 'status')
        if data:
            status, = data
            self._status = status

    def _install_signal_handlers(self):
        signal.signal(signal.SIGINT, self.beat_stop)
        signal.signal(signal.SIGTERM, self.beat_stop)

    def beat_stop(self, signum, frame):
        """ 关闭beat """

        logger.debug('Got signal {0}'.format(signal.Signals(signum).name))

        signal.signal(signal.SIGINT, self.force_stop)
        signal.signal(signal.SIGTERM, self.force_stop)

        logger.warning('Warm shut down beat')

        self.shutdown_time = datetime.now()
        self.pid = None
        if self.status == BeatStatus.BUSY.value:  # 当前beat正在处理
            self.status = BeatStatus.DEATH.value
            logger.debug('Stopping after current beat is finished. Press Ctrl+C again for a cold shutdown.')
            while True:

                status = self.connection.hget(self.key, 'status')
                if status and str(status, 'utf-8') == BeatStatus
                os._exit(1)

        else:
            self.status = BeatStatus.DEATH.value
            # raise SystemExit()

    def force_stop(self, signum, frame):
        """ 强制关闭 """
        logger.warning('Cold shut down beat')
        raise SystemExit()

    def run(self, restart=False):
        """ 运行 """
        status = self.connection.hget(self.key, 'status')
        status = str(status, encoding='utf-8') if status else status
        if status and status != BeatStatus.DEATH.value:
            if restart:
                print('Kill the beat.')
            else:
                logger.warning('The beat is running now. Not run over one beat.')
                return

        self.pid = os.getpid()
        self.register_birth()
        self._install_signal_handlers()
        timer.start()

    def heart(self):
        """ 每次心跳 """
        print('hello')