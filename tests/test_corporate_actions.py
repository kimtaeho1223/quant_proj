import unittest
from decimal import Decimal

import pandas as pd

from quantdesk.corporate_actions import (
    CorporateActionError,
    PriceBasisEvidence,
    ResearchActionContext,
    SplitEvent,
    convert_split_position,
    require_raw_basis,
    require_event_date_raw_bar,
    split_adjusted_closes,
    validate_split_event,
)


def raw_evidence(**changes):
    fields = dict(
        code='000001', basis='raw', provider='test-provider',
        retrieved_at='2026-10-06T12:00:00+09:00', source_version='v1',
        fingerprint='sha256:example',
    )
    fields.update(changes)
    return PriceBasisEvidence(**fields)


def split_event(**changes):
    fields = dict(
        code='000001', effective_date='2026-09-25',
        published_at='2026-09-24T16:00:00+09:00', action_type='split',
        ratio=Decimal('2'), status='validated',
        source_fingerprint='sha256:action',
    )
    fields.update(changes)
    return SplitEvent(**fields)


class EvidenceContractTests(unittest.TestCase):
    def test_complete_raw_evidence_is_accepted(self):
        self.assertIsNone(require_raw_basis(raw_evidence(), '000001'))

    def test_non_raw_basis_and_wrong_code_are_rejected(self):
        for basis in ('adjusted', 'unknown', ''):
            with self.subTest(basis=basis), self.assertRaises(CorporateActionError):
                require_raw_basis(raw_evidence(basis=basis), '000001')
        with self.assertRaises(CorporateActionError):
            require_raw_basis(raw_evidence(), '000002')

    def test_missing_source_fields_and_unaware_timestamp_are_rejected(self):
        for change in ({'provider': ''}, {'source_version': ''},
                       {'fingerprint': ''}, {'retrieved_at': '2026-10-06T12:00:00'}):
            with self.subTest(change=change), self.assertRaises(CorporateActionError):
                require_raw_basis(raw_evidence(**change), '000001')

    def test_valid_split_event_is_accepted(self):
        self.assertIsNone(validate_split_event(split_event()))

    def test_invalid_split_events_are_rejected(self):
        for change in (
            {'ratio': Decimal('0')}, {'ratio': Decimal('NaN')},
            {'ratio': Decimal('Infinity')}, {'ratio': Decimal('-1')},
            {'published_at': '2026-09-24T16:00:00'},
            {'action_type': 'dividend'}, {'status': 'pending'},
            {'source_fingerprint': ''}, {'effective_date': '2026-09-31'},
        ):
            with self.subTest(change=change), self.assertRaises(CorporateActionError):
                validate_split_event(split_event(**change))


class SignalViewTests(unittest.TestCase):
    def setUp(self):
        self.prices = pd.DataFrame({
            'Date': ['2026-09-24', '2026-09-25'], 'Close': [100.0, 50.0],
        })

    def test_published_split_adjusts_only_pre_event_closes(self):
        original = self.prices.copy(deep=True)
        view = split_adjusted_closes(
            self.prices, raw_evidence(), [split_event()],
            '2026-09-25T15:30:00+09:00',
        )
        self.assertEqual(view.Close.tolist(), [50.0, 50.0])
        pd.testing.assert_frame_equal(self.prices, original)

    def test_effective_but_not_published_split_blocks(self):
        event = split_event(published_at='2026-09-25T18:00:00+09:00')
        with self.assertRaises(CorporateActionError):
            split_adjusted_closes(self.prices, raw_evidence(), [event],
                                   '2026-09-25T15:30:00+09:00')

    def test_future_event_does_not_adjust_history(self):
        event = split_event(effective_date='2026-09-28')
        view = split_adjusted_closes(self.prices, raw_evidence(), [event],
                                     '2026-09-25T15:30:00+09:00')
        self.assertEqual(view.Close.tolist(), [100.0, 50.0])

    def test_two_successive_splits_compound(self):
        prices = pd.DataFrame({
            'Date': ['2026-09-22', '2026-09-24', '2026-09-25'],
            'Close': [200.0, 100.0, 50.0],
        })
        first = split_event(effective_date='2026-09-24',
                            published_at='2026-09-23T16:00:00+09:00')
        view = split_adjusted_closes(prices, raw_evidence(), [split_event(), first],
                                     '2026-09-25T15:30:00+09:00')
        self.assertEqual(view.Close.tolist(), [50.0, 50.0, 50.0])

    def test_ambiguous_or_unverified_input_blocks(self):
        cases = [
            (raw_evidence(basis='unknown'), [split_event()]),
            (raw_evidence(), [split_event(code='000002')]),
            (raw_evidence(), [split_event(), split_event()]),
        ]
        for evidence, events in cases:
            with self.subTest(events=events), self.assertRaises(CorporateActionError):
                split_adjusted_closes(self.prices, evidence, events,
                                       '2026-09-25T15:30:00+09:00')


class PositionConversionTests(unittest.TestCase):
    def test_whole_share_split_and_reverse_split(self):
        self.assertEqual(convert_split_position(10, split_event()), 20)
        reverse = split_event(action_type='reverse_split', ratio=Decimal('0.5'))
        self.assertEqual(convert_split_position(6, reverse), 3)

    def test_fractional_entitlement_blocks_whole_shares(self):
        reverse = split_event(action_type='reverse_split', ratio=Decimal('0.5'))
        with self.assertRaises(CorporateActionError):
            convert_split_position(5, reverse)
        self.assertEqual(convert_split_position(5, reverse, fractional=True),
                         Decimal('2.5'))

    def test_invalid_holdings_and_unverified_event_block(self):
        for quantity in (0, -1, True, Decimal('1.5')):
            with self.subTest(quantity=quantity), self.assertRaises(CorporateActionError):
                convert_split_position(quantity, split_event())
        with self.assertRaises(CorporateActionError):
            convert_split_position(10, split_event(status='pending'))

    def test_event_day_raw_bar_is_required(self):
        prices = pd.DataFrame({
            'Date': ['2026-09-24', '2026-09-25'],
            'Open': [100.0, 50.0], 'Close': [100.0, 52.0],
        })
        self.assertEqual(require_event_date_raw_bar(prices, raw_evidence(), split_event()),
                         (50.0, 52.0))
        with self.assertRaises(CorporateActionError):
            require_event_date_raw_bar(prices, raw_evidence(basis='adjusted'), split_event())
        with self.assertRaises(CorporateActionError):
            require_event_date_raw_bar(prices.iloc[:1], raw_evidence(), split_event())
        for column in ('Open', 'Close'):
            bad = prices.copy()
            bad.loc[1, column] = 0
            with self.subTest(column=column), self.assertRaises(CorporateActionError):
                require_event_date_raw_bar(bad, raw_evidence(), split_event())


class ResearchContextTests(unittest.TestCase):
    def test_fingerprint_is_independent_of_event_order(self):
        first = split_event()
        second = split_event(code='000002', effective_date='2026-09-28',
                             source_fingerprint='sha256:second')
        left = ResearchActionContext(
            evidence=(raw_evidence(), raw_evidence(code='000002')),
            events=(first, second),
        )
        right = ResearchActionContext(
            evidence=(raw_evidence(code='000002'), raw_evidence()),
            events=(second, first),
        )
        self.assertEqual(left.fingerprint(), right.fingerprint())
        self.assertEqual(left.evidence_for('000001'), raw_evidence())
        self.assertEqual(left.events_for('000001'), (first,))

    def test_duplicate_event_and_missing_basis_are_rejected(self):
        with self.assertRaises(CorporateActionError):
            ResearchActionContext(evidence=(raw_evidence(),),
                                  events=(split_event(), split_event()))
        context = ResearchActionContext(evidence=(), events=(split_event(),))
        with self.assertRaises(CorporateActionError):
            context.evidence_for('000001')


if __name__ == '__main__':
    unittest.main()
