# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Benchmark-aligned persona mappings.

This module maps each benchmark to a set of personas whose domain expertise
aligns with the types of questions in that benchmark.

Rationale: Using a generic panel of scientists (mathematician, physicist, etc)
for ALL benchmarks is suboptimal. A detective/forensic-pathologist panel is
better suited to murder mysteries than a fourier-transform-scientist.

Usage:
    from agentstorming_benchmark.aligned_personas import get_aligned_personas

    personas = get_aligned_personas('musr_murder')
    # Returns: ['detective', 'forensic-pathologist', ...]
"""

from __future__ import annotations


ALIGNED_PERSONAS: dict[str, list[str]] = {
    'musr_murder': [
        'detective',
        'forensic-pathologist',
        'criminal-psychologist',
        'defence-lawyer',
        'prosecutor',
    ],
    'musr_object_placements': [
        'spatial-reasoning-expert',
        'theory-of-mind-expert',
        'logician',
        'librarian',
        'household-organiser',
    ],
    'musr_team_allocation': [
        'operations-research-expert',
        'manager',
        'sociologist',
        'economist',
        'psychometrician',
    ],
    'truthfulqa_mc': [
        'epistemologist',
        'sceptic',
        'fact-checker',
        'logician',
        'historian-of-misconceptions',
    ],
    'mmlu_pro': [
        'mathematician',
        'physicist',
        'computer-scientist',
        'chemist',
        'biologist',
        'economist',
        'philosopher',
        'historian',
    ],
    'arc_challenge': [
        'biology-teacher',
        'chemistry-teacher',
        'physics-teacher',
        'earth-science-teacher',
        'science-historian',
    ],
    'gpqa_diamond': [
        'physicist',
        'chemist',
        'biologist',
        'mathematician',
        'computer-scientist',
    ],
    'math500': [
        'mathematician',
        'physicist',
        'computer-scientist',
        'logician',
        'economist',
    ],
}


def get_aligned_personas(benchmark: str, n: int = 5) -> list[str]:
    """Get benchmark-aligned personas.

    Args:
        benchmark: Benchmark name (e.g., 'musr_murder', 'truthfulqa_mc')
        n: Number of personas to return (default: 5)

    Returns:
        List of persona names aligned to the benchmark domain.
        Falls back to MMLU-Pro panel if benchmark not found.
        Returns first n personas from the list.
    """
    personas = ALIGNED_PERSONAS.get(benchmark, ALIGNED_PERSONAS['mmlu_pro'])
    return personas[:n]


def get_aligned_fast_track(benchmark: str) -> list[str]:
    """Get fast-track subset (first 3) of aligned personas."""
    return get_aligned_personas(benchmark, n=3)


def get_aligned_full_panel(benchmark: str) -> list[str]:
    """Get full panel (all 5) of aligned personas."""
    return get_aligned_personas(benchmark, n=5)
