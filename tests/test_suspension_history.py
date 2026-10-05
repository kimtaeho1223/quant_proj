import gzip
import tempfile
import unittest
from pathlib import Path

from quantdesk.suspension_history import (
    archive_suspension_history,
    compare_suspension_timing,
    load_suspension_history,
    parse_suspension_history,
    suspension_state_on,
)


def suspension_bytes(rows=None):
    if rows is None:
        rows = [
            '1,032960,동일기연,KOSDAQ,2026/04/16,2026/04/20',
            '2,032960,동일기연,KOSDAQ,2026/08/10,2026/08/12',
        ]
    return ('번호,종목코드,종목명,시장구분,정지일,재개일\n'
            + '\n'.join(rows) + '\n').encode('cp949')


class SuspensionHistoryTests(unittest.TestCase):
    def test_closed_intervals_only_establish_status_within_observed_boundaries(self):
        history = parse_suspension_history(suspension_bytes())

        self.assertEqual(suspension_state_on(history, '2026-04-15'), 'unknown')
        self.assertEqual(suspension_state_on(history, '2026-04-16'), 'suspended')
        self.assertEqual(suspension_state_on(history, '2026-04-17'), 'suspended')
        self.assertEqual(suspension_state_on(history, '2026-04-20'), 'resumption_event')
        self.assertEqual(suspension_state_on(history, '2026-04-21'), 'unknown')
        self.assertEqual(suspension_state_on(history, '2026-08-10'), 'suspended')

    def test_open_or_same_day_interval_does_not_infer_future_tradability(self):
        open_history = parse_suspension_history(suspension_bytes([
            '1,032960,동일기연,KOSDAQ,2026/04/16,',
        ]))
        self.assertEqual(suspension_state_on(open_history, '2026-04-16'), 'suspended')
        self.assertEqual(suspension_state_on(open_history, '2026-04-17'), 'unknown')

        same_day = parse_suspension_history(suspension_bytes([
            '1,032960,동일기연,KOSDAQ,2026/04/16,2026/04/16',
        ]))
        self.assertEqual(suspension_state_on(same_day, '2026-04-16'), 'unknown')

    def test_compares_effective_dates_without_certifying_disclosure_time(self):
        history = parse_suspension_history(suspension_bytes())
        result = compare_suspension_timing(
            history, interval_index=0,
            halted_disclosed_on='2026-04-15', halted_disclosed_at='',
            halted_effective_on='2026-04-16',
            resumed_disclosed_on='2026-04-17', resumed_disclosed_at='',
            resumed_effective_on='2026-04-20',
        )

        self.assertTrue(result['effective_dates_match'])
        self.assertTrue(result['calendar_order_valid'])
        self.assertFalse(result['publication_time_verified'])
        self.assertFalse(result['publication_times_recorded'])

    def test_rejects_bad_order_time_and_mismatched_effective_date(self):
        history = parse_suspension_history(suspension_bytes())
        evidence = dict(
            interval_index=0, halted_disclosed_on='2026-04-15',
            halted_disclosed_at='19:30', halted_effective_on='2026-04-16',
            resumed_disclosed_on='2026-04-17', resumed_disclosed_at='16:20',
            resumed_effective_on='2026-04-20',
        )
        self.assertTrue(compare_suspension_timing(history, **evidence)['publication_times_recorded'])
        evidence['resumed_disclosed_on'] = '2026-04-21'
        self.assertFalse(compare_suspension_timing(history, **evidence)['calendar_order_valid'])
        evidence['resumed_disclosed_on'] = '2026-04-17'
        evidence['resumed_effective_on'] = '2026-04-21'
        self.assertFalse(compare_suspension_timing(history, **evidence)['effective_dates_match'])
        evidence['halted_disclosed_at'] = '25:00'
        with self.assertRaises(ValueError):
            compare_suspension_timing(history, **evidence)

    def test_rejects_duplicate_overlapping_or_mixed_security_rows(self):
        first = '1,032960,동일기연,KOSDAQ,2026/04/16,2026/04/20'
        for extra in (
            '2,032960,동일기연,KOSDAQ,2026/04/16,2026/04/21',
            '2,032960,동일기연,KOSDAQ,2026/04/19,2026/04/21',
            '2,005930,삼성전자,KOSPI,2026/04/22,2026/04/24',
        ):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                parse_suspension_history(suspension_bytes([first, extra]))

    def test_archives_exact_original_bytes_and_detects_tampering(self):
        payload = suspension_bytes()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = archive_suspension_history(root, payload)
            second = archive_suspension_history(root, payload)
            self.assertEqual(first.path, second.path)
            with gzip.open(first.path, 'rb') as source:
                self.assertEqual(source.read(), payload)
            self.assertEqual(len(load_suspension_history(first.path)), 2)
            with gzip.open(first.path, 'wb') as output:
                output.write(payload.replace(b'032960', b'005930'))
            with self.assertRaises(ValueError):
                load_suspension_history(first.path)


if __name__ == '__main__':
    unittest.main()
