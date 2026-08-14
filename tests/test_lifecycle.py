import tempfile
import unittest
from pathlib import Path

from core.lifecycle import PlotFileLifecycle, PlotLoadController, TaskLifecycle


class FakeTask:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class PlotFileLifecycleTest(unittest.TestCase):
    def test_successful_commit_removes_prior_only_after_pending_load(self):
        # Given: a committed plot and a serialized replacement pending WebEngine load.
        with tempfile.TemporaryDirectory() as temporary_directory:
            lifecycle = PlotFileLifecycle(temporary_directory)
            first = lifecycle.prepare(lambda path: path.write_text("first", encoding="utf-8"))
            lifecycle.commit(first)
            second = lifecycle.prepare(lambda path: path.write_text("second", encoding="utf-8"))

            self.assertEqual(lifecycle.active_path, first)
            self.assertEqual(lifecycle.pending_path, second)
            self.assertTrue(first.exists())

            # When: WebEngine confirms the replacement loaded.
            lifecycle.commit(second)

            # Then: browser-open resolves to the replacement and the superseded file is gone.
            self.assertEqual(lifecycle.browser_path, second)
            self.assertIsNone(lifecycle.pending_path)
            self.assertEqual(second.read_text(encoding="utf-8"), "second")
            self.assertFalse(first.exists())
            self.assertEqual(list(Path(temporary_directory).glob("*.html")), [second])

    def test_failed_load_rolls_back_pending_and_preserves_browser_path(self):
        # Given: an active plot and a pending replacement.
        with tempfile.TemporaryDirectory() as temporary_directory:
            lifecycle = PlotFileLifecycle(temporary_directory)
            active = lifecycle.prepare(lambda path: path.write_text("active", encoding="utf-8"))
            lifecycle.commit(active)
            pending = lifecycle.prepare(lambda path: path.write_text("pending", encoding="utf-8"))

            # When: WebEngine rejects the pending document.
            restored = lifecycle.rollback(pending)

            # Then: the failed file is discarded and the prior browser path is returned for reload.
            self.assertEqual(restored, active)
            self.assertEqual(lifecycle.browser_path, active)
            self.assertIsNone(lifecycle.pending_path)
            self.assertFalse(pending.exists())
            self.assertEqual(list(Path(temporary_directory).glob("*.html")), [active])

    def test_serialization_failure_preserves_active_file(self):
        # Given: an active self-contained plot.
        with tempfile.TemporaryDirectory() as temporary_directory:
            lifecycle = PlotFileLifecycle(temporary_directory)
            active = lifecycle.prepare(lambda path: path.write_text("active", encoding="utf-8"))
            lifecycle.commit(active)

            def fail_after_partial_write(path):
                path.write_text("partial", encoding="utf-8")
                raise RuntimeError("serialization failed")

            # When: writing its replacement fails.
            with self.assertRaisesRegex(RuntimeError, "serialization failed"):
                lifecycle.prepare(fail_after_partial_write)

            # Then: the browser-open path stays valid and the partial file is removed.
            self.assertEqual(lifecycle.browser_path, active)
            self.assertIsNone(lifecycle.pending_path)
            self.assertEqual(active.read_text(encoding="utf-8"), "active")
            self.assertEqual(list(Path(temporary_directory).glob("*.html")), [active])

    def test_clear_removes_active_and_pending_files(self):
        # Given: active and pending plot documents.
        with tempfile.TemporaryDirectory() as temporary_directory:
            lifecycle = PlotFileLifecycle(temporary_directory)
            active = lifecycle.prepare(lambda path: path.write_text("active", encoding="utf-8"))
            lifecycle.commit(active)
            pending = lifecycle.prepare(lambda path: path.write_text("pending", encoding="utf-8"))

            # When: the lifecycle is cleared during dock disposal.
            lifecycle.clear()

            # Then: no browser or pending path survives.
            self.assertIsNone(lifecycle.browser_path)
            self.assertIsNone(lifecycle.pending_path)
            self.assertFalse(active.exists())
            self.assertFalse(pending.exists())
            self.assertEqual(list(Path(temporary_directory).glob("*.html")), [])

    def test_browser_path_remains_active_while_replacement_is_pending(self):
        # Given: an active plot.
        with tempfile.TemporaryDirectory() as temporary_directory:
            lifecycle = PlotFileLifecycle(temporary_directory)
            active = lifecycle.prepare(lambda path: path.write_text("active", encoding="utf-8"))
            lifecycle.commit(active)

            # When: a replacement is prepared but not loaded.
            pending = lifecycle.prepare(lambda path: path.write_text("pending", encoding="utf-8"))

            # Then: external browser opening still targets the last confirmed document.
            self.assertEqual(lifecycle.browser_path, active)
            self.assertEqual(lifecycle.pending_path, pending)
            self.assertEqual(len(list(Path(temporary_directory).glob("*.html"))), 2)


class TaskLifecycleTest(unittest.TestCase):
    def test_dispose_cancels_active_task_and_rejects_late_completion(self):
        # Given: an active background task.
        lifecycle = TaskLifecycle()
        task = FakeTask()
        lifecycle.start(task)

        # When: the owning dock is disposed before completion.
        lifecycle.dispose()

        # Then: cancellation is requested and its late callback cannot touch the dock.
        self.assertTrue(task.cancelled)
        self.assertFalse(lifecycle.finish(task))
        self.assertIsNone(lifecycle.active_task)

    def test_superseded_task_completion_is_rejected(self):
        # Given: a newer task has replaced a previous registration.
        lifecycle = TaskLifecycle()
        previous = FakeTask()
        active = FakeTask()
        lifecycle.start(previous)
        lifecycle.start(active)

        # When/Then: only the current task can complete the lifecycle.
        self.assertFalse(lifecycle.finish(previous))
        self.assertTrue(lifecycle.finish(active))
        self.assertIsNone(lifecycle.active_task)

    def test_start_cancels_previous_active_task(self):
        # Given: a task is already active.
        lifecycle = TaskLifecycle()
        previous = FakeTask()
        replacement = FakeTask()
        lifecycle.start(previous)

        # When: a replacement starts.
        lifecycle.start(replacement)

        # Then: the superseded task is cooperatively cancelled.
        self.assertTrue(previous.cancelled)
        self.assertIs(lifecycle.active_task, replacement)


class PlotLoadControllerTest(unittest.TestCase):
    def test_only_matching_generation_and_url_can_commit(self):
        # Given: a newer plot superseded an earlier pending load.
        controller = PlotLoadController()
        first = controller.begin(Path("/tmp/first.html"))
        second = controller.begin(Path("/tmp/second.html"))

        # When/Then: stale and URL-mismatched events are ignored.
        self.assertIsNone(controller.resolve(first.generation, first.path, succeeded=True))
        self.assertIsNone(controller.resolve(second.generation, first.path, succeeded=True))
        self.assertEqual(controller.resolve(second.generation, second.path, succeeded=True), True)

    def test_matching_failure_resolves_as_rollback(self):
        # Given: one current pending load.
        controller = PlotLoadController()
        pending = controller.begin(Path("/tmp/pending.html"))

        # When/Then: its exact failure resolves once and later duplicates are stale.
        self.assertEqual(controller.resolve(pending.generation, pending.path, succeeded=False), False)
        self.assertIsNone(controller.resolve(pending.generation, pending.path, succeeded=True))
