import tempfile
import unittest
from pathlib import Path

from quantdesk.historical_market_store import HistoricalMarketStore
from quantdesk.security_lifecycle import build_lifecycle_model, reconcile_daily_market
from quantdesk.security_lifecycle_store import SecurityLifecycleStore
from tests.security_lifecycle_fixtures import normalized_lifecycle_rows


class SecurityLifecycleReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / 'market.sqlite3'
        HistoricalMarketStore(self.path)
        self.store = SecurityLifecycleStore(self.path)
        rows = normalized_lifecycle_rows().iloc[[0]].copy()
        self.model = build_lifecycle_model(rows)
        self.store.promote_model(self.model, {'security_master': 1})

    def tearDown(self):
        self.temporary.cleanup()

    def save_day(self, trade_date, rows):
        with self.store.connect() as db:
            cursor = db.execute(
                '''INSERT INTO historical_raw_versions
                (trade_date,source_type,sha256,path,fetched_at,parser_version,metadata_json)
                VALUES(?,?,?,?,?,?,?)''',
                (trade_date, 'fixture', trade_date.replace('-', '') * 5 + '0000',
                 f'{trade_date}.csv.gz', f'{trade_date}T09:00:00+00:00', '1', '{}'),
            )
            raw_id = cursor.lastrowid
            db.executemany(
                '''INSERT INTO historical_daily_market VALUES
                (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                [
                    (
                        trade_date, row['code'], row.get('name', '삼성전자'),
                        row.get('market', 'KOSPI'), '일반', row.get('open', row['close']),
                        row.get('high', row['close']), row.get('low', row['close']),
                        row['close'], row.get('volume', 100), row.get('amount', 1000),
                        row.get('marcap', 10000), row.get('listed_shares', 1000), raw_id,
                    )
                    for row in rows
                ],
            )
        return raw_id

    def test_market_row_without_active_lifecycle_is_blocking(self):
        raw_id = self.save_day('2024-06-03', [
            {'code': '999999', 'name': '미등록종목', 'close': 1000},
        ])

        findings = reconcile_daily_market(self.store, '2024-06-03', '2024-06-03')

        finding = next(item for item in findings if item.rule_id == 'market_without_lifecycle')
        self.assertEqual(finding.severity, 'blocking')
        self.assertEqual(finding.market_raw_version_id, raw_id)

    def test_active_security_missing_from_market_is_unresolved_not_delisted(self):
        self.save_day('2024-06-03', [])

        findings = reconcile_daily_market(self.store, '2024-06-03', '2024-06-03')

        self.assertTrue(any(item.rule_id == 'active_security_missing' for item in findings))
        self.assertEqual(len(self.store.listed_securities('2024-06-03')), 1)
        timeline = self.store.lifecycle_timeline(self.model.securities.iloc[0].security_id)
        self.assertFalse(any(item['event_type'] == 'delisting' for item in timeline['events']))
        readiness = self.store.readiness('2024-06-03', '2024-06-03')
        self.assertEqual(readiness['unresolved_dates'], ['2024-06-03'])

    def test_name_and_market_conflicts_preserve_both_source_values(self):
        self.save_day('2024-06-03', [
            {'code': '005930', 'name': '삼성전자 변경', 'market': 'KOSDAQ', 'close': 70000},
        ])

        findings = reconcile_daily_market(self.store, '2024-06-03', '2024-06-03')

        name = next(item for item in findings if item.rule_id == 'name_conflict')
        market = next(item for item in findings if item.rule_id == 'market_conflict')
        self.assertEqual((name.lifecycle_value, name.market_value), ('삼성전자', '삼성전자 변경'))
        self.assertEqual((market.lifecycle_value, market.market_value), ('KOSPI', 'KOSDAQ'))

    def test_suspended_zero_volume_row_does_not_end_listing(self):
        self.save_day('2024-06-03', [
            {'code': '005930', 'close': 70000, 'volume': 0, 'amount': 0},
        ])

        findings = reconcile_daily_market(self.store, '2024-06-03', '2024-06-03')

        self.assertFalse(any('delist' in item.rule_id for item in findings))
        self.assertEqual(len(self.store.listed_securities('2024-06-04')), 1)

    def test_listed_share_change_of_twenty_percent_sets_corporate_action_pending(self):
        self.save_day('2024-06-03', [
            {'code': '005930', 'close': 70000, 'listed_shares': 1000},
        ])
        self.save_day('2024-06-04', [
            {'code': '005930', 'close': 70000, 'listed_shares': 1200},
        ])

        findings = reconcile_daily_market(self.store, '2024-06-03', '2024-06-04')

        self.assertTrue(any(item.rule_id == 'listed_shares_jump' for item in findings))
        readiness = self.store.readiness('2024-06-03', '2024-06-04')
        self.assertTrue(readiness['corporate_action_pending'])

    def test_close_ratio_outside_half_to_double_sets_corporate_action_pending(self):
        self.save_day('2024-06-03', [{'code': '005930', 'close': 1000}])
        self.save_day('2024-06-04', [{'code': '005930', 'close': 2001}])

        findings = reconcile_daily_market(self.store, '2024-06-03', '2024-06-04')

        self.assertTrue(any(item.rule_id == 'close_ratio_jump' for item in findings))
        self.assertTrue(
            self.store.readiness('2024-06-03', '2024-06-04')['corporate_action_pending']
        )

    def test_reconciliation_never_populates_engine_facing_market_snapshots(self):
        self.save_day('2024-06-03', [{'code': '005930', 'close': 70000}])

        reconcile_daily_market(self.store, '2024-06-03', '2024-06-03')

        with self.store.connect() as db:
            table = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='market_snapshots'"
            ).fetchone()
        self.assertIsNone(table)


if __name__ == '__main__':
    unittest.main()
