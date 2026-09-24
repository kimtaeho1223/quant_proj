from tests.backtest_fixtures import three_week_fixture
from quantdesk.backtest import BacktestConfig, run_backtest
from quantdesk.backtest_ui import render_backtest_result


result = run_backtest(
    three_week_fixture(),
    BacktestConfig('2026-09-01', '2026-09-30'),
)
render_backtest_result(result, run_id=7)
