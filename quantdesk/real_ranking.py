"""Build a current research ranking from locally saved market and DART data."""
import math
import pandas as pd
from quantdesk.core import score_stocks
from quantdesk.financials import summarize_statement


def candidate_universe(listing, limit=100):
    required = {'Code', 'Name', 'Market', 'Marcap', 'Amount'}
    if not required.issubset(listing.columns):
        return pd.DataFrame(columns=list(required))
    data = listing.copy()
    data['Code'] = data.Code.astype(str).str.zfill(6)
    data['Marcap'] = pd.to_numeric(data.Marcap, errors='coerce')
    data['Amount'] = pd.to_numeric(data.Amount, errors='coerce')
    excluded_names = r'우$|우B$|스팩|리츠|REIT'
    keep = (data.Market.isin(['KOSPI', 'KOSDAQ']) & data.Code.str.fullmatch(r'\d{6}') &
            data.Marcap.gt(0) & data.Amount.gt(0) & ~data.Name.astype(str).str.contains(excluded_names, case=False, regex=True))
    return data.loc[keep].sort_values(['Marcap', 'Code'], ascending=[False, True]).head(limit).reset_index(drop=True)


def price_factors(prices, as_of):
    if not {'Date', 'Close'}.issubset(prices.columns):
        raise ValueError('일별 종가 없음')
    data = prices[['Date', 'Close']].copy()
    data.Date = pd.to_datetime(data.Date, errors='coerce')
    data.Close = pd.to_numeric(data.Close, errors='coerce')
    data = data.dropna().sort_values('Date').drop_duplicates('Date', keep='last')
    data = data[(data.Date <= pd.Timestamp(as_of)) & data.Close.gt(0)].tail(252)
    if len(data) < 252:
        raise ValueError('252거래일 주가 부족')
    returns = data.Close.pct_change().dropna()
    if len(returns) < 251 or not returns.map(math.isfinite).all():
        raise ValueError('일별 수익률 계산 불가')
    return dict(price=float(data.Close.iloc[-1]), momentum=float((data.Close.iloc[-1] / data.Close.iloc[0] - 1) * 100),
                volatility=float(returns.std(ddof=1) * math.sqrt(252) * 100), price_date=data.Date.iloc[-1].date().isoformat())


def select_statement(statements, code, as_of):
    date = pd.Timestamp(as_of).date().isoformat()
    records = [entry for entry in statements if entry['stock'] == code and entry['filed_at'] <= date]
    if not records:
        return None
    return sorted(records, key=lambda entry: (entry['year'], entry['basis'] == 'CFS', entry['filed_at'], entry['receipt']), reverse=True)[0]


def build_ranking(listing, prices_by_code, statements, companies, as_of, weights=None):
    candidates = candidate_universe(listing)
    usable, exclusions = [], []
    for row in candidates.itertuples(index=False):
        code = row.Code
        try:
            price = price_factors(prices_by_code.get(code, pd.DataFrame()), as_of)
            statement = select_statement(statements, code, as_of)
            if statement is None:
                raise ValueError('공시일이 지난 재무제표 없음')
            financial = summarize_statement(statement, companies, listing)
            if financial['기업 대조'] != '코드·기업명 일치':
                raise ValueError(financial['기업 대조'])
            if financial['ROE (%)'] is None or financial['연간 이익 / 시가총액 (%)'] is None:
                raise ValueError(financial['계정 검증'] or financial['가치 지표 상태'])
            usable.append(dict(code=code, name=row.Name, sector=getattr(row, 'Sector', ''), price=price['price'],
                momentum=price['momentum'], value=financial['연간 이익 / 시가총액 (%)'], quality=financial['ROE (%)'],
                volatility=price['volatility'], market_cap=row.Marcap, turnover=row.Amount, eligible=True,
                price_date=price['price_date'], filing_date=statement['filed_at'], statement_basis=statement['basis']))
        except ValueError as exc:
            exclusions.append(dict(Code=code, Name=row.Name, 제외_사유=str(exc)))
    if not usable:
        columns = ['rank', 'code', 'name', 'sector', 'score', 'price', 'momentum', 'value', 'quality', 'volatility',
                   'market_cap', 'turnover', 'price_date', 'filing_date', 'statement_basis']
        return pd.DataFrame(columns=columns), pd.DataFrame(exclusions).rename(columns={'제외_사유':'제외 사유'})
    ranked = score_stocks(pd.DataFrame(usable), weights or dict(momentum=45, value=20, quality=20, volatility=15))
    return ranked, pd.DataFrame(exclusions).rename(columns={'제외_사유':'제외 사유'})
