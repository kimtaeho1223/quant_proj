"""Conservative annual-account extraction; no fuzzy issuer matching."""
from decimal import Decimal, InvalidOperation
import math


def amount(value):
    try:
        number = Decimal(str(value).replace(',', '').strip())
        if not number.is_finite():
            raise InvalidOperation
        return number
    except InvalidOperation:
        raise ValueError('금액이 누락되었거나 숫자가 아닙니다.') from None


def account(rows, suffix, sections, fields):
    matches = [r for r in rows if r.get('sj_div') in sections and
               r.get('account_id') in {f'ifrs-full_{suffix}', f'ifrs_{suffix}'}]
    if not matches:
        raise ValueError(f'표준 계정 없음: {suffix}')
    if any(r.get('currency') != 'KRW' for r in matches):
        raise ValueError('원화 이외 통화 또는 통화 누락입니다.')
    values = {tuple(amount(r.get(field)) for field in fields) for r in matches}
    if len(values) != 1:
        raise ValueError(f'같은 계정에 서로 다른 금액이 있습니다: {suffix}')
    return next(iter(values))


def extract_metrics(rows, basis):
    if basis not in ('CFS', 'OFS'):
        raise ValueError('연결·별도 기준을 확인해 주세요.')
    profit_id = 'ProfitLossAttributableToOwnersOfParent' if basis == 'CFS' else 'ProfitLoss'
    equity_id = 'EquityAttributableToOwnersOfParent' if basis == 'CFS' else 'Equity'
    profit, = account(rows, profit_id, {'IS', 'CIS'}, ['thstrm_amount'])
    current, previous = account(rows, equity_id, {'BS'}, ['thstrm_amount', 'frmtrm_amount'])
    if current <= 0 or previous <= 0:
        raise ValueError('당기·전기 자기자본이 양수가 아니어서 ROE를 계산하지 않습니다.')
    return dict(profit=float(profit), equity=float(current), previous_equity=float(previous),
                roe=float(profit / ((current + previous) / 2) * 100),
                accounts=f'{profit_id} / {equity_id}')


def match_company(stock, companies, listing):
    if listing.empty:
        return '종목 목록 갱신 필요'
    found = listing[listing.Code == stock]
    if len(found) != 1:
        return '현재 종목 목록에 없거나 코드가 중복됨'
    company = companies.get(stock)
    if not company:
        return 'DART 기업 목록에 없음'
    normalize = lambda value: ''.join(str(value).split())
    if normalize(company['name']) != normalize(found.iloc[0].Name):
        return '기업명 불일치 · 수동 확인 필요'
    return ''


def summarize_statement(entry, companies, listing):
    stock = entry['stock']
    mismatch = match_company(stock, companies, listing)
    result = {'종목코드':stock, '기업명':companies.get(stock, {}).get('name', stock),
              '사업연도':entry['year'], '기준':'연결' if entry['basis'] == 'CFS' else '별도',
              '공시일':entry['filed_at'], '기업 대조':mismatch or '코드·기업명 일치',
              '순이익 (원)':None, '당기 자기자본 (원)':None, '전기 자기자본 (원)':None,
              'ROE (%)':None, '연간 이익 / 시가총액 (%)':None, '계정 검증':'', '가치 지표 상태':mismatch or '계정 확인 필요'}
    try:
        company = companies.get(stock, {})
        corp_ids = {r.get('corp_code') for r in entry['rows'] if r.get('corp_code')}
        if corp_ids and corp_ids != {company.get('corp_code')}:
            raise ValueError('재무제표 법인번호가 기업 목록과 다릅니다.')
        metrics = extract_metrics(entry['rows'], entry['basis'])
        result.update({'순이익 (원)':metrics['profit'], '당기 자기자본 (원)':metrics['equity'],
                       '전기 자기자본 (원)':metrics['previous_equity'], 'ROE (%)':metrics['roe'],
                       '계정 검증':metrics['accounts']})
        if not mismatch:
            cap = float(listing.loc[listing.Code == stock, 'Marcap'].iloc[0])
            if math.isfinite(cap) and cap > 0:
                result['연간 이익 / 시가총액 (%)'] = metrics['profit'] / cap * 100
                result['가치 지표 상태'] = '참고값 · 목록 스냅샷 기준'
            else:
                result['가치 지표 상태'] = '시가총액 누락 또는 비정상'
    except (ValueError, TypeError, KeyError) as exc:
        result['계정 검증'] = str(exc)
    return result
