import deps; deps.ensure(["matplotlib"])

import argparse
import csv
import glob
import os
import sys
import tempfile
from datetime import datetime

os.environ.setdefault("MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "network-monitor-matplotlib"))
os.makedirs(os.environ["MPLCONFIGDIR"], exist_ok=True)

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates


def parse_timestamp(value):
    value = (value or "").strip()
    if not value:
        raise ValueError("missing timestamp")
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def parse_float(value):
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        return None
    return float(value)


def load_csv(path):
    rows = []
    skipped = 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                row["_ts"] = parse_timestamp(row.get("timestamp"))
                row["download_mbps"] = parse_float(row.get("download_mbps"))
                row["upload_mbps"] = parse_float(row.get("upload_mbps"))
                rows.append(row)
            except Exception:
                skipped += 1
    if skipped:
        print(f"Skipped {skipped} malformed row(s) in {path}", file=sys.stderr)
    return rows


def extract_series(rows, field):
    times, values = [], []
    for r in rows:
        if r.get(field) is not None:
            times.append(r["_ts"])
            values.append(r[field])
    return times, values


def plot(csv_paths, output_path):
    all_rows = []
    for p in csv_paths:
        all_rows.extend(load_csv(p))

    if not all_rows:
        print("No data found in", csv_paths)
        sys.exit(1)

    all_rows.sort(key=lambda r: r["_ts"])

    dl_times, dl_vals = extract_series(all_rows, "download_mbps")
    ul_times, ul_vals = extract_series(all_rows, "upload_mbps")

    fig, axes = plt.subplots(2, 1, figsize=(14, 7), sharex=False)
    title = os.path.splitext(os.path.basename(csv_paths[0]))[0] if len(csv_paths) == 1 else "Network Bandwidth Monitor"
    fig.suptitle(title, fontsize=13)

    for ax, times, vals, label, color in [
        (axes[0], dl_times, dl_vals, "Download (Mbps)", "#2196F3"),
        (axes[1], ul_times, ul_vals, "Upload (Mbps)",   "#4CAF50"),
    ]:
        if times:
            ax.plot(times, vals, marker="o", markersize=3, linewidth=1, color=color)
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
        else:
            ax.text(0.5, 0.5, "No data", transform=ax.transAxes, ha="center")
        ax.set_ylabel(label)
        ax.set_xlabel("Time")
        ax.grid(True, alpha=0.3)
        if vals:
            avg = sum(vals) / len(vals)
            ax.axhline(avg, linestyle="--", linewidth=0.8, color=color, alpha=0.6,
                       label=f"avg {avg:.1f}")
            ax.legend(fontsize=8)

    fig.autofmt_xdate(rotation=30)
    plt.tight_layout()
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    plt.savefig(output_path, dpi=150)
    plt.close(fig)
    print(f"Saved plot to {output_path}")


def resolve_inputs(inputs, results_dir="results"):
    """Accept file paths or literal filename fragments relative to results_dir."""
    paths = []
    seen = set()
    for i in inputs:
        if os.path.isfile(i):
            matched = [i]
        else:
            matched = sorted(glob.glob(os.path.join(results_dir, f"*{glob.escape(i)}*.csv")))
        if matched:
            for path in matched:
                if path not in seen:
                    paths.append(path)
                    seen.add(path)
        else:
            print(f"No files matched: {i}")
    return paths


def main():
    parser = argparse.ArgumentParser(
        description="Plot bandwidth results",
        epilog="Examples:\n"
               "  python plot.py results/20260513_SoftBank_5G.csv\n"
               "  python plot.py SoftBank_5G          # matches any file in results/ containing that string\n"
               "  python plot.py -o out.png SoftBank  # all SoftBank files merged into one plot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("inputs", nargs="+", help="CSV file path(s) or name fragment to match in results/")
    parser.add_argument("-o", "--output", default=None, help="Output PNG path (default: alongside first CSV)")
    parser.add_argument("-d", "--results-dir", default="results", help="Results folder (default: results)")
    args = parser.parse_args()

    paths = resolve_inputs(args.inputs, args.results_dir)
    if not paths:
        print("No matching CSV files found.")
        sys.exit(1)

    out = args.output or os.path.splitext(paths[0])[0] + ".png"
    plot(paths, out)


if __name__ == "__main__":
    main()
