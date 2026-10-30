def process(queue, handler):
    job = queue.get()
    if job is None:
        return 0
    handler(job)
    queue.task_done()
    return 1
