import random
import pandas as pd

SAMPLE_DATE = '2026-09-11'


def sample_stocks():
    rng = random.Random(20260914)
    sectors = ['반도체', '자동차', '금융', '헬스케어', '산업재', '소비재', '통신', '에너지']
    rows = []
    for i in range(80):
        rows.append(dict(code=f'D{i+1:05}', name=f'가상 {sectors[i % 8]} {i+1:02}', sector=sectors[i % 8],
                         price=rng.randrange(20, 900) * 100, momentum=round(rng.uniform(-35, 90), 2),
                         value=round(rng.uniform(1, 18), 2), quality=round(rng.uniform(-5, 30), 2),
                         volatility=round(rng.uniform(12, 65), 2), market_cap=rng.randrange(50, 8000)*1_000_000_000,
                         turnover=rng.randrange(2, 200)*100_000_000, eligible=i % 19 != 0))
    return pd.DataFrame(rows)
