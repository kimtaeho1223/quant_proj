import pandas as pd

from quantdesk.backtest_data import BacktestDataset


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
