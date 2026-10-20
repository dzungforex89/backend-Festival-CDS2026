"""Offline replay/export. Labels are never inputs to inference."""

import argparse, csv, json
from pathlib import Path
from collections import Counter
from .engine import evaluate
from .db import ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="output/submission_output.csv")
    args = parser.parse_args()
    data = {}
    for name in ("contracts", "milestones", "events", "parties", "payment_rules"):
        with (ROOT / "data" / "01_data" / f"{name}.csv").open(
            encoding="utf-8-sig", newline=""
        ) as f:
            data[name] = list(csv.DictReader(f))
    contracts = {r["contract_id"]: r for r in data["contracts"]}
    milestones = {r["milestone_id"]: r for r in data["milestones"]}
    parties = {r["party_id"]: r for r in data["parties"]}
    with (
        ROOT / "data" / "04_output_templates" / "submission_output_template.csv"
    ).open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        rows = list(reader)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    results = []
    with output.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            m = milestones[row["milestone_id"]]
            c = contracts[row["contract_id"]]
            assert m["contract_id"] == row["contract_id"]
            result = evaluate(c, m, data["events"], parties, data["payment_rules"])
            results.append(
                dict(
                    result, contract_id=c["contract_id"], milestone_id=m["milestone_id"]
                )
            )
            row.update({k: v for k, v in result.items() if k in fields})
            writer.writerow(row)
    output.with_suffix(".audit.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report = {
        "rows": len(results),
        "decisions": dict(Counter(r["predicted_decision"] for r in results)),
        "processing_time_ms_total": round(
            sum(r["processing_time_ms"] for r in results), 3
        ),
    }
    output.with_suffix(".report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report))


if __name__ == "__main__":
    main()
