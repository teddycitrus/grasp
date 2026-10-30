def process(queue, handler, batch_size=10):
    done = 0
    while done < batch_size:
        job = queue.get()
        if job is None:
            break
        try:
            handler(job)
        except Exception:
            queue.put(job)
            raise
        finally:
            queue.task_done()
        done += 1
    return done
