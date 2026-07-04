# -*- coding: utf-8 -*-
"""
전쟁 예측 스크리너 · 로컬 프록시 서버 (Python 표준 라이브러리만 사용)
  - index.html 을 같은 오리진으로 서빙 (CORS 회피)
  - /api/stock : Yahoo Finance 실측 (일별 수익률 + 주식 거래량)
  - /api/poly  : Polymarket 실측 (확률 = prices-history, 거래량 = trades 합산)
실행:  python server.py   →  브라우저에서 http://localhost:8787
"""
import json, ssl, time, datetime, os, re, urllib.request, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8787
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
HERE = os.path.dirname(os.path.abspath(__file__))

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE          # 공개 데이터 GET, 회사망 인증서 이슈 회피

_CACHE = {}
_TTL = 600

def _get(url, headers=None):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json', **(headers or {})})
    with urllib.request.urlopen(req, timeout=30, context=_CTX) as r:
        return json.loads(r.read().decode('utf-8'))

def cached(key, fn):
    now = time.time()
    if key in _CACHE and now - _CACHE[key][0] < _TTL:
        return _CACHE[key][1]
    val = fn(); _CACHE[key] = (now, val); return val

def d2ts(d):  # date -> unix (UTC 자정)
    return int(datetime.datetime(d.year, d.month, d.day, tzinfo=datetime.timezone.utc).timestamp())

def ts2date(t):
    return datetime.datetime.fromtimestamp(int(t), datetime.timezone.utc).date()

# ------------------------------------------------------------------ 주식
DEFENSE = ['LMT', 'RTX', 'NOC', 'GD', 'LHX']
ENERGY  = ['XOM', 'CVX', 'COP', 'LNG', 'OXY']

def yahoo_rows(ticker, start_dt, end_dt):
    p1 = d2ts(start_dt - datetime.timedelta(days=8))   # 첫날 수익률 계산용 baseline 여유
    p2 = d2ts(end_dt + datetime.timedelta(days=2))
    url = ('https://query1.finance.yahoo.com/v8/finance/chart/%s?period1=%d&period2=%d&interval=1d'
           % (ticker, p1, p2))
    d = cached('y:' + url, lambda: _get(url))
    res = d['chart']['result'][0]
    ts = res.get('timestamp') or []
    q = res['indicators']['quote'][0]
    closes, vols = q.get('close', []), q.get('volume', [])
    rows = []
    for i, t in enumerate(ts):
        c = closes[i] if i < len(closes) else None
        v = vols[i] if i < len(vols) else None
        if c is None:
            continue
        rows.append((ts2date(t), c, v if v is not None else 0))
    rows.sort(key=lambda r: r[0])
    return rows

def build_stock(start, days):
    start_dt = datetime.date.fromisoformat(start)
    end_dt = start_dt + datetime.timedelta(days=days)
    per = {}          # ticker -> {date: (retPct, vol)}
    datesets = []
    for tk in DEFENSE + ENERGY:
        rows = yahoo_rows(tk, start_dt, end_dt)
        m = {}
        for i in range(1, len(rows)):
            d0, c0, _ = rows[i - 1]
            d1, c1, v1 = rows[i]
            if start_dt <= d1 < end_dt and c0:
                m[d1.isoformat()] = ((c1 / c0 - 1.0) * 100.0, v1)
        per[tk] = m
        datesets.append(set(m.keys()))
    common = sorted(set.intersection(*datesets)) if datesets else []
    def sect(tickers):
        return [{'ticker': tk,
                 'ret': [round(per[tk][d][0], 4) for d in common],
                 'vol': [int(per[tk][d][1]) for d in common]} for tk in tickers]
    return {'dates': common,
            'defense': sect(DEFENSE), 'energy': sect(ENERGY),
            'meta': {'source': 'Yahoo Finance (실측)',
                     'volLabel': '주식 거래량',
                     'note': '수익률·거래량 모두 야후파이낸스 일봉 실측. 과거 일별 옵션 총거래량은 무료 미제공이라 주식 거래량으로 대체.',
                     'tradingDays': len(common)}}

# ------------------------------------------------------------------ 예측시장
WAR_WORDS   = ['strike', 'attack', 'war', 'invade', 'enter', 'nuke', 'nuclear', 'bomb',
               'conflict', 'military', 'forces', 'missile', 'escalat']
PEACE_WORDS = ['ceasefire', 'cease-fire', 'peace', 'deal', 'truce', 'agreement',
               'diplomat', 'resolution', 'talks', 'negotiat']

def classify(question):
    ql = question.lower()
    if any(w in ql for w in PEACE_WORDS):
        return 'peace'
    if any(w in ql for w in WAR_WORDS):
        return 'war'
    return 'war'

# 한국어 → 폴리마켓(영문) 검색어 매핑 (무API 번역 대체)
KO_EN = {
    '이란': 'Iran', '미국': 'US', '러시아': 'Russia', '우크라이나': 'Ukraine', '우크라': 'Ukraine',
    '중국': 'China', '대만': 'Taiwan', '북한': 'North Korea', '한국': 'Korea', '일본': 'Japan',
    '이스라엘': 'Israel', '팔레스타인': 'Palestine', '가자': 'Gaza', '레바논': 'Lebanon',
    '베네수엘라': 'Venezuela', '시리아': 'Syria', '예멘': 'Yemen', '사우디': 'Saudi', '인도': 'India',
    '전쟁': 'war', '침공': 'invade', '공습': 'strike', '폭격': 'strike', '협정': 'ceasefire',
    '휴전': 'ceasefire', '평화': 'peace', '핵': 'nuclear', '미사일': 'missile', '드론': 'drone',
    '봉쇄': 'blockade', '제재': 'sanctions', '도발': 'provocation', '위협': 'threat',
    '군사': 'military', '분쟁': 'conflict', '협상': 'deal', '정권교체': 'regime change',
    '무역전쟁': 'trade war', '관세': 'tariff', '선거': 'election',
}
def to_en(keyword):
    k = (keyword or '').strip()
    if not k:
        return k
    if k in KO_EN:
        return KO_EN[k]
    if re.search('[가-힣]', k):            # 한글 포함 → 사전 부분매칭
        for ko, en in KO_EN.items():
            if ko in k:
                return en
    return k                                        # 영문/미매핑은 원문 그대로

def gamma_markets(q):
    """공개 전문검색(public-search)으로 임의 키워드의 롱테일 시장까지 + 거래량 상위 보강."""
    def fetch():
        out = {}
        try:
            data = _get('https://gamma-api.polymarket.com/public-search?q=%s&limit_per_type=40&events_status=all'
                        % urllib.parse.quote(q))
            for ev in data.get('events', []):
                for m in ev.get('markets', []):
                    cid = m.get('conditionId')
                    if cid:
                        out[cid] = m
        except Exception:
            pass
        ql = q.lower()
        for closed in ('true', 'false'):             # 초대형 시장 보강
            try:
                for m in _get('https://gamma-api.polymarket.com/markets?limit=500&closed=%s'
                              '&order=volumeNum&ascending=false' % closed):
                    cid = m.get('conditionId')
                    if cid and ql in (m.get('question', '').lower()):
                        out[cid] = m
            except Exception:
                pass
        # 키워드가 실제 제목에 포함된 시장만 (public-search가 이벤트 단위로 딸려오는 무관 시장 제거)
        return [m for m in out.values() if ql in (m.get('question', '').lower())]
    return cached('gm:' + q, fetch)

def poly_series(token, total, dates):
    """전체 일봉(interval=max, startTs/endTs 조합은 400)으로:
    - 확률 = 윈도 날짜에 매핑(+forward fill)
    - 일별 거래량 = 실측 총거래량을 '전 생애 가격변동성'으로 배분 후 윈도 구간만 추출
      (data-api trades는 offset<5000 상한이라 대형시장 전량 집계 불가한 대안). 부분 구간도 비율 정확."""
    url = 'https://clob.polymarket.com/prices-history?market=%s&interval=max&fidelity=1440' % token
    try:
        h = cached('ph:' + url, lambda: _get(url)).get('history', [])
    except Exception:
        h = []
    by = {}
    for pt in h:
        by[ts2date(pt['t']).isoformat()] = pt['p']     # 날짜별 마지막 가격
    keys = sorted(by.keys())
    # 확률 (윈도)
    prob, last = [], None
    for d in keys:
        if d < dates[0]:
            last = by[d]
    for d in dates:
        if d in by:
            last = by[d]
        prob.append(last)
    if prob and prob[0] is None:
        first = next((x for x in prob if x is not None), 0.0)
        prob = [x if x is not None else first for x in prob]
    # 거래량 (전 생애 변동성 배분 → 윈도)
    BASE = 0.012
    volmap = {}
    if keys:
        w = [(abs(by[keys[i]] - by[keys[i - 1]]) if i > 0 else 0.0) + BASE for i in range(len(keys))]
        sw = sum(w) or 1.0
        for i, d in enumerate(keys):
            volmap[d] = total * w[i] / sw
    vol = [round(volmap.get(d, 0.0), 2) for d in dates]
    return prob, vol

def build_poly(keyword, start, days):
    start_dt = datetime.date.fromisoformat(start)
    end_dt = start_dt + datetime.timedelta(days=days)
    dates = [(start_dt + datetime.timedelta(days=i)).isoformat() for i in range(days)]
    q = to_en(keyword)
    cands = gamma_markets(q)
    # 윈도 기간에 활성(겹침)이던 시장 모두
    def overlaps(m):
        try:
            s = datetime.datetime.fromisoformat(m['startDate'].replace('Z', '+00:00')).date()
            e = datetime.datetime.fromisoformat(m['endDate'].replace('Z', '+00:00')).date()
            return s <= end_dt and e >= start_dt
        except Exception:
            return False
    cands = [m for m in cands if overlaps(m)]
    cands.sort(key=lambda m: float(m.get('volumeNum') or 0), reverse=True)
    cands = cands[:5]
    markets, fallback = [], False
    if not cands:                      # 매칭 실패 → 키워드 무시하고 상위 Iran류라도
        fallback = True
    for m in cands:
        try:
            toks = json.loads(m['clobTokenIds'])
        except Exception:
            continue
        direction = classify(m.get('question', ''))
        total = float(m.get('volumeNum') or 0)
        prob, vol = poly_series(toks[0], total, dates)   # Yes 토큰 가격 + 거래량 배분
        warprob = [round((p if direction == 'war' else 1 - p), 4) for p in prob]
        markets.append({'name': m.get('question', ''), 'direction': direction,
                        'warProb': warprob, 'vol': vol, 'totalVolume': total})
    note = '확률=CLOB prices-history 실측. 일별 거래량은 무료 API가 시계열 미제공(trades offset<5000 상한)이라, 실측 총거래량을 실측 가격변동성으로 배분 추정(시장별 총합은 실측과 일치).'
    if q.lower() != (keyword or '').lower().strip():
        note += " · 검색어 '%s'→'%s'" % (keyword, q)
    return {'dates': dates, 'keyword': keyword, 'searchTerm': q, 'markets': markets, 'fallback': fallback,
            'meta': {'source': 'Polymarket (확률 실측 · 거래량 추정)', 'note': note}}

# ------------------------------------------------------------------ HTTP
class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype='application/json; charset=utf-8'):
        b = body if isinstance(body, bytes) else body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(b)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):        # 조용히
        pass

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        g = lambda k, d=None: qs.get(k, [d])[0]
        try:
            if u.path in ('/', '/index.html'):
                p = os.path.join(HERE, 'index.html')
                with open(p, 'rb') as f:
                    return self._send(200, f.read(), 'text/html; charset=utf-8')
            if u.path == '/api/stock':
                data = build_stock(g('start'), int(g('days')))
                return self._send(200, json.dumps(data, ensure_ascii=False))
            if u.path == '/api/poly':
                data = build_poly(g('keyword', ''), g('start'), int(g('days')))
                return self._send(200, json.dumps(data, ensure_ascii=False))
            return self._send(404, json.dumps({'error': 'not found'}))
        except Exception as e:
            return self._send(500, json.dumps({'error': str(e)}, ensure_ascii=False))

if __name__ == '__main__':
    print('전쟁 예측 스크리너 서버 실행 중  →  http://localhost:%d' % PORT)
    print('(종료: Ctrl+C)')
    ThreadingHTTPServer(('127.0.0.1', PORT), H).serve_forever()
