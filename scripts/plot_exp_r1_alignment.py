"""Post-hoc inspectable DTW alignments; does not change any prediction or score."""

import argparse
import json
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from polar_activity.experiments.matcher import compare_representations
from polar_activity.experiments.runner import digest, load_sensor, save
from polar_activity.experiments.signal import represent
from polar_activity.recognition import motion_blocks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("--group", default="r09")
    args = parser.parse_args()
    manifest = json.loads((args.run / "manifest.json").read_text())
    entry = next(r for r in manifest["data"]["records"] if r["group"] == args.group)
    blocks = motion_blocks(load_sensor(Path(entry["path"])))
    annotations = json.loads(Path(manifest["config"]["annotations"]).read_text())
    refs = [r for r in annotations["reference_sets"] if r["group"] == args.group]
    records = []
    for version in manifest["config"]["representations"]:
        folder = args.run / args.group / version
        model = json.loads((folder / "model.json").read_text())
        pred = json.loads((folder / "predictions.json").read_text())
        for ref in refs:
            options = [
                c
                for c in pred["candidates"]
                if c["best"]
                and ref["start_time_s"] <= c["start_time_s"] < c["end_time_s"] <= ref["end_time_s"]
            ]
            if not options:
                continue
            candidate = min(options, key=lambda c: c["best"]["distance"])
            template = next(
                t for t in model["templates"] if t["id"] == candidate["best"]["template_id"]
            )
            t, acc, gyro = blocks[candidate["block_id"]]
            m = (t >= candidate["start_time_s"]) & (t <= candidate["end_time_s"])
            rep = represent(acc[m], gyro[m], model["scales"], version, model["settings"]["points"])
            match = compare_representations(
                rep, template, version, model["settings"]["band_radius"], True
            )
            key = "fallback" if match["used_invariant_fallback"] else "values"
            x = np.asarray(rep[key]) * np.tile(match["proper_sign"], 2)
            y = np.asarray(template[key])
            pairs = np.asarray(match["path"])
            fig, axes = plt.subplots(1, 3, figsize=(13, 3.5))
            for ax, channel, name in zip(
                axes[:2], (0, 3), ("ACC channel 1", "Gyro channel 1"), strict=True
            ):
                ax.plot(x[pairs[:, 0], channel], label="Candidate")
                ax.plot(y[pairs[:, 1], channel], label="Training template")
                ax.set(title=name, xlabel="Aligned path pair", ylabel="Scaled value")
                ax.legend(fontsize=8)
            axes[2].plot(pairs[:, 0], pairs[:, 1])
            axes[2].plot([0, 63], [0, 63], linestyle="--", color="gray")
            axes[2].set(
                title=f"Shared path; RMS {match['distance']:.3f}",
                xlabel="Candidate point",
                ylabel="Template point",
                aspect="equal",
            )
            fig.suptitle(
                f"{ref['id']} — closest candidate in supplied interval (post-hoc): "
                f"{candidate['best']['activity']} / {candidate['activity']}"
            )
            fig.tight_layout()
            path = folder / f"{ref['id']}-alignment.png"
            fig.savefig(path, dpi=130)
            plt.close(fig)
            records.append(
                {
                    "plot": str(path),
                    "candidate_id": candidate["candidate_id"],
                    "template_id": template["id"],
                    "match": match,
                    "selection": "Post-hoc lowest distance inside supplied approximate interval",
                }
            )
    save(
        args.run / args.group / "alignment-plots.json",
        {"script_sha256": digest(__file__), "plots": records, "predictions_modified": False},
    )


if __name__ == "__main__":
    main()
