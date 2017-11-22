# sanic-task

sanic-task is an extension for Sanic that adds support for asynchronous task to your application.

sanic-script是Sanic框架的一个扩展库，为你的Web应用提供异步任务支持。


## install

    $ pip install sanic-script


## quick start

1. New the `manage.py` file in your project.

        from runserver import app
        from sanic_script import Manager
        from sanic_task.command import manager as task_manager

        manager = Manager(app)
        manager.add_command('task', task_manager)


        import requests
        from sanic_task.decorators import Task


        @Task()
        def count_words_at_url(url):
            resp = requests.get(url)
            print(resp.text.split())
            return len(resp.text.split())


        @manager.command
        def add_job():
            s = count_words_at_url.s('http://nvie.com') | count_words_at_url.s('http://baidu.com')
            s.delay()


        if __name__ == "__main__":
            manager.run()

2. Start task worker:

        python manage.py task worker

3. Test add a simple job:

        python manage.py add_job
        
