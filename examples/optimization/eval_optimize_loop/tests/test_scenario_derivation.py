"""scenario 由 eval_id 后缀推导, 不维护映射表."""

from run_pipeline import derive_scenario


def test_derive_scenario_from_suffix():
    assert derive_scenario("val_001_optimizable") == "optimizable_success"
    assert derive_scenario("train_002_ineffective") == "optimization_ineffective"
    assert derive_scenario("val_003_regression") == "optimization_regression"
    assert derive_scenario("train_003_working") == "optimization_regression"
    assert derive_scenario("foo_bar") == "unknown"
