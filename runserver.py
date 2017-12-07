#!/usr/bin/python
# -*- coding: utf-8 -*-

from sanic import Sanic

from sanic_task.views import blueprint as bp_task

app = Sanic(__name__)
app.blueprint(bp_task, url_prefix='/api/sanic_task')

# 配置静态文件目录
app.static('', './sanic_task/static/')
app.static('', './sanic_task/static/index.html')


if __name__ == '__main__':
    app.run(host="0.0.0.0", port=8000, debug=True)
