"""Isolate provider calls so an unresponsive upstream can be terminated."""
import contextlib
import sys
import FinanceDataReader as fdr


def main():
    with contextlib.redirect_stdout(sys.stderr):
        if sys.argv[1] == 'listing':
            frame = fdr.StockListing('KRX')
        else:
            frame = fdr.DataReader('NAVER:' + sys.argv[2], sys.argv[3], sys.argv[4]).reset_index()
    print(frame.to_json(orient='table', date_format='iso', force_ascii=True))


if __name__ == '__main__':
    main()
