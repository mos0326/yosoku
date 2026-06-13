from yosoku.backtest import score_bucket, summarize


def test_score_bucket():
    assert score_bucket(90) == "80+"
    assert score_bucket(80) == "80+"
    assert score_bucket(70) == "60-79"
    assert score_bucket(40) == "40-59"
    assert score_bucket(0) == "0-39"
    assert score_bucket(-5) == "<0"


def test_summarize_empty():
    r = summarize([])
    assert r.total_signals == 0
    assert r.evaluated == 0
    assert r.avg_return == 0.0


def test_summarize_basic():
    scored = [(85, 0.05), (70, -0.02), (50, 0.01), (88, 0.10)]
    r = summarize(scored)
    assert r.evaluated == 4
    # 平均リターン
    assert abs(r.avg_return - (0.05 - 0.02 + 0.01 + 0.10) / 4) < 1e-9
    # 勝率(>0 が 3/4)
    assert abs(r.hit_rate - 0.75) < 1e-9
    # バケット集計
    assert r.by_bucket["80+"].count == 2
    assert abs(r.by_bucket["80+"].avg_return - (0.05 + 0.10) / 2) < 1e-9
    assert r.by_bucket["80+"].hit_rate == 1.0
    assert r.by_bucket["60-79"].count == 1
    assert r.by_bucket["60-79"].hit_rate == 0.0


def test_render_runs():
    r = summarize([(85, 0.05), (70, -0.02)])
    out = r.render()
    assert "勝率" in out
    assert "80+" in out
