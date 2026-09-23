"""Performance metrics and aligned price-return comparisons."""
import math

import pandas as pd


class MetricError(ValueError):
    pass


def _nav_series(nav):
    if not isinstance(nav, pd.DataFrame) or not {'date', 'equity'}.issubset(nav.columns):
        raise MetricError('NAV 필수 열이 없습니다.')
    data = nav[['date', 'equity']].copy()
    data['date'] = pd.to_datetime(data.date, errors='coerce')
    data['equity'] = pd.to_numeric(data.equity, errors='coerce')
    if len(data) < 2 or data.isna().any().any():
        raise MetricError('NAV 관측값이 2개 이상 필요합니다.')
    if data.date.duplicated().any():
        raise MetricError('NAV 날짜가 중복되었습니다.')
    if (data.equity <= 0).any() or not data.equity.map(math.isfinite).all():
        raise MetricError('NAV 값이 유효하지 않습니다.')
    return data.sort_values('date').set_index('date').equity.astype(float)


def performance_metrics(nav, risk_free_rate=0.0):
    if not math.isfinite(risk_free_rate):
        raise MetricError('무위험수익률이 유효하지 않습니다.')
    series = _nav_series(nav)
    returns = series.pct_change().dropna()
    drawdown = series / series.cummax() - 1
    years = (series.index[-1] - series.index[0]).days / 365.2425
    volatility = returns.std(ddof=1) * math.sqrt(252)
    if years <= 0 or not math.isfinite(volatility) or volatility <= 0:
        raise MetricError('성과 기간 또는 변동성이 충분하지 않습니다.')
    values = {
        'cumulative_return': series.iloc[-1] / series.iloc[0] - 1,
        'cagr': (series.iloc[-1] / series.iloc[0]) ** (1 / years) - 1,
        'max_drawdown': drawdown.min(),
        'annual_volatility': volatility,
        'sharpe': ((returns.mean() * 252) - risk_free_rate) / volatility,
        'risk_free_rate': risk_free_rate,
        'return_type': 'price',
    }
    if any(not math.isfinite(value) for key, value in values.items()
           if key not in {'return_type'}):
        raise MetricError('성과 지표를 유한한 값으로 계산할 수 없습니다.')
    return values


def _return_frame(nav, name):
    series = _nav_series(nav)
    return pd.DataFrame({
        'date': series.index,
        name: series / series.iloc[0] - 1,
    }).reset_index(drop=True)


def _aligned_index(dates, frame, name):
    result = pd.DataFrame({'date': pd.to_datetime(dates)})
    if not isinstance(frame, pd.DataFrame) or frame.empty or not {'Date', 'Close'}.issubset(frame.columns):
        result[name] = float('nan')
        return result
    index = frame[['Date', 'Close']].copy()
    index['date'] = pd.to_datetime(index.Date, errors='coerce')
    index['value'] = pd.to_numeric(index.Close, errors='coerce')
    index = index.dropna(subset=['date', 'value']).sort_values('date')
    merged = pd.merge_asof(result.sort_values('date'), index[['date', 'value']],
                           on='date', direction='backward')
    first = merged.value.dropna()
    merged[name] = merged.value / first.iloc[0] - 1 if not first.empty else float('nan')
    return merged[['date', name]]


def build_comparison(strategy_nav, benchmark_nav, kospi, kosdaq):
    strategy = _return_frame(strategy_nav, 'strategy')
    benchmark = _return_frame(benchmark_nav, 'top100_equal_weight')
    result = strategy.merge(benchmark, on='date', how='left')
    result = result.merge(_aligned_index(result.date, kospi, 'kospi'), on='date', how='left')
    result = result.merge(_aligned_index(result.date, kosdaq, 'kosdaq'), on='date', how='left')
    result['date'] = result.date.dt.strftime('%Y-%m-%d')
    return result[['date', 'strategy', 'top100_equal_weight', 'kospi', 'kosdaq']]
