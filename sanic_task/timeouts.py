#!/usr/bin/python
# -*- coding: utf-8 -*-

import signal


class JobTimeoutException(Exception):
    """ 一个Job执行时间过长超过允许的最大超时时间，会抛出这个异常 """
    pass


class JobTimeoutBase(object):
    """ Job超时的基类 """

    def __init__(self, timeout):
        self._timeout = timeout

    def __enter__(self):
        self.setup_timeout()

    def __exit__(self, type, value, traceback):
        self.cancel_timeout()
        return False

    def setup_timeout(self):
        raise NotImplementedError()

    def cancel_timeout(self):
        raise NotImplementedError()


class UnixSignalJobTimout(JobTimeoutBase):
    """ Unix Signal的实现 """

    def handle_timeout(self, signum, frame):
        """ 处理超时 """
        raise JobTimeoutException('Job exceeded maximum timeout value ({0} seconds)'.format(self._timeout))

    def setup_timeout(self):
        """ 利用signal的alarm函数，一定时间间隔后产生SIGALRM信号，并对这个信号进行处理。"""
        signal.signal(signal.SIGALRM, self.handle_timeout)
        signal.alarm(self._timeout)

    def cancel_timeout(self):
        """ 移除alarm，并恢复默认的信号处理 """
        signal.alarm(0)
        signal.signal(signal.SIGALRM, signal.SIG_DFL)