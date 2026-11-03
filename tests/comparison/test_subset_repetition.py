from apps.api.services.comparison import ComparisonRunner, Config, TaskLoader


def test_subset_selects_only_matching_tasks():
    loader = TaskLoader()
    tasks = loader.load_subset(["stub_data_diagnosis_repair"])
    assert [task.task_id for task in tasks] == ["stub_data_diagnosis_repair"]

    runner = ComparisonRunner(loader)
    results = runner.run([Config.A], task_subset=["stub_data_diagnosis_repair"], repetitions=2, use_doubles=True)
    assert len(results) == 2
    assert {rec.task_id for rec in results} == {"stub_data_diagnosis_repair"}
    assert {rec.reproducibility["repetition_index"] for rec in results} == {0, 1}


def test_unknown_subset_ids_produce_no_trials():
    runner = ComparisonRunner()
    results = runner.run([Config.A, Config.B], task_subset=["does_not_exist"], use_doubles=True)
    assert results == []


def test_trial_count_is_configs_times_tasks_times_repetitions():
    runner = ComparisonRunner()
    results = runner.run(
        [Config.A, Config.B, Config.C],
        task_subset=["stub_data_diagnosis_repair", "stub_factor_implementation"],
        repetitions=2,
        seed=11,
        use_doubles=True,
    )

    assert len(results) == 3 * 2 * 2
    assert all(rec.reproducibility["repetition_count"] == 2 for rec in results)
    assert all(rec.reproducibility["plan_size"] == 12 for rec in results)


def test_same_seed_reproduces_the_interleaved_order():
    def order(seed):
        runner = ComparisonRunner()
        records = runner.run([Config.A, Config.B], repetitions=2, seed=seed, use_doubles=True)
        return [(rec.config.value, rec.task_id) for rec in records], records

    first, records = order(7)
    second, _ = order(7)
    other, _ = order(8)

    assert first == second
    assert first != other
    assert all(rec.reproducibility["interleave_seed"] == 7 for rec in records)


def test_recorded_seed_and_position_reproduce_the_plan():
    runner = ComparisonRunner()
    records = runner.run([Config.A, Config.B], repetitions=2, seed=42, use_doubles=True)

    ordered = sorted(records, key=lambda rec: rec.reproducibility["interleaved_order"])
    assert [rec.reproducibility["interleaved_order"] for rec in ordered] == list(range(len(records)))
    assert [(rec.config.value, rec.task_id) for rec in ordered] == [
        (rec.config.value, rec.task_id) for rec in records
    ]

    replay = ComparisonRunner()
    replayed = replay.run([Config.A, Config.B], repetitions=2, seed=42, use_doubles=True)
    assert [(rec.config.value, rec.task_id) for rec in replayed] == [
        (rec.config.value, rec.task_id) for rec in ordered
    ]


def test_unseeded_interleaving_still_records_a_seed():
    runner = ComparisonRunner()
    records = runner.run([Config.A], repetitions=1, use_doubles=True)

    seed = records[0].reproducibility["interleave_seed"]
    assert isinstance(seed, int)
    assert 0 <= seed < 2**31


def test_no_interleaving_keeps_the_declared_order_and_records_no_seed():
    runner = ComparisonRunner()
    records = runner.run([Config.A, Config.B], repetitions=1, interleave=False, use_doubles=True)

    expected = [(config.value, task.task_id) for config in [Config.A, Config.B] for task in runner.loader.load_subset()]
    assert [(rec.config.value, rec.task_id) for rec in records] == expected
    assert all(rec.reproducibility["interleave_seed"] is None for rec in records)
