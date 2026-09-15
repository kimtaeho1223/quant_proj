"""Isolate provider calls so an unresponsive upstream can be terminated."""
import contextlib
import sys
from datetime import date, timedelta
import pandas as pd
import FinanceDataReader as fdr
from quantdesk.market import market_values_complete


def read_listing(stock_listing, read_cache=pd.read_csv, today=None):
    frame = stock_listing('KRX')
    if market_values_complete(frame):
        return frame
    today = today or date.today()
    for offset in range(1, 8):
        day = today - timedelta(days=offset)
        url = 'https://raw.githubusercontent.com/FinanceData/fdr_krx_data_cache/refs/heads/master/' \
              f'data/listing/krx/{day.isoformat()}.csv'
        try:
            cached = read_cache(url, index_col=0, dtype={'Code': str, 'Dept': str, 'ChangeCode': str, 'MarketId': str})
        except (OSError, ValueError):
            continue
        if market_values_complete(cached):
            return cached.reset_index(drop=True)
    raise ValueError('시가총액과 거래대금이 있는 최근 종목 목록을 찾을 수 없습니다.')


def main():
    with contextlib.redirect_stdout(sys.stderr):
        if sys.argv[1] == 'listing':
            frame = read_listing(fdr.StockListing)
        else:
            frame = fdr.DataReader('NAVER:' + sys.argv[2], sys.argv[3], sys.argv[4]).reset_index()
    print(frame.to_json(orient='table', date_format='iso', force_ascii=True))


if __name__ == '__main__':
    main()
