#!/usr/bin/python
# -*- coding: utf-8 -*-

from sanic import Sanic
from views import blueprint as bp_task

app = Sanic(__name__)
app.blueprint(bp_task)


if __name__ == '__main__':
    app.run(host="0.0.0.0", port=8000, debug=True)
