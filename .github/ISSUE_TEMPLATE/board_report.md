---
name: Board report
about: Share a rkdoctor report from a board we have not tested yet
labels: board-report
---

Thanks for contributing evidence — new boards are how support spreads.

- Board / vendor:
- SoC:
- OS / kernel:
- Anything that looks wrong in the report:

Attach `report.json` (and `report.html` if convenient) produced by:

```sh
PYTHONPATH=src python3 -m rkdoctor board --json report.json --html report.html
```

Reports contain hostname, SoC model, OS and kernel versions only — no serial
numbers, MACs or IPs. Edit the hostname in the JSON first if it is sensitive.
