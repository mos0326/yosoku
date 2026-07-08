"""設定ロードのテスト(既定値・YAML上書き・型の防御)。"""

from yosoku.config import DEFAULT_RELEVANCE_KEYWORDS, load_config
from yosoku.pipeline import passes_prefilter


def test_defaults_widened_coverage():
    cfg = load_config(None)
    # 開示ラッシュ(15時台)の取りこぼし防止で広めに取る
    assert cfg.tdnet.limit == 150
    # ニュースは既定オン・2フィード
    assert cfg.news.enabled is True
    assert len(cfg.news.feeds) == 2
    assert any("yahoo" in f for f in cfg.news.feeds)


def test_new_catalyst_keywords_hit_prefilter():
    cfg = load_config(None)
    from yosoku.models import RawEvent

    def ev(title):
        return RawEvent(source="tdnet", event_id="tdnet:x", title=title)

    # 拡充したキーワードが実際の開示タイトル形式に引っかかる
    hits = [
        "2026年6月度 月次売上高に関するお知らせ",
        "医薬品の製造販売承認取得に関するお知らせ",
        "特許権取得に関するお知らせ",
        "株式会社◯◯の子会社化に関するお知らせ",
        "資本業務提携に関するお知らせ",
        "ライセンス契約締結のお知らせ",
        "株主優待制度の新設に関するお知らせ",
        "過去最高益更新に関するお知らせ",
    ]
    for title in hits:
        assert passes_prefilter(ev(title), cfg), title
    # 事務的な開示は引き続き弾く
    assert not passes_prefilter(ev("本社移転に関するお知らせ"), cfg)
    assert not passes_prefilter(ev("代表取締役の異動に関するお知らせ"), cfg)


def test_keywords_include_new_catalysts():
    for kw in ("月次", "治験", "特許", "買収", "MBO", "優待", "提携"):
        assert kw in DEFAULT_RELEVANCE_KEYWORDS


def test_yaml_overrides_and_type_coercion(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        """
tdnet:
  limit: 80
analysis:
  min_daily_alerts: 3
  daily_pick_time: 19:00   # 引用符なし → YAMLでは六十進整数になるが文字列化される
""",
        encoding="utf-8",
    )
    cfg = load_config(str(p))
    assert cfg.tdnet.limit == 80
    assert cfg.analysis.min_daily_alerts == 3
    # int 1140 のまま流れて AttributeError で機能停止しない(文字列化される)
    assert isinstance(cfg.analysis.daily_pick_time, str)
