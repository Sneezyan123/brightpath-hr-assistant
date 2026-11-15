from eval.metrics import corrupt_answer, fact_recall, prf, reciprocal_rank, roc_auc


def test_fact_recall_whole_numbers():
    assert fact_recall("You get 25 days.", ["5"]) == 0.0
    assert fact_recall("You get 25 days.", ["25"]) == 1.0
    assert fact_recall("EUR 1,000 per year", ["1000"]) == 1.0
    assert fact_recall("paid pro-rata", ["pro rata"]) == 1.0


def test_prf_and_ranking():
    m = prf([True, True, False, False], [True, False, True, False])
    assert m["precision"] == 0.5 and m["recall"] == 0.5
    assert reciprocal_rank(["a", "b"], ["b"]) == 0.5
    assert roc_auc([3, 4], [1, 2]) == 1.0


def test_corrupt_answer_changes_numbers():
    assert corrupt_answer("You get 25 days.") == "You get 32 days."
