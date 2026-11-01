# Contributing

Thanks for helping make Rockchip board diagnostics better. The fastest way to
contribute is not code — it is evidence.

## 1. Report your board (no code needed)

Run the doctor on your board and open a **Board report** issue with the JSON
attached:

```sh
PYTHONPATH=src python3 -m rkdoctor board --json report.json --html report.html
```

Reports from boards we do not own (RK3566/RK3568, RK3576, Orange Pi, Radxa,
Firefly, …) are how support spreads. Anything that reads wrong on your board
(a check that should pass but doesn't, a version we fail to parse) is a bug —
please file it with the report attached.

**Privacy**: reports contain hostname, SoC model, OS and kernel versions only.
No serial numbers, MAC addresses, IPs or file contents are collected. Check the
JSON before posting if your hostname is sensitive.

## 2. Code contributions

- Python standard library only for `board` — no new runtime dependencies.
- Every check keeps its stable ID (see `docs/checks.md`); thresholds change
  only with a real-board justification in the PR description.
- Tests use fixture root trees under `tests/fixtures/`; add a fixture instead
  of mocking the hardware.
- Run the test suite before opening a PR:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests
```

- Never commit proprietary Rockchip binaries, model files, or vendor SDK
  archives. This project detects what is installed; it does not redistribute it.

## 3. Ground rules

- A check that cannot read its source reports `skip` with a reason — never a
  guessed value.
- Fixes for false pass/fail on real boards take priority over new features;
  attach the before/after report if you can.
