"""Lifecycle authority stays independent of Qt and device-library health."""
import unittest

from soundbox.device_operations import (
    DeviceCommand, DeviceOperationCoordinator, DeviceOperationResult,
    DeviceSessionIdentity, Phase,
)


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.lane = DeviceOperationCoordinator()
        self.mount = DeviceSessionIdentity("/fixture/IPOD", 1, "library")
        self.lane.observe(self.mount)

    def test_result_does_not_release_worker(self):
        worker = object()
        command = DeviceCommand(self.mount, "sync", confirmation_scope="sync")
        self.lane.begin(command, worker, Phase.EXECUTING)
        self.lane.publish(worker, DeviceOperationResult(committed=True))
        self.assertTrue(self.lane.busy)
        with self.assertRaises(RuntimeError):
            self.lane.begin(command, object(), Phase.INSPECTING)
        self.assertTrue(self.lane.finished(worker))
        self.assertFalse(self.lane.busy)

    def test_remount_revokes_queued_authority_and_stale_callback(self):
        worker = object()
        command = DeviceCommand(self.mount, "export", confirmation_scope="drop")
        state = self.lane.begin(command, worker, Phase.PLANNING)
        self.lane.enqueue(command)
        self.lane.observe(DeviceSessionIdentity(self.mount.path, 2, "library"))
        self.assertTrue(state.cancellation.is_set())
        self.assertFalse(self.lane.queued)
        self.assertFalse(self.lane.finished(worker))
        with self.assertRaises(RuntimeError):
            self.lane.begin(command, object(), Phase.EXECUTING)

    def test_cancel_clears_queue_but_committed_result_remains_committed(self):
        command = DeviceCommand(self.mount, "sync")
        worker = object()
        state = self.lane.begin(command, worker, Phase.EXECUTING)
        self.lane.enqueue(command)
        self.lane.cancel()
        self.assertTrue(state.cancellation.is_set())
        self.assertFalse(self.lane.queued)
        self.lane.publish(worker, DeviceOperationResult(committed=True, refresh="failed"))
        self.lane.finished(worker)
        self.assertTrue(self.lane.last_result.committed)
        self.assertEqual(self.lane.last_result.refresh, "failed")

    def test_batch_never_absorbs_destructive_or_other_device_commands(self):
        other = DeviceSessionIdentity("/fixture/OTHER", 3)
        self.lane.observe(other)
        commands = [DeviceCommand(self.mount, "export", (str(i),), "drop") for i in range(2)]
        delete = DeviceCommand(self.mount, "delete", ("x",), "delete-x")
        foreign = DeviceCommand(other, "export", ("z",), "drop")
        for command in [*commands, delete, foreign]:
            self.lane.enqueue(command)
        self.assertEqual(self.lane.next_batch(), tuple(commands))
        self.assertEqual(self.lane.next_batch(), (delete,))
        self.assertEqual(self.lane.next_batch(), (foreign,))

    def test_eject_hold_suppresses_poll_without_integrity_input(self):
        self.assertTrue(self.lane.polling_allowed)
        self.lane.eject_pending = self.mount.path
        self.assertFalse(self.lane.polling_allowed)
        self.assertFalse(self.lane.busy)

    def test_reconnect_with_same_filesystem_token_has_new_authority(self):
        old = DeviceCommand(self.mount, "delete", confirmation_scope="selected")
        self.lane.disconnect(self.mount.path)
        self.lane.observe(DeviceSessionIdentity(self.mount.path, self.mount.mount_token, "library"))
        self.assertFalse(self.lane.current(old))
