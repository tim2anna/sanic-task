#!/usr/bin/python
# -*- coding: utf-8 -*-

from os import path
import hashlib
from functools import wraps
from aiofiles import open as open_async
from sanic import Blueprint, response
from __init__ import SANIC_TASK_USERNAME, SANIC_TASK_PASSWORD

# 声明蓝图
blueprint = Blueprint('sanic_task', url_prefix='sanic_task')

# 配置静态文件目录
blueprint.static('/static/', './static')


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


@blueprint.route('/')
@login_required
async def index(request):
    """ 首页 """
    return await render_template('index.html')
