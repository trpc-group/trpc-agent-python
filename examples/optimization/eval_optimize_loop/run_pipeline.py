"""Run the offline evaluation and optimization loop."""

from pathlib import Path

from pipeline import EvalOptimizePipeline


if __name__ == "__main__":
    EvalOptimizePipeline(Path(__file__).resolve().parent).run()
