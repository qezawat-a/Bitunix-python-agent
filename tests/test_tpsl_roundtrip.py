from trader.risk.tpsl import PositionTPSL, TPSLMethod


def _make(**overrides) -> PositionTPSL:
    kwargs = dict(
        entry_price=1.2345,
        side="LONG",
        atr_value=0.02,
        method="TRAILING",
        trailing_stop_pct=0.8,
        trailing_trigger_roi_pct=3.0,
        breakeven_threshold_pct=1.5,
        trailing_distance=0.4,
        quote_precision=4,
        sl_price=1.20,
        qty=100,
    )
    kwargs.update(overrides)
    return PositionTPSL(**kwargs)


def test_round_trip_preserves_overlay_settings():
    original = _make()
    restored = PositionTPSL.from_dict(original.to_dict())

    assert restored.trailing_stop_pct_val == original.trailing_stop_pct_val
    assert restored.trailing_trigger_roi == original.trailing_trigger_roi
    assert restored.breakeven_threshold == original.breakeven_threshold
    assert restored.trailing_distance == original.trailing_distance
    assert restored.quote_precision == original.quote_precision
    assert restored._qty_precision == original._qty_precision


def test_round_trip_preserves_method_and_levels():
    original = _make()
    restored = PositionTPSL.from_dict(original.to_dict())

    assert restored.method is TPSLMethod.TRAILING
    assert restored.sl_price == original.sl_price
    assert restored.best_price == original.best_price


def test_round_trip_preserves_partial_ladder_settings():
    original = _make(method="PARTIAL", partial_ratios=[50, 50], tp_price=None)
    restored = PositionTPSL.from_dict(original.to_dict())

    assert restored.partial_ratios == [50, 50]
    assert [lv.price for lv in restored.ladder] == [lv.price for lv in original.ladder]
    assert [lv.qty for lv in restored.ladder] == [lv.qty for lv in original.ladder]


def test_from_dict_defaults_for_legacy_payload():
    # Payloads saved before this fix have no overlay keys; they must still load.
    legacy = {
        "entry_price": 1.2345,
        "side": "LONG",
        "sl_price": 1.20,
        "tp_price": None,
        "method": "POSITION",
    }
    restored = PositionTPSL.from_dict(legacy)

    assert restored.trailing_stop_pct_val == 0.005
    assert restored.trailing_trigger_roi == 0.05
    assert restored.breakeven_threshold == 0.02
    assert restored.quote_precision == 2
