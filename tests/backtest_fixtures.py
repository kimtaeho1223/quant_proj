import pandas as pd

from quantdesk.backtest_data import BacktestDataset


class FakeInvestabilityAdapter:
    def __init__(self, codes, eligible_codes=None, execution_states=None,
                 investability_ready=True):
        self.codes = [str(code).zfill(6) for code in codes]
        self.eligible_codes = set(
            str(code).zfill(6) for code in (eligible_codes or self.codes)
        )
        self.execution_states = dict(execution_states or {})
        self.investability_ready = investability_ready
        self.universe_calls = []
        self.execution_calls = []

    def universe(self, as_of_timestamp, policy_id):
        self.universe_calls.append((as_of_timestamp, policy_id))
        return pd.DataFrame([{
            'security_id': f'sec-{code}', 'short_code': code,
            'state': 'eligible', 'reason_codes': (),
            'evidence_ids': (f'evidence-{code}',),
            'policy_fingerprint': 'policy-fingerprint',
            'model_fingerprint': 'model-fingerprint',
        } for code in self.codes if code in self.eligible_codes])

    def execution_status(self, security_id, session_date, decision_timestamp, policy_id):
        code = security_id.removeprefix('sec-')
        self.execution_calls.append(
            (security_id, session_date, decision_timestamp, policy_id)
        )
        state, reasons = self.execution_states.get(
            (session_date, code), ('eligible', ()),
        )
        return {
            'security_id': security_id,
            'as_of_timestamp': decision_timestamp,
            'state': state,
            'reason_codes': tuple(reasons),
            'evidence_ids': (f'execution-{session_date}-{code}',),
            'policy_fingerprint': 'policy-fingerprint',
            'model_fingerprint': 'model-fingerprint',
        }

    def readiness(self, start_date, end_date, policy_id):
        return {
            'investability_ready': self.investability_ready,
            'blocking_reasons': [] if self.investability_ready else ['공식 상태 미확인'],
            'blocking_codes': [] if self.investability_ready else ['missing_daily_coverage'],
            'lifecycle_ready': True,
            'corporate_action_ready': False,
            'official_backtest_ready': False,
            'policy_id': policy_id,
        }


def listing_fixture(count=100):
    return pd.DataFrame([
        {
            'Code': f'{index:06d}',
            'Name': f'기업{index:03d}',
            'Market': 'KOSPI' if index % 2 else 'KOSDAQ',
            'SecurityType': '보통주',
            'Marcap': 1_000_000_000_000 - index * 1_000_000,
            'Amount': 10_000_000_000,
        }
        for index in range(1, count + 1)
    ])


def dataset_fixture(calendar=None, snapshot_date='2026-09-25', filing_date='2026-03-31',
                    candidate_count=100, shuffle=False):
    calendar = calendar or ['2026-09-24', '2026-09-25', '2026-09-28']
    listing = listing_fixture(candidate_count)
    if shuffle:
        listing = listing.sample(frac=1, random_state=7).reset_index(drop=True)
    prices = {
        code: pd.DataFrame([
            {'Date': '2026-09-24', 'Open': 9_900, 'Close': 10_000},
            {'Date': '2026-09-25', 'Open': 10_100, 'Close': 10_200},
            {'Date': '2026-09-28', 'Open': 10_300, 'Close': 10_400},
        ])
        for code in listing.Code
    }
    statements = [{
        'stock': '000001', 'year': 2025, 'basis': 'CFS',
        'receipt': '20260331000001', 'filed_at': filing_date, 'rows': [],
    }]
    companies = {
        row.Code: {'corp_code': f'{int(row.Code):08d}', 'name': row.Name}
        for row in listing.itertuples(index=False)
    }
    indices = {
        'KOSPI': pd.DataFrame([
            {'Date': '2026-09-25', 'Open': 3_000, 'Close': 3_010},
            {'Date': '2026-09-28', 'Open': 3_020, 'Close': 3_030},
        ]),
        'KOSDAQ': pd.DataFrame([
            {'Date': '2026-09-25', 'Open': 900, 'Close': 905},
            {'Date': '2026-09-28', 'Open': 906, 'Close': 910},
        ]),
    }
    return BacktestDataset(
        calendar=calendar,
        snapshots={snapshot_date: listing},
        prices=prices,
        statements=statements,
        companies=companies,
        indices=indices,
        corporate_actions=pd.DataFrame(columns=[
            'code', 'effective_date', 'action_type', 'status',
        ]),
    )


def rank_ready_dataset_fixture(candidate_count=100):
    listing = listing_fixture(candidate_count)
    history = pd.bdate_range(end='2026-09-25', periods=252)
    execution = pd.Timestamp('2026-09-28')
    prices = {}
    statements = []
    companies = {}
    for index, row in enumerate(listing.itertuples(index=False), start=1):
        closes = [10_000 + index + day for day in range(252)]
        prices[row.Code] = pd.DataFrame({
            'Date': history.strftime('%Y-%m-%d'),
            'Open': closes,
            'Close': closes,
        })
        prices[row.Code] = pd.concat([
            prices[row.Code],
            pd.DataFrame([{'Date': execution.strftime('%Y-%m-%d'),
                           'Open': closes[-1] + 10, 'Close': closes[-1] + 20}]),
        ], ignore_index=True)
        corp_code = f'{index:08d}'
        companies[row.Code] = {'corp_code': corp_code, 'name': row.Name}
        statements.append({
            'stock': row.Code, 'year': 2025, 'basis': 'CFS',
            'receipt': f'20260331{index:06d}', 'filed_at': '2026-03-31',
            'rows': [
                {'corp_code': corp_code, 'account_id': 'ifrs-full_ProfitLossAttributableToOwnersOfParent',
                 'sj_div': 'IS', 'thstrm_amount': str(1_000_000_000 + index), 'currency': 'KRW'},
                {'corp_code': corp_code, 'account_id': 'ifrs-full_EquityAttributableToOwnersOfParent',
                 'sj_div': 'BS', 'thstrm_amount': '10000000000',
                 'frmtrm_amount': '9000000000', 'currency': 'KRW'},
            ],
        })
    return BacktestDataset(
        calendar=[*history.strftime('%Y-%m-%d'), execution.strftime('%Y-%m-%d')],
        snapshots={'2026-09-25': listing},
        prices=prices,
        statements=statements,
        companies=companies,
        indices={},
        corporate_actions=pd.DataFrame(columns=[
            'code', 'effective_date', 'action_type', 'status',
        ]),
    )


def three_week_fixture(candidate_count=100, missing_open_date=None,
                       corrected=False, corporate_action_status=None):
    listing = listing_fixture(candidate_count)
    sessions = pd.bdate_range(end='2026-10-02', periods=275)
    signal_dates = ['2026-09-04', '2026-09-11', '2026-09-18', '2026-09-25']
    snapshots = {date: listing.copy() for date in signal_dates}
    prices = {}
    statements = []
    companies = {}
    for index, row in enumerate(listing.itertuples(index=False), start=1):
        closes = [10_000 + index * 2 + day for day in range(len(sessions))]
        frame = pd.DataFrame({
            'Date': sessions.strftime('%Y-%m-%d'),
            'Open': [value - 5 for value in closes],
            'Close': closes,
        })
        if missing_open_date:
            frame = frame[frame.Date != missing_open_date].reset_index(drop=True)
        prices[row.Code] = frame
        corp_code = f'{index:08d}'
        companies[row.Code] = {'corp_code': corp_code, 'name': row.Name}
        rows = [
            {'corp_code': corp_code, 'account_id': 'ifrs-full_ProfitLossAttributableToOwnersOfParent',
             'sj_div': 'IS', 'thstrm_amount': str(1_000_000_000 + index * 1_000_000), 'currency': 'KRW'},
            {'corp_code': corp_code, 'account_id': 'ifrs-full_EquityAttributableToOwnersOfParent',
             'sj_div': 'BS', 'thstrm_amount': '10000000000',
             'frmtrm_amount': '9000000000', 'currency': 'KRW'},
        ]
        statements.append({
            'stock': row.Code, 'year': 2025, 'basis': 'CFS',
            'receipt': f'20260331{index:06d}', 'filed_at': '2026-03-31', 'rows': rows,
        })
        if corrected:
            corrected_rows = [dict(item) for item in rows]
            corrected_rows[0]['thstrm_amount'] = str(1_500_000_000 + index * 1_000_000)
            statements.append({
                'stock': row.Code, 'year': 2025, 'basis': 'CFS',
                'receipt': f'20260910{index:06d}', 'filed_at': '2026-09-10',
                'rows': corrected_rows,
            })
    actions = pd.DataFrame(columns=['code', 'effective_date', 'action_type', 'status'])
    if corporate_action_status:
        actions = pd.DataFrame([{
            'code': '000001', 'effective_date': '2026-09-15',
            'action_type': 'split', 'status': corporate_action_status,
        }])
    indices = {
        symbol: pd.DataFrame({
            'Date': sessions.strftime('%Y-%m-%d'),
            'Open': [base + day for day in range(len(sessions))],
            'Close': [base + day + 1 for day in range(len(sessions))],
        })
        for symbol, base in [('KOSPI', 3_000), ('KOSDAQ', 900)]
    }
    return BacktestDataset(
        calendar=sessions.strftime('%Y-%m-%d').tolist(),
        snapshots=snapshots,
        prices=prices,
        statements=statements,
        companies=companies,
        indices=indices,
        corporate_actions=actions,
    )
