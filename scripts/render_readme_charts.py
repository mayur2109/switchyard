"""Rebuild README SVG charts from a saved smoke report. Standard library only."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "docs" / "assets"


def chart(filename, title, subtitle, rows, maximum, footer):
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="960" height="420" '
        'viewBox="0 0 960 420" role="img">',
        f"<title>{title}</title>",
        '<rect width="960" height="420" rx="20" fill="#101827"/>',
        '<g font-family="Arial, sans-serif" fill="#f1f5f9">',
        f'<text x="36" y="50" font-size="26" font-weight="700">{title}</text>',
        f'<text x="36" y="82" font-size="16" fill="#b9c6d8">{subtitle}</text>',
    ]
    for index, (label, value, color, annotation) in enumerate(rows):
        y = 116 + index * 64
        width = value / maximum * 480
        parts.extend(
            [
                f'<text x="36" y="{y + 23}" font-size="16">{label}</text>',
                f'<rect x="265" y="{y}" width="{width:.2f}" height="34" rx="5" fill="{color}"/>',
                f'<text x="{280 + width:.2f}" y="{y + 23}" font-size="16">{annotation}</text>',
            ]
        )
    parts.extend(
        [
            f'<text x="36" y="365" font-size="15" fill="#b9c6d8">{footer[0]}</text>',
            f'<text x="36" y="391" font-size="15" fill="#b9c6d8">{footer[1]}</text>',
            "</g></svg>",
        ]
    )
    (DEST / filename).write_text("\n".join(parts) + "\n")


def main():
    report = json.loads((ROOT / "docs" / "evidence" / "smoke-report.json").read_text())
    before = sum(row["input_chars"] for row in report["rows"])
    after = sum(row["selected_chars"] for row in report["rows"])
    reduction = (before - after) / before * 100
    DEST.mkdir(parents=True, exist_ok=True)
    chart(
        "smoke-comparison.svg",
        "A small, measured selection example",
        "Four synthetic candidates / two tasks / saved local smoke run",
        [
            ("Without selection", before, "#94a3b8", f"{before} chars"),
            ("Recommended payload", after, "#34d399", f"{after} chars"),
        ],
        before,
        [
            f"{reduction:.1f}% less candidate text if applied. "
            "Relevant items retained: 2/2. Mandatory: 1/1.",
            "Advisory result. Not a production benchmark or measured provider-token saving.",
        ],
    )
    chart(
        "potential-savings.svg",
        "What context reduction could mean",
        "Illustrative only: 10,000 input tokens = 2,000 fixed + 8,000 candidate tokens",
        [
            ("No filtering", 10000, "#94a3b8", "10,000"),
            ("25% candidates removed", 8000, "#93c5fd", "8,000 / -20%"),
            ("50% candidates removed", 6000, "#60a5fa", "6,000 / -40%"),
            ("75% candidates removed", 4000, "#34d399", "4,000 / -60%"),
        ],
        10000,
        [
            "Assumes selection happens before the provider call, with no added prompt overhead.",
            "Excludes output tokens, cache pricing, local compute, retries, and quality effects.",
        ],
    )
    transcript = ROOT / "docs" / "evidence" / "transcript-report.json"
    if transcript.exists():
        results = json.loads(transcript.read_text())["replay"]
        rows = []
        for source, values in results.items():
            rows.extend(
                [
                    (
                        source.title() + " original",
                        values["input_tokens"],
                        "#94a3b8",
                        f"{values['input_tokens']:,}",
                    ),
                    (
                        source.title() + " recommended",
                        values["selected_tokens"],
                        "#34d399",
                        f"{values['selected_tokens']:,}",
                    ),
                ]
            )
        uncertain = sum(v["uncertain_omitted"] for v in results.values())
        chart(
            "transcript-replay.svg",
            "Real transcripts: offline selection replay",
            "Reference tokens: cl100k_base / 50% byte budget / original tool text only",
            rows,
            max(row[1] for row in rows),
            [
                f"Uncertain chunks recommended for omission: {uncertain}. "
                "Relevant retention is unmeasured.",
                "Reduction is not billing savings. Unlabeled replay cannot enable filtering.",
            ],
        )


if __name__ == "__main__":
    main()
