# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""LLM-as-judge grader for XDomain cross-domain synthesis benchmark.

Uses a strong model (Opus-4.7) to evaluate candidate answers against rubrics.
"""

from __future__ import annotations
import json
from typing import Any

import boto3


def grade_xdomain_answer(
    problem_id: str,
    problem_text: str,
    candidate_answer: str,
    gold_answer: str,
    rubric: list[str],
    max_score: int,
    model_id: str = "us.anthropic.claude-opus-4-7",
    region: str = "us-east-1",
) -> dict[str, Any]:
    """Grade a candidate answer using LLM-as-judge.

    Args:
        problem_id: Unique problem identifier
        problem_text: The original problem statement
        candidate_answer: The model's answer to evaluate
        gold_answer: Reference answer for comparison
        rubric: List of evaluation criteria (each worth 1 point)
        max_score: Maximum possible score
        model_id: Bedrock model ID for judge
        region: AWS region

    Returns:
        {
            "score": int (0 to max_score),
            "correct": bool (score >= threshold),
            "reasoning": str,
            "rubric_scores": dict[str, int],
            "cost_usd": float,
        }
    """
    # Threshold: ≥60% of max score = correct
    threshold = max_score * 0.6

    # Build judge prompt
    judge_prompt = f"""You are an expert evaluator for a cross-domain synthesis benchmark. Your task is to grade a candidate answer by comparing it against a gold answer and evaluation rubric.

**Problem:**
{problem_text}

**Gold Answer (Reference):**
{gold_answer}

**Candidate Answer (To Grade):**
{candidate_answer}

**Evaluation Rubric:**
Each criterion below is worth 1 point. Award 1 if the candidate substantially addresses the criterion, 0 otherwise.

{chr(10).join(f"{i+1}. {criterion}" for i, criterion in enumerate(rubric))}

**Maximum Score:** {max_score}

**Your Task:**
1. For each rubric criterion, decide if the candidate answer addresses it (1 point) or not (0 points).
2. Provide brief reasoning for each criterion.
3. Sum the points to get the total score.
4. Output your evaluation in this EXACT JSON format:

{{
  "rubric_scores": {{
    "criterion_1": 0 or 1,
    "criterion_2": 0 or 1,
    ...
  }},
  "total_score": <sum of rubric_scores>,
  "reasoning": "<Your detailed reasoning for each criterion>",
  "overall_assessment": "<Brief summary of candidate's strengths and weaknesses>"
}}

Be fair but strict. The candidate must demonstrate clear understanding of the cross-domain concept, not just generic or surface-level responses.
"""

    # Call Bedrock
    client = boto3.client("bedrock-runtime", region_name=region)

    try:
        response = client.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": judge_prompt}]}],
            inferenceConfig={"maxTokens": 2000},  # Opus-4.7: temperature deprecated, defaults to deterministic
        )

        # Extract response
        output_text = response["output"]["message"]["content"][0]["text"]

        # Calculate cost
        input_tokens = response["usage"]["inputTokens"]
        output_tokens = response["usage"]["outputTokens"]
        # Opus-4.7 pricing: $15/MTok input, $75/MTok output
        cost_usd = (input_tokens / 1_000_000 * 15) + (output_tokens / 1_000_000 * 75)

        # Parse JSON from response
        # Try to extract JSON from markdown code blocks if present
        json_text = output_text
        if "```json" in output_text:
            json_text = output_text.split("```json")[1].split("```")[0].strip()
        elif "```" in output_text:
            json_text = output_text.split("```")[1].split("```")[0].strip()

        judge_result = json.loads(json_text)

        score = judge_result.get("total_score", 0)
        is_correct = score >= threshold

        return {
            "score": score,
            "correct": is_correct,
            "reasoning": judge_result.get("reasoning", ""),
            "overall_assessment": judge_result.get("overall_assessment", ""),
            "rubric_scores": judge_result.get("rubric_scores", {}),
            "cost_usd": cost_usd,
            "threshold": threshold,
            "judge_model": model_id,
            "raw_output": output_text,
        }

    except Exception as e:
        # If judge fails, return a safe default (mark as incorrect)
        return {
            "score": 0,
            "correct": False,
            "reasoning": f"Judge evaluation failed: {str(e)}",
            "overall_assessment": "Error",
            "rubric_scores": {},
            "cost_usd": 0.0,
            "threshold": threshold,
            "judge_model": model_id,
            "error": str(e),
        }
