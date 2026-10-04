import gzip
import tempfile
import unittest
from pathlib import Path

from quantdesk.management_history import (
    archive_management_history,
    compare_management_dates,
    load_management_history,
    management_state_on,
    parse_management_history,
)


def history_bytes(rows=None):
    rows = rows or [
        '1,2026/08/11,032960,동일기연,KOSDAQ,-,-',
        '2,2026/08/10,032960,동일기연,KOSDAQ,해제,주식분산기준미달 사유 해소',
        '3,2026/08/07,032960,동일기연,KOSDAQ,-,-',
        '4,2026/04/17,032960,동일기연,KOSDAQ,-,-',
        '5,2026/04/16,032960,동일기연,KOSDAQ,지정,주식분산기준미달',
        '6,2026/04/15,032960,동일기연,KOSDAQ,-,-',
    ]
    return ('번호,일자,종목코드,종목명,시장구분,지정구분,사유\n'
            + '\n'.join(rows) + '\n').encode('cp949')


class ManagementHistoryTests(unittest.TestCase):
    def test_dash_means_no_event_not_not_designated(self):
        history = parse_management_history(history_bytes())

        self.assertEqual(management_state_on(history, '2026-04-15'), 'unknown')
        self.assertEqual(management_state_on(history, '2026-04-16'), 'designated')
        self.assertEqual(management_state_on(history, '2026-04-17'), 'designated')
        self.assertEqual(management_state_on(history, '2026-08-07'), 'designated')
        self.assertEqual(management_state_on(history, '2026-08-10'), 'released')
        self.assertEqual(management_state_on(history, '2026-08-11'), 'released')
        self.assertEqual(management_state_on(history, '2026-08-12'), 'unknown')

    def test_official_dates_match_events_without_certifying_publication_time(self):
        history = parse_management_history(history_bytes())

        self.assertEqual(
            compare_management_dates(history, '2026-04-16', '2026-08-10'),
            {'matched': True, 'publication_time_verified': False},
        )
        self.assertFalse(compare_management_dates(
            history, '2026-04-15', '2026-08-10',
        )['matched'])

    def test_rejects_conflicting_or_ambiguous_event_history(self):
        rows = history_bytes().decode('cp949').splitlines()[1:]
        with self.assertRaises(ValueError):
            parse_management_history(history_bytes(rows + [
                '7,2026/04/16,032960,동일기연,KOSDAQ,해제,충돌',
            ]))
        with self.assertRaises(ValueError):
            parse_management_history(history_bytes(rows + [
                '7,2026/04/18,005930,삼성전자,KOSPI,지정,다른 종목',
            ]))

    def test_rejects_missing_cells_instead_of_crashing(self):
        with self.assertRaises(ValueError):
            parse_management_history(history_bytes([
                '1,2026/04/16,032960,동일기연,KOSDAQ,지정',
            ]))

    def test_archives_exact_original_bytes_idempotently(self):
        payload = history_bytes()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first = archive_management_history(root, payload)
            second = archive_management_history(root, payload)

            self.assertEqual(first.sha256, second.sha256)
            self.assertEqual(first.path, second.path)
            self.assertTrue(first.path.resolve().is_relative_to(root.resolve()))
            with gzip.open(first.path, 'rb') as stored:
                self.assertEqual(stored.read(), payload)
            self.assertEqual(len(list(root.rglob('*.csv.gz'))), 1)

    def test_rejects_tampered_archived_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            artifact = archive_management_history(Path(folder), history_bytes())
            with gzip.open(artifact.path, 'wb') as stored:
                stored.write(history_bytes().replace(b'032960', b'005930'))

            with self.assertRaises(ValueError):
                load_management_history(artifact.path)


if __name__ == '__main__':
    unittest.main()
