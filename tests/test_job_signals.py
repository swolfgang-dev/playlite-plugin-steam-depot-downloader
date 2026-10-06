import unittest
import test_worker
from PyQt6.QtCore import QThread, QThreadPool
from downloader.plugin import Job


class JobSignalTests(unittest.TestCase):
    def test_background_completion_is_delivered_on_the_gui_thread(self):
        app = test_worker.APP
        worker_threads = []
        completions = []
        def operation():
            worker_threads.append(QThread.currentThread())
            return 'Done'
        job = Job(operation)
        job.signals.finished.connect(lambda result: completions.append((result, QThread.currentThread())))
        pool = QThreadPool()
        pool.start(job)
        self.assertTrue(pool.waitForDone(2000))
        self.assertEqual(completions, [])
        app.processEvents()
        self.assertEqual(completions, [('Done', app.thread())])
        self.assertNotEqual(worker_threads[0], app.thread())
