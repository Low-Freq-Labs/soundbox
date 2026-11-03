"""Exact immutable proof reuse never substitutes for a changed input or failure."""
from pathlib import Path
import unittest
from unittest import mock

from soundbox import immutable_plan_proof as proof


class ImmutablePlanProof(unittest.TestCase):
    def setUp(self):
        proof._verified.clear()
        self.addCleanup(proof._verified.clear)
        self.clock = self.enterContext(mock.patch.object(proof.time, 'monotonic', return_value=100.0))
        self.verify = mock.Mock()

    def call(self, **change):
        args = dict(folder=Path('/explicit/captured/plan'), raw_plan=b'exact original record',
                    authority=b'exact original authority', profile='wav-v1', verify=self.verify)
        args.update(change)
        proof.verify_once(**args)

    def test_every_immutable_input_and_domain_participates_in_the_proof_key(self):
        self.call(); self.call(); self.assertEqual(self.verify.call_count, 1)
        for change in (dict(raw_plan=b'exact original record\n'), dict(authority=b'different authority'),
                       dict(profile='cbr-v1'), dict(folder=Path('/other/captured/plan'))):
            self.call(**change)
        self.assertEqual(self.verify.call_count, 5)

    def test_expiry_is_not_extended_by_hits_and_capacity_is_bounded(self):
        self.call()
        self.clock.return_value = 129.0
        self.call(); self.assertEqual(self.verify.call_count, 1)
        self.clock.return_value = 130.0
        self.call(); self.assertEqual(self.verify.call_count, 2)
        for index in range(proof.MAX_PROOFS):
            self.call(raw_plan=str(index).encode())
        self.assertEqual(len(proof._verified), proof.MAX_PROOFS)
        self.call(); self.assertEqual(self.verify.call_count, 3 + proof.MAX_PROOFS)

    def test_failed_reconstruction_never_produces_a_reusable_proof(self):
        self.verify.side_effect = ValueError('unverified paired inputs')
        for _ in range(2):
            with self.assertRaisesRegex(ValueError, 'unverified paired'):
                self.call()
        self.assertEqual(self.verify.call_count, 2)
        self.assertFalse(proof._verified)
        self.verify.side_effect = None
        self.call(); self.call(); self.assertEqual(self.verify.call_count, 3)

    def test_mutable_or_ambiguous_key_types_never_enter_the_memo(self):
        for change in (dict(raw_plan=bytearray(b'record')), dict(authority=bytearray(b'authority')),
                       dict(profile=''), dict(profile=True)):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.call(**change)
        self.verify.assert_not_called()
