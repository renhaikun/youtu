"""
- [ ] log the tool usage into a seperate table? (w/ toocalling id)
- [x] analysis, on the axis of exp -> this script
"""

import argparse
import json
from collections import defaultdict

import pandas as pd

from utu.config import ConfigLoader, EvalConfig
from utu.eval.data import DBDataManager


def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config_name", type=str, default="v00", help="Configuration name for evaluation.")
    parser.add_argument("--exp_id", type=str, default=None, help="Experiment ID.")
    args = parser.parse_args()

    config = ConfigLoader.load_eval_config(args.config_name)
    if args.exp_id:
        config.exp_id = args.exp_id
    return config


def stat_trajectory(t: str | dict) -> dict:
    """stat tool usage in single trajectory"""
    stat = {
        "turns_total": 0,
        "turns_assistant": 0,
        "num_tool_calls": 0,
        "avg_tool_calls": 0,
        "tool_calls": defaultdict(int),
    }
    trajectory = json.loads(t) if isinstance(t, str) else t
    stat["turns_total"] = len(trajectory)
    for msg in trajectory:
        if msg["role"] == "assistant":
            stat["turns_assistant"] += 1
        if "tool_calls" in msg:
            stat["num_tool_calls"] += len(msg["tool_calls"])
            for tool_call in msg["tool_calls"]:
                assert tool_call["type"] == "function"
                stat["tool_calls"][tool_call["function"]["name"]] += 1
    stat["avg_tool_calls"] = stat["num_tool_calls"] / stat["turns_assistant"]
    return stat


def aggregate_stats(stats_series: pd.Series) -> dict:
    """Aggregate statistics across all trajectories."""
    # Initialize aggregated stats
    agg_stats = {
        "total_trajectories": len(stats_series),
        "total_turns": 0,
        "total_assistant_turns": 0,
        "total_tool_calls": 0,
        "avg_tool_calls_per_trajectory": 0,
        "avg_tool_calls_per_assistant_turn": 0,
        "tool_usage": defaultdict(int),
    }

    # Aggregate stats
    for stat in stats_series:
        agg_stats["total_turns"] += stat["turns_total"]
        agg_stats["total_assistant_turns"] += stat["turns_assistant"]
        agg_stats["total_tool_calls"] += stat["num_tool_calls"]

        # Aggregate tool usage counts
        for tool_name, count in stat["tool_calls"].items():
            agg_stats["tool_usage"][tool_name] += count

    # Calculate averages
    if agg_stats["total_trajectories"] > 0:
        agg_stats["avg_tool_calls_per_trajectory"] = agg_stats["total_tool_calls"] / agg_stats["total_trajectories"]

    if agg_stats["total_assistant_turns"] > 0:
        agg_stats["avg_tool_calls_per_assistant_turn"] = (
            agg_stats["total_tool_calls"] / agg_stats["total_assistant_turns"]
        )

    # Sort tool usage by frequency
    agg_stats["tool_usage"] = dict(sorted(agg_stats["tool_usage"].items(), key=lambda x: x[1], reverse=True))

    return agg_stats


def print_stats_summary(agg_stats: dict):
    """Print a summary of the aggregated statistics."""
    print("\n===== TOOL USAGE ANALYSIS SUMMARY =====\n")
    print(f"Total trajectories analyzed: {agg_stats['total_trajectories']}")
    print(f"Total turns: {agg_stats['total_turns']}")
    print(f"Total assistant turns: {agg_stats['total_assistant_turns']}")
    print(f"Total tool calls: {agg_stats['total_tool_calls']}")
    print(f"Average tool calls per trajectory: {agg_stats['avg_tool_calls_per_trajectory']:.2f}")
    print(f"Average tool calls per assistant turn: {agg_stats['avg_tool_calls_per_assistant_turn']:.2f}")

    print("\nTool usage breakdown:")
    for tool_name, count in agg_stats["tool_usage"].items():
        percentage = (count / agg_stats["total_tool_calls"] * 100) if agg_stats["total_tool_calls"] > 0 else 0
        print(f"  {tool_name}: {count} calls ({percentage:.2f}%)")


def main(config: EvalConfig):
    db = DBDataManager(config)
    samples = db.get_samples()  # get samples from specific exp_id
    df = pd.DataFrame([sample.as_dict() for sample in samples])

    # remove empty trajectory
    series_trajectory = df["trajectory"].dropna()
    stats_series = series_trajectory.apply(stat_trajectory)
    agg_stats = aggregate_stats(stats_series)
    print_stats_summary(agg_stats)


if __name__ == "__main__":
    config = get_args()
    main(config)
