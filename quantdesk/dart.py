"""Annual OpenDART statements with verified filing dates and local snapshots."""
import io
import json
import re
import zipfile
from datetime import datetime
from xml.etree import ElementTree
import requests
from quantdesk.storage import Store


class DartError(ValueError):
    pass


class DartNoDataError(DartError):
    pass


ERRORS = {'010':'등록되지 않은 인증키입니다.', '011':'사용할 수 없는 인증키입니다.',
          '012':'허용되지 않은 IP입니다.', '013':'조회된 자료가 없습니다.',
          '020':'요청 한도를 초과했습니다.', '800':'OpenDART 점검 중입니다.'}


class DartClient:
    def __init__(self, key, session=None):
        if not re.fullmatch(r'[A-Za-z0-9]{40}', key):
            raise DartError('40자리 OpenDART 인증키를 입력해 주세요.')
        self.key = key
        self.session = session or requests.Session()

    def request(self, endpoint, params, binary=False):
        try:
            response = self.session.get('https://opendart.fss.or.kr/api/' + endpoint,
                params={**params, 'crtfc_key': self.key}, timeout=(5, 25), allow_redirects=False)
            response.raise_for_status()
            if response.status_code != 200:
                raise DartError('OpenDART 응답 상태를 확인할 수 없습니다.')
            if binary:
                return response.content
            payload = response.json()
        except (requests.RequestException, ValueError):
            # Request exceptions can contain the authentication key in their URL.
            raise DartError('OpenDART 연결 또는 응답 오류입니다. 네트워크 상태를 확인해 주세요.') from None
        if not isinstance(payload, dict):
            raise DartError('OpenDART 응답 형식이 올바르지 않습니다.')
        if payload.get('status') != '000':
            error = DartNoDataError if payload.get('status') == '013' else DartError
            raise error(ERRORS.get(payload.get('status'), 'OpenDART 요청을 처리할 수 없습니다.'))
        return payload

    def companies(self):
        content = self.request('corpCode.xml', {}, binary=True)
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                info = archive.getinfo('CORPCODE.xml')
                if info.file_size > 100_000_000:
                    raise DartError('기업 목록 파일이 허용 크기를 초과했습니다.')
                root = ElementTree.fromstring(archive.read(info))
            result = {}
            for item in root.findall('list'):
                stock = (item.findtext('stock_code') or '').strip()
                corp = (item.findtext('corp_code') or '').strip()
                if re.fullmatch(r'[0-9A-Z]{6}', stock) and re.fullmatch(r'\d{8}', corp):
                    result[stock] = dict(corp_code=corp, name=item.findtext('corp_name') or stock)
            if not result:
                raise DartError('상장기업 목록이 비어 있습니다.')
            return result
        except (zipfile.BadZipFile, KeyError, ElementTree.ParseError):
            raise DartError('기업 목록을 읽을 수 없습니다. 인증키와 사용 권한을 확인해 주세요.') from None

    def annual(self, corp, year, basis):
        payload = self.request('fnlttSinglAcntAll.json', dict(corp_code=corp, bsns_year=str(year), reprt_code='11011', fs_div=basis))
        rows = payload.get('list', [])
        receipts = {row.get('rcept_no', '') for row in rows}
        if len(receipts) != 1 or not re.fullmatch(r'\d{14}', next(iter(receipts), '')):
            raise DartError('재무제표의 접수번호가 없거나 일치하지 않습니다.')
        # Receipt prefix only narrows the search. The saved date comes from list.json.
        day = next(iter(receipts))[:8]
        filings = []
        page = 1
        while True:
            found = self.request('list.json', dict(corp_code=corp, bgn_de=day, end_de=day,
                last_reprt_at='N', page_count='100', page_no=str(page)))
            filings.extend(found.get('list', []))
            if page >= int(found.get('total_page', 1)):
                break
            page += 1
            if page > 20:
                raise DartError('공시 목록이 너무 많아 접수번호를 확인하지 못했습니다.')
        return validate_statement(rows, filings)


def validate_statement(rows, filings):
    receipts = {row.get('rcept_no') for row in rows}
    if len(receipts) != 1 or not re.fullmatch(r'\d{14}', str(next(iter(receipts), ''))):
        raise DartError('재무제표 접수번호가 일치하지 않습니다.')
    receipt = next(iter(receipts))
    matches = [f for f in filings if f.get('rcept_no') == receipt]
    try:
        receipt_day = matches[0]['rcept_dt'] if len(matches) == 1 else receipt[:8]
        date = datetime.strptime(receipt_day, '%Y%m%d').date().isoformat()
    except (ValueError, KeyError):
        raise DartError('공시 접수일이 올바르지 않습니다.') from None
    return dict(receipt=receipt, filed_at=date, rows=rows)


class DartStore(Store):
    def __init__(self, path):
        super().__init__(path)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS dart_companies (id INTEGER PRIMARY KEY, payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS dart_statements (stock TEXT, year INTEGER, basis TEXT, receipt TEXT, filed_at TEXT, fetched TEXT, payload TEXT, PRIMARY KEY(stock,year,basis,receipt))')
            db.execute('CREATE TABLE IF NOT EXISTS dart_logs (id INTEGER PRIMARY KEY, stock TEXT, year INTEGER, status TEXT, basis TEXT, message TEXT, fetched TEXT)')

    def companies(self):
        with self.connect() as db:
            row = db.execute('SELECT payload FROM dart_companies WHERE id=1').fetchone()
        return json.loads(row[0]) if row else {}

    def save_companies(self, companies):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO dart_companies VALUES(1,?)', (json.dumps(companies, ensure_ascii=False),))

    def save_statement(self, stock, year, basis, data):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO dart_statements VALUES(?,?,?,?,?,?,?)',
                (stock, year, basis, data['receipt'], data['filed_at'], datetime.now().astimezone().isoformat(), json.dumps(data['rows'], ensure_ascii=False)))

    def statements(self):
        with self.connect() as db:
            rows = db.execute('SELECT stock,year,basis,receipt,filed_at,fetched,payload FROM dart_statements ORDER BY fetched DESC').fetchall()
        return [dict(stock=r[0], year=r[1], basis=r[2], receipt=r[3], filed_at=r[4], fetched=r[5], rows=json.loads(r[6])) for r in rows]

    def coverage(self, year):
        with self.connect() as db:
            rows = db.execute('''SELECT stock,
                CASE WHEN SUM(CASE WHEN basis='CFS' THEN 1 ELSE 0 END) > 0 THEN 'CFS' ELSE 'OFS' END
                FROM dart_statements WHERE year=? GROUP BY stock''', (year,)).fetchall()
        return dict(rows)

    def record(self, stock, year, status, basis, message):
        with self.connect() as db:
            db.execute('INSERT INTO dart_logs(stock,year,status,basis,message,fetched) VALUES(?,?,?,?,?,?)',
                (stock, year, status, basis, message, datetime.now().astimezone().isoformat()))

    def latest_results(self):
        with self.connect() as db:
            rows = db.execute('''SELECT stock,year,status,basis,message,fetched FROM dart_logs
                WHERE id IN (SELECT MAX(id) FROM dart_logs GROUP BY stock,year) ORDER BY id DESC''').fetchall()
        return [dict(종목코드=r[0], 사업연도=r[1], 상태=r[2], 기준=r[3] or '', 내용=r[4], 수집시각=r[5]) for r in rows]


def refresh_top_statements(store, listing, companies, year, client, progress=None, force=False):
    from quantdesk.financials import match_company
    from quantdesk.real_ranking import candidate_universe

    candidates = candidate_universe(listing)
    coverage = store.coverage(year)
    results = []
    for index, row in enumerate(candidates.itertuples(index=False)):
        if not force and row.Code in coverage:
            results.append(dict(stock=row.Code, name=row.Name, status='건너뜀',
                                basis=coverage[row.Code], message='저장된 재무제표 있음'))
            if progress:
                progress((index + 1) / len(candidates))
            continue
        basis = ''
        try:
            mismatch = match_company(row.Code, companies, listing)
            if mismatch:
                raise DartError(mismatch)
            company = companies[row.Code]
            basis = 'CFS'
            try:
                data = client.annual(company['corp_code'], year, basis)
            except DartNoDataError:
                basis = 'OFS'
                data = client.annual(company['corp_code'], year, basis)
            store.save_statement(row.Code, year, basis, data)
            status = '성공'
            message = ('연결' if basis == 'CFS' else '별도') + ' 재무제표 저장'
        except DartError as exc:
            status = '실패'
            message = str(exc)
        store.record(row.Code, year, status, basis, message)
        results.append(dict(stock=row.Code, name=row.Name, status=status, basis=basis, message=message))
        if progress:
            progress((index + 1) / len(candidates))
    return results
