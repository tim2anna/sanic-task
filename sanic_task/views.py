#!/usr/bin/python
# -*- coding: utf-8 -*-

from os import path
import hashlib
from functools import wraps
from aiofiles import open as open_async
from sanic import Blueprint, response
from sanic_task import TaskManager
from sanic_task.worker import Worker
from sanic_task.queue import Queue
from sanic_task.utils import import_attribute
from sanic_task.job import JobStatus
from __init__ import SANIC_TASK_USERNAME, SANIC_TASK_PASSWORD

# 声明蓝图
blueprint = Blueprint('sanic_task')


LOGIN_HASH = hashlib.md5((SANIC_TASK_USERNAME + SANIC_TASK_PASSWORD).encode('utf-8')).hexdigest()


async def render_template(name):
    """ 渲染模板 """
    template_file = path.join(path.dirname(__file__), 'templates', name)

    async with open_async(template_file, mode='r') as _file:
        out_stream = await _file.read()

    return response.html(out_stream)


def login_required(func):
    """ 登陆装饰器 """
    @wraps(func)
    def decorated_view(request, *args, **kwargs):
        login_flag = request.cookies.get('sanic_task_login')
        if login_flag == LOGIN_HASH:
            return func(request, *args, **kwargs)
        else:
            return response.redirect('/sanic_task/login/')
    return decorated_view


@blueprint.route('/login/', methods=['GET', 'POST'])
async def login(request):
    """ 登陆 """
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == SANIC_TASK_USERNAME and password == SANIC_TASK_PASSWORD:
            resp = response.redirect('/sanic_task/')
            resp.cookies['sanic_task_login'] = LOGIN_HASH
            return resp

    return await render_template('login.html')


@blueprint.route('/dashboard/worker_cards/', methods=['GET'])
async def worker_cards(request):
    results = []
    for worker in Worker.all():
        results.append({'id': worker.key, 'name': worker.name, 'status': worker.status})
    return response.json(results)


@blueprint.route('/dashboard/beat/', methods=['GET'])
async def beat(request):
    pass


@blueprint.route('/dashboard/worker_table/', methods=['GET'])
async def worker_table(request):
    results = []
    for worker in Worker.all():
        results.append({
            'id': worker.key,
            'name': worker.name,
            'status': worker.status,
            'pid': worker.pid,
            'death': worker.death,
            'success_job_count': worker.success_job_count,
            'failure_job_count': worker.failure_job_count,
        })
    return response.json(results)


@blueprint.route('/dashboard/recent_exception_jobs/', methods=['GET'])
async def recent_exception_jobs(request):
    job_class = import_attribute(TaskManager.settings.JOB_CLASS)
    results = []
    for job in job_class.all_jobs(JobStatus.FAILED):
        results.append({'id': job.id, 'desc': job.desc, 'start_time': job.start_time, 'exc_info': job.exc_info})
    return response.json(results)


@blueprint.route('/dashboard/job_numbers/', methods=['GET'])
async def job_numbers(request):
    job_class = import_attribute(TaskManager.settings.JOB_CLASS)
    results = {
        'running': job_class.count(JobStatus.STARTED),
        'enqueue': job_class.count(JobStatus.QUEUED),
        'failure': job_class.count(JobStatus.FAILED),
        'success': job_class.count(JobStatus.FINISHED),
    }
    return response.json(results)


@blueprint.route('/workers/', methods=['GET'])
async def workers(request):
    results = []
    for worker in Worker.all():
        results.append({
            'id': worker.key,
            'name': worker.name,
            'status': worker.status,
            'pid': worker.pid,
            'death': worker.death,
            'success_job_count': worker.success_job_count,
            'failure_job_count': worker.failure_job_count,
        })
    return response.json({'total': len(results), 'results': results})


@blueprint.route('/queues/', methods=['GET'])
async def queues(request):
    results = []
    for queue in Queue.all():
        results.append({
            'id': queue.key,
            'name': queue.name,
            'count': queue.count,
        })
    return response.json({'total': len(results), 'results': results})


@blueprint.route('/jobs/', methods=['GET'])
async def jobs(request):
    job_class = import_attribute(TaskManager.settings.JOB_CLASS)
    results = []
    for job in job_class.all_jobs():
        results.append({
            'id': job.key,
            'desc': job.desc,
            'status': job.status,

        })
    return response.json({'total': len(results), 'results': results})